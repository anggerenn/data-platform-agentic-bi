"""
Lightdash dashboard creation via content-as-code YAML + Docker SDK.

Flow:
  1. _plan_charts()     — deterministic: PRD metrics + model columns → chart specs
  2. _generate_yaml()   — build Lightdash content-as-code YAML (savedCharts + dashboards)
  3. _write_yaml()      — write to /dbt/dashboards/<slug>.yml
  4. _trigger_deploy()  — run lightdash-deploy container via Docker SDK
  5. _find_dashboard()  — query Lightdash API to get the new dashboard URL
  6. create_dashboard() — orchestrates all steps
"""
import os
import re
import uuid
from datetime import datetime, timezone
from typing import Optional

import docker
import requests
import yaml

from agents.storyteller import arrange_tiles

_NUM_RE = re.compile(
    r'(count|amount|revenue|total|sum|avg|quantity|units|sold|price|cost|value|rate|pct|percent)',
    re.I,
)
_DATE_RE = re.compile(r'(date|time|day|month|week|year|period|created|updated)', re.I)
_FILLER = {
    'by', 'per', 'and', 'or', 'the', 'a', 'of', 'to', 'in', 'for', 'with',
    'over', 'each', 'all',
}


# ── Helpers ────────────────────────────────────────────────────────────────────

def _slugify(text: str) -> str:
    return re.sub(r'[^a-z0-9]+', '_', text.lower()).strip('_')


def _classify_columns(columns: list[str]) -> dict:
    date_cols = [c for c in columns if _DATE_RE.search(c)]
    num_cols = [c for c in columns if _NUM_RE.search(c) and c not in date_cols]
    cat_cols = [c for c in columns if c not in date_cols and c not in num_cols]
    return {'date': date_cols, 'num': num_cols, 'cat': cat_cols}


def _metric_keywords(metrics: list[str]) -> set:
    words = set()
    for m in metrics:
        words.update(re.findall(r'\w+', m.lower()))
    return words - _FILLER


# ── Chart planning ─────────────────────────────────────────────────────────────

def _plan_charts(model_name: str, columns: list[str], metrics: list[str]) -> list[dict]:
    classified = _classify_columns(columns)
    kw = _metric_keywords(metrics)
    dim = lambda col: f"{model_name}_{col}"      # dimension field ID
    met = lambda col: f"{model_name}_{col}_sum"  # metric field ID (matches schema meta.metrics)

    # Prefer total_revenue > revenue > other numeric columns
    primary = None
    for keyword in ['total_revenue', 'revenue', 'total', 'amount', 'sales']:
        for col in classified['num']:
            if col.lower() == keyword or keyword in col.lower():
                primary = col
                break
        if primary:
            break
    if not primary and classified['num']:
        primary = classified['num'][0]
    if not primary:
        return []

    charts = []

    # Trend over time
    if classified['date'] and kw & {'date', 'daily', 'trend', 'time', 'month', 'growth', 'mom', 'over'}:
        date_col = classified['date'][0]
        charts.append({
            "name": f"{primary.replace('_', ' ').title()} Trend",
            "dimensions": [dim(date_col)],
            "metrics": [met(primary)],
            "sorts": [{"fieldId": dim(date_col), "descending": False}],
            "type": "line",
        })

    # Category breakdowns
    for cat_col in classified['cat']:
        cat_lower = cat_col.lower()
        if any(cat_lower in kw_item or kw_item in cat_lower for kw_item in kw):
            charts.append({
                "name": f"{primary.replace('_', ' ').title()} by {cat_col.replace('_', ' ').title()}",
                "dimensions": [dim(cat_col)],
                "metrics": [met(primary)],
                "sorts": [{"fieldId": met(primary), "descending": True}],
                "type": "bar",
            })

    # Total KPI — avoid "Total Total Revenue" double-word
    label = primary.replace('_', ' ').title()
    kpi_name = label if label.lower().startswith('total') else f"Total {label}"
    charts.append({
        "name": kpi_name,
        "dimensions": [],
        "metrics": [met(primary)],
        "sorts": [],
        "type": "big_number",
    })

    return charts[:6]


# ── YAML generation ────────────────────────────────────────────────────────────

def _chart_config(spec: dict) -> dict:
    chart_type = spec["type"]
    if chart_type == "big_number":
        return {
            "type": "big_number",
            "config": {
                "bigNumber": spec["metrics"][0] if spec["metrics"] else None,
                "bigNumberLabel": spec["name"],
            },
        }
    x_field = spec["dimensions"][0] if spec["dimensions"] else None
    y_fields = spec["metrics"]
    plotly_type = "line" if chart_type == "line" else "bar"
    return {
        "type": "cartesian",
        "config": {
            "layout": {"xField": x_field, "yField": y_fields},
            "eChartsConfig": {
                "series": [{
                    "type": plotly_type,
                    "name": y_fields[0] if y_fields else "",
                    "encode": {
                        "xRef": {"field": x_field},
                        "yRef": {"field": y_fields[0] if y_fields else ""},
                    },
                }]
            },
        },
    }


def _generate_content_files(prd, model_name: str, chart_specs: list[dict], positioned: list[dict], guide=None) -> list[tuple[str, str]]:
    """Return [(filename, yaml_content)] for individual chart files + one dashboard file.

    Format matches what `lightdash upload` expects: one YAML file per object,
    each with `type: chart` or `type: dashboard` at root.
    """
    now = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.000Z')
    _dump = lambda d: yaml.dump(d, default_flow_style=False, allow_unicode=True, sort_keys=False)
    files = []

    # Chart files → charts/ subdir (format matches lightdash download output)
    for spec in chart_specs:
        slug = _slugify(spec['name'])
        dims = spec.get('dimensions', [])
        mets = spec.get('metrics', [])
        chart_doc = {
            'name': spec['name'],
            'description': spec['name'],
            'tableName': model_name,
            'updatedAt': now,
            'metricQuery': {
                'exploreName': model_name,
                'dimensions': dims,
                'metrics': mets,
                'filters': {},
                'sorts': spec.get('sorts', []),
                'limit': 500,
                'metricOverrides': {},
                'dimensionOverrides': {},
                'tableCalculations': [],
                'additionalMetrics': [],
                'customDimensions': [],
            },
            'chartConfig': _chart_config(spec),
            'slug': slug,
            'tableConfig': {'columnOrder': dims + mets},
            'spaceSlug': 'home',
            'version': 1,
        }
        files.append((f'charts/{slug}.yml', _dump(chart_doc)))

    # Two tabs: charts in "Dashboard", guide in "README.md"
    tab_dashboard_uuid = str(uuid.uuid4())
    tab_readme_uuid = str(uuid.uuid4())
    tabs = [
        {'uuid': tab_dashboard_uuid, 'name': 'Dashboard', 'order': 0},
        {'uuid': tab_readme_uuid,    'name': 'README.md', 'order': 1},
    ]

    # Chart tiles — all assigned to the Dashboard tab
    pos_map = {p['name']: p for p in positioned}
    tiles = [
        {
            'x': pos_map[spec['name']]['x'],
            'y': pos_map[spec['name']]['y'],
            'w': pos_map[spec['name']]['w'],
            'h': pos_map[spec['name']]['h'],
            'tabUuid': tab_dashboard_uuid,
            'type': 'saved_chart',
            'properties': {
                'title': '',
                'hideTitle': False,
                'chartSlug': _slugify(spec['name']),
                'chartName': spec['name'],
            },
            'tileSlug': _slugify(spec['name']),
        }
        for spec in chart_specs
        if spec['name'] in pos_map
    ]

    # Markdown guide tile — lives in the README.md tab
    if guide:
        use_cases_md = '\n'.join(f'- {u}' for u in (guide.use_cases or []))
        tips_md = '\n'.join(f'- {t}' for t in (guide.tips or []))
        content = f"## Overview\n{guide.overview}"
        if use_cases_md:
            content += f"\n\n## Questions this answers\n{use_cases_md}"
        if tips_md:
            content += f"\n\n## Tips\n{tips_md}"
        tiles.append({
            'x': 0,
            'y': 0,
            'w': 36,
            'h': 12,
            'tabUuid': tab_readme_uuid,
            'type': 'markdown',
            'properties': {
                'title': 'How to use this dashboard',
                'hideTitle': False,
                'content': content,
                'hideFrame': False,
            },
        })

    dashboard_slug = _slugify(prd.title)
    dashboard_doc = {
        'name': prd.title,
        'description': '',
        'updatedAt': now,
        'tiles': tiles,
        'filters': {'metrics': [], 'dimensions': [], 'tableCalculations': []},
        'tabs': tabs,
        'slug': dashboard_slug,
        'spaceSlug': 'home',
        'version': 1,
    }
    files.append((f'dashboards/{dashboard_slug}.yml', _dump(dashboard_doc)))

    return files


def _write_content_files(dbt_path: str, files: list[tuple[str, str]]) -> list[str]:
    base_dir = os.path.join(dbt_path, 'dashboards')
    paths = []
    for filename, content in files:
        path = os.path.join(base_dir, filename)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w') as f:
            f.write(content)
        paths.append(path)
    return paths


# ── Docker deploy ──────────────────────────────────────────────────────────────

def _get_container_context(client) -> tuple[Optional[str], Optional[str]]:
    """Return (network_name, host_dbt_path) by scanning running containers for a /dbt mount."""
    # Env var override — useful for VPS / Coolify deployments
    host_dbt_path = os.environ.get('HOST_DBT_PATH')

    try:
        for container in client.containers.list():
            mounts = container.attrs.get('Mounts', [])
            networks = list(
                container.attrs.get('NetworkSettings', {}).get('Networks', {}).keys()
            )
            network = networks[0] if networks else 'data-platform_data-network'

            for mount in mounts:
                if mount.get('Destination') == '/dbt':
                    return network, host_dbt_path or mount.get('Source')

        return None, host_dbt_path or None
    except Exception:
        return None, host_dbt_path or None


def _get_deploy_image(client) -> Optional[str]:
    for name in ['data-platform-lightdash-deploy', 'data-platform_lightdash-deploy']:
        try:
            client.images.get(name)
            return name
        except docker.errors.ImageNotFound:
            continue
    return None


def _trigger_deploy(host_dbt_path: str, network: str) -> tuple[bool, str]:
    try:
        client = docker.from_env()
        image = _get_deploy_image(client)
        if not image:
            return False, "lightdash-deploy image not found — run docker-compose build first"

        env = {
            'CLICKHOUSE_HOST': os.environ.get('CLICKHOUSE_HOST', 'clickhouse'),
            'CLICKHOUSE_PORT': os.environ.get('CLICKHOUSE_PORT', '8123'),
            'CLICKHOUSE_USER': 'bi_readonly',
            'CLICKHOUSE_PASSWORD': os.environ.get('CLICKHOUSE_PASSWORD', ''),
            'LIGHTDASH_URL': 'http://lightdash:8080',
            'LIGHTDASH_EMAIL': os.environ.get('LIGHTDASH_EMAIL', ''),
            'LIGHTDASH_PASSWORD': os.environ.get('LIGHTDASH_PASSWORD', ''),
            'LIGHTDASH_API_KEY': os.environ.get('LIGHTDASH_API_KEY', ''),
            'CI': 'true',
        }
        volumes = {
            host_dbt_path: {'bind': '/dbt', 'mode': 'rw'},
            f"{host_dbt_path}/profiles.yml": {'bind': '/root/.dbt/profiles.yml', 'mode': 'ro'},
        }

        logs = client.containers.run(
            image=image,
            environment=env,
            volumes=volumes,
            network=network,
            working_dir='/dbt',
            remove=True,
        )
        output = logs.decode('utf-8') if isinstance(logs, bytes) else str(logs)
        return True, output
    except Exception as e:
        return False, str(e)


# ── Dashboard URL lookup ───────────────────────────────────────────────────────

def _find_dashboard_url(title: str) -> Optional[str]:
    """Query Lightdash API to find the dashboard by name and return its URL."""
    internal = os.environ.get('LIGHTDASH_INTERNAL_URL', 'http://lightdash:8080')
    public = os.environ.get('LIGHTDASH_PUBLIC_URL', 'http://localhost:8080')
    headers = {
        "Authorization": f"ApiKey {os.environ.get('LIGHTDASH_API_KEY', '')}",
        "Content-Type": "application/json",
    }
    try:
        r = requests.get(f"{internal}/api/v1/org/projects", headers=headers, timeout=10)
        projects = r.json().get('results', [])
        if not projects:
            return None
        project_uuid = projects[0]['projectUuid']

        r = requests.get(
            f"{internal}/api/v1/projects/{project_uuid}/dashboards",
            headers=headers,
            timeout=10,
        )
        dashboards = r.json().get('results', [])
        for d in dashboards:
            if d.get('name') == title:
                return f"{public}/projects/{project_uuid}/dashboards/{d['uuid']}/view"
        # fallback: return the most recently created one
        if dashboards:
            return f"{public}/projects/{project_uuid}/dashboards/{dashboards[-1]['uuid']}/view"
    except Exception:
        pass
    return f"{public}/projects"


# ── Public entry point ─────────────────────────────────────────────────────────

def create_dashboard(prd, model_result, guide=None) -> dict:
    """
    Generate Lightdash dashboard YAML, write it to dbt/dashboards/,
    trigger lightdash-deploy via Docker SDK, return dashboard URL.
    """
    chart_specs = _plan_charts(model_result.model_name, model_result.columns, prd.metrics)
    if not chart_specs:
        return {"error": "Could not plan any charts from PRD metrics and model columns"}

    positioned = arrange_tiles(prd, chart_specs)
    content_files = _generate_content_files(prd, model_result.model_name, chart_specs, positioned, guide=guide)
    written_paths = _write_content_files('/dbt', content_files)
    yaml_path = written_paths[-1]  # dashboard file is last

    try:
        client = docker.from_env()
        network, host_dbt_path = _get_container_context(client)

        if not host_dbt_path:
            return {
                "error": "Could not find dbt host path — is the Docker socket mounted?",
                "yaml_written": yaml_path,
            }

        success, logs = _trigger_deploy(
            host_dbt_path,
            network or 'data-platform_data-network',
        )

        if not success:
            return {"error": f"Deploy failed: {logs}", "yaml_written": yaml_path}

        url = _find_dashboard_url(prd.title)
        return {
            "url": url,
            "charts_created": len(chart_specs),
            "yaml_written": yaml_path,
        }

    except Exception as e:
        return {"error": str(e), "yaml_written": yaml_path}
