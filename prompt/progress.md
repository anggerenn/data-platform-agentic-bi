# Project Progress

## Session 18 — Scaffolded Dashboard Metric Mismatch Fix (2026-04-12)

### Context
Churn dashboard created by the dashboard builder showed "unknown field" errors for all charts. After fixing field IDs, charts showed "No data available" despite the underlying table having data.

### Root cause 1 — `lightdash.py` hardcoded `_sum` metric suffix
`_plan_charts()` used `met = lambda col: f"{model_name}_{col}_sum"` for all metric field IDs. But `builder.py`'s `_write_schema_file()` correctly infers metric types from SQL — e.g. `COUNT(DISTINCT customer_id)` → metric key `total_customers_count_distinct`. Chart YAMLs referenced `_sum`, schema defined `_count_distinct` → Lightdash said "unknown field".

### Fix 1 — `_build_metric_map()` in `lightdash.py`
Added helper that reads the model's schema YAML and returns `{column_name: full_lightdash_field_id}`. `_plan_charts()` now uses this map instead of hardcoding `_sum`. Falls back to `_sum` when YAML is missing (backward compatible for `daily_sales`).

### Root cause 2 — Wrong Lightdash metric types for materialised columns
`_infer_metric_type()` in `builder.py` detected SQL aggregation types correctly (e.g. `COUNT(DISTINCT ...)` → `count_distinct`), but after `dbt run` materialises the table, the column is a plain integer. Lightdash applying `count_distinct` on an integer column counts distinct integer values (0,1,2,3,4,5 → 6) instead of summing them (2+3+5+... = 98).

### Fix 2 — `_infer_metric_type()` maps to re-aggregation types
- SQL `COUNT` / `COUNT(DISTINCT)` → Lightdash `sum` (pre-aggregated counts are additive)
- SQL ratio (multiple agg calls) → Lightdash `number` with weighted avg, or `average` fallback
- Added `_DATE_COL_RE` regex for better date column detection (`month_start`, `order_month`, etc.) — previously only matched `_date` suffix

### Root cause 3 — `lightdash upload` skips existing charts
After fixing chart YAML files on disk, `lightdash upload` logged "Total charts skipped: 20" — it doesn't update charts that already exist by slug. Had to delete old charts via Lightdash API, then re-upload.

### Lesson learned
`lightdash upload` is create-only for charts — it won't update existing ones. To fix chart definitions, either delete via API first or use the Lightdash UI.

### Commits
- `fix(lightdash): resolve metric field IDs from schema YAML instead of hardcoding _sum`

---

## Session 17 — Traefik 504 Gateway Timeout Fix (2026-04-12)

### Context
Fresh Coolify deploy (deleted and recreated service). Lightdash returned 504 Gateway Timeout despite all containers being healthy.

### Root cause — Traefik picks wrong Docker network
- nginx was on two networks: `data-network` (from docker-compose.yml) and `szr5me99lqxbqleraza4mdt2` (added by Coolify)
- Traefik (coolify-proxy) was only connected to the Coolify project network (`szr5me99lqxbqleraza4mdt2`)
- Without a `traefik.docker.network` label, Traefik v3 non-deterministically picks which network to route through — when it picked `data-network`, it couldn't reach nginx → 504
- This worked in previous sessions by luck (Traefik happened to pick the right network)

### Fix — Remove custom `data-network` entirely
- Removed `networks: [data-network]` from all 13 services in `docker-compose.yml`
- Removed the `networks: data-network: driver: bridge` block
- Docker Compose auto-creates a default network for all services (works locally)
- On Coolify, all services land on the single Coolify project network — no ambiguity for Traefik
- Confirmed fix: `https://lightdash.baroqafarm.com/` returns 200 after redeploy

### Research
- [Coolify docs](https://coolify.io/docs/troubleshoot/applications/gateway-timeout) confirms: custom networks cause Traefik routing ambiguity
- [Traefik community](https://community.traefik.io/t/app-with-multiple-networks-sometimes-fails-with-gateway-timeout/19167) confirms: known non-deterministic behavior with multi-network containers
- Best practice: either remove extra networks or always set `traefik.docker.network` label

### Commit
- `fix(compose): remove custom data-network to prevent Traefik 504`

---

## Session 16 — Chat Widget Height Fix + VPS Fresh Deploy (2026-04-11)

### Context
Widget rendering broken on `lightdash.baroqafarm.com` — panel appeared as a tiny box. Multiple fixes attempted. Fresh VPS redeploy triggered multiple times due to stale env vars and volume issues.

### Fix 1 — nginx stale DNS after redeploy (`nginx/lightdash.conf`)
- Added `resolver 127.0.0.11 valid=10s` + variable-based `proxy_pass` (`set $vanna` / `set $lightdash`) so nginx re-resolves Docker DNS every 10s without a manual restart.
- Added `listen [::]:80` to fix Alpine wget IPv6 healthcheck failure.

### Fix 2 — Widget panel height (partial — `vanna/static/widget.js`, `style.css`)
- **Root cause attempt 1:** `#vanna-panel iframe` used `flex:1` — iframes have intrinsic sizes that override flex-grow in some browsers. Changed to `position:absolute; top:52px; left:0; right:0; bottom:0`.
- **Root cause attempt 2:** Embedded mode CSS (`body.embedded`) not applying reliably. Switched to inline styles in `index.html` JS block to force `#chat-panel` height.
- **Root cause attempt 3:** Lightdash global CSS overriding `position:fixed` on `#vanna-panel`. Added `!important` to all panel CSS + explicit `height:100vh`.
- **Status:** Widget still renders as tiny box on `lightdash.baroqafarm.com` despite working at `vanna.baroqafarm.com` directly. Needs DevTools inspection on the nginx-proxied page to identify the winning CSS rule.

### VPS ops — Traefik routing delay on fresh deploy
- After fresh Coolify deploy with new UUID (`f14e63...`), `lightdash.baroqafarm.com` returned gateway timeout.
- **Root cause (corrected in session 17):** Was misdiagnosed as Traefik discovery interval. Actual cause was multi-network ambiguity — nginx on both `data-network` and Coolify project network, Traefik picking the wrong one. Fixed by removing `data-network` entirely.

### VPS ops — repeated fresh deploys
- Multiple volume wipe + redeploy cycles due to stale `LIGHTDASH_API_KEY` in Coolify env vars blocking lightdash-deploy.
- Resolution: user manually cleared the key in Coolify UI before each fresh deploy.
- lightdash-deploy confirmed working (ExitCode 0, dashboards uploaded).

---

## Session 15 — Model Selection Refactor + VPS Testing (2026-04-10)

### Context
Live testing session against `vanna.baroqafarm.com` + `lightdash.baroqafarm.com`. Found and fixed several gaps in the model selection logic discovered through real end-to-end dashboard builds.

### Fix 1 — `grain_covers` wrong — checked `meta.grain` not physical columns (`builder.py`)
- **Root cause:** `grain_covers()` checked `model.meta.grain` (which is the primary key definition, e.g. `[order_id]`) instead of physical columns. `stg_orders` has `meta.grain = [order_id]` but `customer_id` is a physical column — so customer-level requirements always failed the grain check. Both models fell to the fallback keyword score where `daily_sales` won due to canonical bonus.
- **Fix:** `grain_covers()` now checks `model['columns']` — if required grain columns exist as physical columns, the model passes.

### Fix 2 — Grain inference missed compound column names (`builder.py`)
- **Root cause:** `_infer_grain_from_prd()` uses `re.findall(r'\w+', text)` which treats `customer_id` as one token. Keyword `'customer'` never matched it. DPM populates `prd.dimensions = ['customer_id', 'city']` — but `customer_id` wasn't being included in `required_grain`.
- **First attempt:** Hardcoded `_DIRECT_GRAIN_COLS` set — rejected as not maintainable.
- **Fix:** `run_data_modeler()` augments keyword-inferred grain by scanning all physical columns across scanned models. If a PRD dimension value matches any physical column, it's included. No hardcoded list — automatically picks up new tables/columns.

### Fix 3 — Source table correction in scaffold (`builder.py`)
- **Root cause:** If `required_grain` didn't include `customer_id`, Vanna would generate SQL from `daily_sales` (no `customer_id`) and fail EXPLAIN 3 times.
- **Fix:** Added `_source_table_for_sql()` — after SQL generation, if SQL references `customer_id` but FROM points to `daily_sales`, regenerates with explicit `stg_orders` instruction.

### Fix 4 — `_wrap_as_dbt_model` double-wrapped already-converted refs (`builder.py`)
- **Root cause:** New `_BARE_REF_RE` regex matched `stg_orders` inside existing `{{ ref('stg_orders') }}` blocks from Vanna's training data → `{{ ref('{{ ref('stg_orders') }}') }}` → dbt compilation error.
- **Fix:** Added negative lookbehind/lookahead for single quotes so already-quoted refs are skipped.

### Fix 5 — `_generate_model_sql` dead code removed (`builder.py`)
- Never called — scaffold always used `vn.generate_sql()`. Removed along with `_AVAILABLE_GRAIN_COLS`.

### Fix 6 — lightdash-deploy image fragile (`lightdash.py`)
- **Root cause:** `LIGHTDASH_DEPLOY_IMAGE` set by Coolify to a commit-specific tag. When vanna and lightdash-deploy redeploy at different times, the tag diverges → 404 on image pull.
- **Fix:** Removed env var override entirely. `_get_deploy_image()` now dynamically finds the most recently built/used lightdash-deploy image (stopped containers first, then image tags sorted by creation time).

### Fix 7 — Vanna not routable via Traefik (`docker-compose.yml`)
- **Root cause:** No `expose` directive on vanna service. Traefik defaulted to port 80 instead of 8084 → "no available server".
- **Fix:** Added `expose: - "8084"` to vanna service.

### Operational finding — nginx stale state after full stack redeploy
- After full Coolify redeploy, nginx holds stale upstream connections. `lightdash.baroqafarm.com` returns 504.
- Fix: `docker restart nginx-<id>` on VPS. Resolves immediately.

### E2E result
- Triggered "Top Customer Revenue" dashboard from CLI
- `required_grain: [city, customer_id]` — schema-driven inference correct
- New model `top_customer_revenue` scaffolded from `stg_orders` with `customer_id` grain
- 3 charts deployed, dashboard live at `lightdash.baroqafarm.com`

---

## Session 14 — VPS Deployment Debugging + E2E Fixes (2026-04-05)

### Context
Full VPS deployment debug session. Deployed all code from sessions 11–13, then diagnosed and fixed a cascade of real production failures via SSH.

### Fix 1 — `profiles.yml` directory corruption (`lightdash.py` + `Dockerfile.vanna`)
- **Root cause:** `_trigger_deploy` in `lightdash.py` had a second bind mount: `f"{host_dbt_path}/profiles.yml": {'bind': '/root/.dbt/profiles.yml', 'mode': 'ro'}`. When Coolify first deploys, the host path `./dbt/profiles.yml` doesn't exist as a FILE — Docker silently creates it as an empty DIRECTORY. This caused `dbt ls` to fail with "Could not find profile named 'default'" on every programmatic deploy. `--ignore-errors` on `lightdash deploy` swallowed it, so the model was never registered as an explore in Lightdash.
- **Symptom:** `Explore "customer_churn_risk" does not exist` when chart tile loaded in Lightdash UI.
- **Fix (lightdash.py):** Removed the `profiles.yml` separate bind mount from `_trigger_deploy`. The baked `profiles.yml` inside the container is copied to `/root/.dbt/profiles.yml` at image build time — no separate mount needed.
- **Fix (Dockerfile.vanna):** Added defensive CMD: `[ -d /dbt/profiles.yml ] && rm -rf /dbt/profiles.yml` — removes any corrupted directory before seeding dbt files from baked copy.
- **Manual VPS repair:** `rm -rf /data/coolify/.../dbt/profiles.yml && docker cp vanna:/dbt-baked/profiles.yml /data/coolify/.../dbt/profiles.yml`

### Fix 2 — `_wrap_as_dbt_model` 3-part qualified table refs (`builder.py`)
- CASE: scaffolded SQL contained `transformed_marts.customer_churn_risk_revenue` (2-part) and CTEs referencing `analytics.transformed_marts.customer_churn_risk_revenue` (3-part). The regex only handled 2-part refs.
- Fixed with: `re.sub(r'(?:\w+\.)?transformed_\w+\.(\w+)', _to_ref, sql)` — optional schema prefix before `transformed_`.

### Fix 3 — `_write_schema_file` CASE column metric generation (`builder.py`)
- **Root cause:** `churned_customer_count` was a `CASE ... THEN 1 ELSE 0 END` expression. `_infer_metric_type()` returns `None` for CASE → classified as string dimension with no `_sum` metric. Chart planner matched `_NUM_RE.search('churned_customer_count')` and assumed `_sum` metric existed → broken field ID in Lightdash.
- **Fix:** In the `elif expr and not is_plain_ref:` branch, when `_NUM_COL_RE.search(col)` is True, emit a `sum` metric entry in `meta`. Dimension type forced to `number`.

### Fix 4 — `bi_readonly` password mismatch in `_trigger_deploy` (`lightdash.py`)
- `_trigger_deploy` was passing `ANALYTICS_DB_PASSWORD` (admin password: `mechange`) as `bi_readonly`'s password. On Coolify these are different env vars.
- Fixed: `os.environ.get('ANALYTICS_DB_READONLY_PASSWORD') or os.environ.get('ANALYTICS_DB_PASSWORD', '')`

### Fix 5 — Admin credentials in `_trigger_deploy` (`lightdash.py`)
- `ANALYTICS_DB_ADMIN_USER` and `ANALYTICS_DB_ADMIN_PASSWORD` were not being passed to the programmatic lightdash-deploy container.
- Added both to the env dict so `dbt run --target scaffold` inside the container has write access.

### Fix 6 — Deploy output logged for diagnostics (`lightdash.py`)
- Added `print(f"[lightdash-deploy] output:\n{output[:2000]}")` after container run so deploy failures are visible in vanna logs without SSH.

### Fix 7 — Traefik gateway timeout (`/data/coolify/proxy/dynamic/vanna-timeouts.yaml`)
- Dashboard build takes 2–3 min. Traefik v3.6.1 had no explicit `responseHeaderTimeout`, which may have been 60s.
- Created `/data/coolify/proxy/dynamic/vanna-timeouts.yaml` on VPS:
  ```yaml
  http:
    serversTransports:
      default:
        forwardingTimeouts:
          responseHeaderTimeout: 0s
          dialTimeout: 30s
          idleConnTimeout: 300s
  ```
- Traefik auto-reloads dynamic config — no restart needed.

### Cleanup
- Removed 5 duplicate churn dashboard YAML files from VPS (`dbt/lightdash/dashboards/` and `dbt/lightdash/prd/`)
- Deleted corresponding Lightdash dashboard objects via Lightdash API
- VPS `lightdash/prd/` files confirmed as source of truth (Prefect sync flow pushes them to git)

### E2E Result (fresh test after all fixes)
- New session, answered all 6 DPM questions including metric definitions (Q5)
- `/dashboard/build` triggered → no gateway timeout
- `profiles.yml` read correctly → `lightdash deploy` succeeded (3 explores, SUCCESS=3)
- `customer_churn_risk` model registered → explore exists
- 5 chart tiles rendered without "unknown field" errors
- Dashboard URL returned in widget ✓

---

## Session 13 — Schema Quality + Chart Fixes (2026-04-04)

### Fixes

**`_write_schema_file` SQL parsing overhaul (`builder.py`)**
- Added `_PLAIN_COL_RE` — CTE-based SQL produces plain `a.col` refs in the final SELECT; these now fall through to column-name heuristics instead of being misclassified as dimensions via `elif expr:`
- Added `_RANK_COL_RE` — `*_rank` columns are always dimensions, never sum metrics even if they contain `revenue` in the name
- Narrowed `_ID_COL_RE` to `_id$` only — `city`, `category`, `customer_type` no longer get spurious `count_distinct` metrics
- Fixed `number` metric `sql:` field — only emitted when `_build_weighted_sql` actually substituted `${...}` refs; otherwise falls back to `average` (prevents broken Lightdash explore from referencing source columns that don't exist in the physical table)

**DPM metric definitions (`planner.py`)**
- Added `metric_definitions: dict[str, str]` to PRD model
- Added Q5 to DPM: asks for definitions of ambiguous terms (`active`, `inactive`, `churn`, `retention`, `leaderboard`, `at risk`, etc.) before proceeding to actions
- Skipped when all metrics are unambiguous
- `_build_model_question` in `builder.py` now injects definitions into vanna's SQL question: `"active" means customer with ≥1 order in last 30 days`

**Lightdash chart fixes (`lightdash.py`)**
- Added `_field_label()` — strips model prefix + metric suffix from field IDs for readable y-axis labels (`customer_retention_risk_monitoring_total_revenue_sum` → `Total Revenue`)
- Added `axes` section to `eChartsConfig` with human-readable left axis name
- Added `_RANK_RE` — `*_rank` / `*_leaderboard_rank` columns excluded from `cat_cols` so they're never used as chart x-axis dimensions

**Training data fixes (`train.py`)**
- Fixed 2 wrong training pairs: `SUM(customer_count)` → `COUNT(DISTINCT customer_id)` from `stg_orders`
- Added 4 new "revenue per customer" training pairs using `SUM(line_total) / NULLIF(COUNT(DISTINCT customer_id), 0)`
- Added data availability doc: dataset covers 2026-03-04 to 2026-04-03 — no February data
- Updated `customer_count` documentation with explicit warning against `SUM` for unique counts

### E2E Result (fresh stack)
```
Q1  explore  COUNT(DISTINCT customer_id) from stg_orders ✓
Q2  explore  customers with >1 order ✓
Q3  explore  city drop — returns 1 row (no Feb comparison) ✓
Q4  explore  SUM(line_total) / NULLIF(COUNT(DISTINCT customer_id), 0) ✓
Q5  semantic correct data availability explanation ✓
Q6  explore  bar chart (was scatter) ✓
DPM 6-turn: Q5 definitions captured, metric_definitions in PRD ✓
Build: 5 charts, new model scaffolded, dashboard URL returned ✓
```

---

## Session 12 — Scaffold Model via SQL Generator (2026-04-03)

### Problem
Churn PRD metrics like "Active Customer Count", "Inactive Customer Count", "Customer Retention Rate" were silently passing `_uncovered_metrics()` with a 67% keyword score because `daily_sales` has `customer_count` — matching "customer" and "count" — even though `daily_sales` can't compute active/inactive breakdowns without `customer_id`.

### Fix 1 — `_HARD_GRAIN_SIGNALS` in `builder.py`
Added a two-stage check to `_uncovered_metrics()`:
- Stage 1 (hard): keywords `active`, `inactive`, `churn`, `retention`, `leaderboard` unconditionally require `customer_id` to be physically present in the model. No keyword score can override this.
- Stage 2: existing keyword-score check (threshold 0.5) as before.
Also added `churn` and `retention` to `_GRAIN_SIGNALS` for grain inference.
14 new tests in `tests/test_builder.py`. `conftest.py` updated with psycopg2 stub for local runs.

### Fix 2 — `scaffold_model()` wired in `app.py`
When `needs_new_model=True`, instead of returning an error message:
1. `scaffold_model()` builds a natural-language question from PRD metrics + dimensions
2. Calls `vn.generate_sql()` to generate the aggregation SQL
3. Validates with `EXPLAIN` via psycopg2 (uses `bi_readonly` — SELECT perms sufficient for EXPLAIN)
4. On validation failure: retries `vn.generate_sql()` with the PostgreSQL error as context (up to 3 attempts)
5. `_wrap_as_dbt_model()` strips LIMIT, replaces schema-qualified refs with `{{ ref(...) }}`, adds config header
6. Writes `.sql` file, runs `dbt run --target scaffold` (uses admin credentials)
7. Returns model dict → pipeline continues to chart generation normally

### Fix 3 — `scaffold` dbt target + admin credentials
- Added `scaffold` target to `dbt/profiles.yml` reading `ANALYTICS_DB_ADMIN_USER` / `ANALYTICS_DB_ADMIN_PASSWORD`
- Added those env vars to vanna service in `docker-compose.yml` (maps to `analytics` write user)
- `scaffold_model()` passes `--target scaffold` to `dbt run`

### Fix 4 — `--ignore-errors` on lightdash deploy
Scaffolded models have no Lightdash dimension metadata → `lightdash deploy` was blocking with "No dimensions available". Added `--ignore-errors` to both `lightdash deploy` calls in `lightdash-deploy-entrypoint.sh`.

### E2E result
```
PRD metrics: ['total revenue per customer', 'order count per customer',
              'average order value', 'customer count by type (active vs inactive)',
              'customer leaderboard by revenue']
→ needs_new_model=True (active/inactive/leaderboard hard fail on daily_sales)
→ vn.generate_sql() → EXPLAIN → dbt run --target scaffold
→ model: transformed_marts.customer_churn_risk_revenue (new)
→ charts_created: 5
→ Dashboard URL returned ✓
→ housekeeper: partial_uncovered (78% overlap with existing churn dashboard)
```

### Files changed
- `vanna/agents/builder.py` — `_HARD_GRAIN_SIGNALS`, `_validate_sql()`, `_build_model_question()`, `_wrap_as_dbt_model()`, `scaffold_model()` rewritten
- `vanna/app.py` — replaced early-return block with `scaffold_model()` call; added `DataModelResult` import
- `dbt/profiles.yml` — added `scaffold` target
- `docker-compose.yml` — added `ANALYTICS_DB_ADMIN_USER` / `ANALYTICS_DB_ADMIN_PASSWORD` to vanna env
- `docker/lightdash-deploy-entrypoint.sh` — added `--ignore-errors` to deploy calls
- `tests/test_builder.py` — 14 new tests for `_uncovered_metrics()`
- `tests/conftest.py` — added psycopg2 stub

---

## Session 11 — Churn Analysis Gap Fixes (2026-03-24)

### Open items from churn analysis testing — 2 of 4 fixed

**Fix: Wrong chart type for leaderboard questions (`designer.py`)**
- Root cause: result with 1 cat col + 2 num cols matched both `bar` and `scatter` structurally; LLM picked `scatter` for "top 10 customers" type questions
- Fix: `_drop_scatter_if_ranking()` — pre-filters `scatter` out of the shortlist when question contains ranking keywords (`top/bottom/most/least/highest/lowest/rank/leaderboard/best/worst`) AND result has ≥1 categorical column
- Deterministic — no LLM call. Correlation questions (no ranking keywords) keep scatter in the shortlist

**Fix: Data Modeler silent failure for uncovered metrics (`builder.py` + `app.py`)**
- Root cause: `run_data_modeler` set `needs_new_model=False` whenever any model was found, regardless of whether that model's columns covered the PRD metrics
- Fix: `_uncovered_metrics(model, metrics)` — per-metric keyword fraction check (threshold 0.5); returns metrics where fewer than half their keywords match model columns/description
  - Correctly flags "customer count by type (active vs inactive)" as uncovered (keywords: customer ✓, count ✓, type ✗, active ✗, inactive ✗ → 2/5 = 0.4 < 0.5)
  - Does NOT flag "total revenue", "average order value" etc. (all keywords match)
- `app.py` now returns distinct message for partial vs zero coverage: names the best-match model and lists the specific uncovered metrics; `uncovered_metrics[]` field added to response
- `churn_test.py` prints `uncovered_metrics` when `needs_new_model=True`

**Fix: Inconsistent PRD output across runs (`planner.py`)**
- Root cause: DeepSeek default temperature ~1.0 + open-ended prompt letting LLM paraphrase user's metric list
- Fix: `temperature=0` in `model_settings` + explicit instruction to copy user's metric/dimension names verbatim from Q4 answer
- Result (3 runs): metrics now 100% consistent — all 5 verbatim ("total revenue per customer", "order count per customer", "average order value", "customer count by type (active vs inactive)", "customer leaderboard by revenue")
- Title still has minor cosmetic variation (DeepSeek multi-turn temperature behaviour) — does not affect model selection, housekeeper, or chart generation
- Commit: `fix(p3): stabilise DPM PRD output — temperature=0 + verbatim metrics`

**Remaining open items (session 11)**
- Mixed deployment (Coolify first-boot) — not yet tested end-to-end on VPS

**Commit:** `fix(p2): leaderboard chart type + data modeler metric coverage check`

---

## Session 10 — E2E Local Test + DPM Bug Fix (2026-03-24)

### E2E smoke test — full stack verified
- Rebuilt vanna container from latest code (was 17 days stale, pre-fix)
- Ran all 39 unit tests (test_app_utils, test_housekeeper, test_routes) inside container: **39/39 pass**
  - Note: `test_vec.py` skipped — BM25 was reverted to ChromaDB; `vec.py` no longer exists
- Ran full smoke test (`vanna/smoke_test.py`): **7/7 pass**
  - Chat: explore × 3, semantic × 2, clarify × 1
  - Dashboard: DPM 5-turn clarification → PRD → 4 charts → YAML written → URL returned

### Bug found and fixed: DPM returns `status=complete` with `prd=null`
- **Root cause:** `DPMResponse.prd` is `Optional[PRD] = None`, so pydantic accepts `null` even when `status=complete`. LLM occasionally omits the PRD object but marks itself done.
- **Symptom:** `/dashboard/build` returned HTTP 400 "No completed PRD in session" — the session's `prd` key was `None`
- **Fix:** Added `model_validator(mode='after')` to `DPMResponse` in `vanna/agents/planner.py` that raises `ValueError` when `status=complete` and `prd is None`. pydantic-ai catches the validation failure and retries the LLM call with the error as feedback — guarantees a populated PRD before the build proceeds.
- Committed: `fix(planner): enforce PRD presence when DPM status is complete`

---

## Session 9 — P2 Fixes (2026-03-24)

### Fix: `meta.grain` declared on all dbt models
- `dbt/models/marts/schema.yml`: added `grain: [order_date, category, city]` and `relationships` (→ stg_orders) to `daily_sales` meta
- `dbt/models/staging/schema.yml`: added `grain: [order_id]` to `stg_orders` meta
- `dbt/validate_schema.py`: canonical models now fail validation if `meta.grain` is missing, with a clear error message
- `vanna/agents/builder.py`:
  - `_scan_models` now extracts `grain` from each model's meta
  - `_CUSTOMER_GRAIN` set and `_needs_customer_grain()` removed
  - `find_best_model` rewritten: takes explicit `dimensions` + `metrics` params; checks grain-superset coverage before falling back to keyword scoring — a PRD with `dimensions: ['customer_id']` correctly routes to `stg_orders` because `daily_sales.grain` doesn't contain `customer_id`
  - `run_data_modeler` passes `prd.dimensions` and `prd.metrics` separately

### Fix: Flask route tests (`tests/test_routes.py`)
- 17 tests covering `/chat/stream`, `/dashboard/build`, `/export`, `/feedback`
- Test isolation: only stubs `dotenv` (not installed locally) and `agents.lightdash` (Python 3.10+ union syntax incompatible with local 3.9) — real agent modules load naturally so `test_housekeeper.py` is unaffected
- 15 pass locally; 2 pandas CSV tests skip (will pass in Docker where pandas is installed)

### Fix: Housekeeper API call batching (`vanna/agents/housekeeper.py`)
- Added `_chart_meta_cache: dict[str, set]` — module-level cache, survives across `check()` calls in the same process; cache hits avoid repeat HTTP calls for charts appearing in multiple dashboards
- Added `_fetch_chart_keywords(chart_uuid, internal, headers)` — cache-aware single-chart fetcher
- Restructured `_fetch_api_fingerprints()` into 4 phases:
  1. Fetch all dashboard tile lists sequentially (1 call per dashboard — unavoidable)
  2. Collect unique chart UUIDs across all dashboards
  3. Fetch all uncached chart UUIDs in parallel via `ThreadPoolExecutor(max_workers=8)`
  4. Build fingerprints from cache — zero additional HTTP calls
- Reduces worst-case calls from O(dashboards × charts) sequential to O(dashboards) + O(unique_charts) parallel

---

## Session 8 — P0 Bug Fixes (2026-03-24)

### Fix: `asyncio.run()` removed from housekeeper (`housekeeper.py`)
- `_llm_disambiguate` changed from `async def` + `await _agent.run()` to plain `def` + `_agent.run_sync()`
- `asyncio.run()` call in `check()` removed — no event loop created, no deadlock risk
- Unused `import asyncio` removed

### Fix: Docker socket failure surfaced (`lightdash.py`, `app.py`)
- `update_readme_tile` return type changed from `bool` to `tuple[bool, Optional[str]]`
- Inner `except Exception: pass` on Docker deploy → `except docker.errors.DockerException as e` — returns `(True, "YAML updated but deploy failed: ...")` so YAML write success is preserved but deploy failure is visible
- `app.py` caller updated to unpack tuple and store `readme_deploy_error` in API response when set

### Fix: hardcoded `localhost` defaults removed
- `vn.py`: `ANALYTICS_DB_HOST` now requires explicit env var (no default) — VPS host is `analytics-db`, not `localhost`
- `app.py`: `LIGHTDASH_PUBLIC_URL` now requires explicit env var
- `housekeeper.py`: `LIGHTDASH_PUBLIC_URL` now requires explicit env var
- `LIGHTDASH_INTERNAL_URL` default (`http://lightdash:8080`) left intact — correct for Docker network

### Fix: missing env vars in `.env.example`
- Added `GEMINI_API_KEY`, `LIGHTDASH_INTERNAL_URL`, `HOST_DBT_PATH`, `DOCKER_NETWORK_NAME` with comments explaining local vs VPS values
- All four were referenced in code but absent from the example — a VPS deployer would have no hint to set them

### Fix: `needs_new_model` stub (`app.py`)
- Was: `return jsonify({"needs_new_model": True, "error": "No existing model covers these metrics."})` — dead end for the user
- Now: calls `vn.generate_sql()` using the PRD objective + metrics as the question, returns `suggested_sql` with a clear message instructing the user to add it as a new dbt model and retry
- Gracefully falls back to `suggested_sql: null` if SQL generation fails

### Verified done: `merge_guides()` already correct (`instructor.py`)
- `_merge()` already combines both existing PRD + new PRD in the prompt before calling LLM
- `update_readme_tile()` reads existing YAML and overwrites the markdown tile correctly
- Gap note from Session 7 was stale — fix was applied in Session 6; tasklist updated

### Fix: `answer_semantic` returns question unchanged (`router.py`)
- Tool was `return question` — echoed the input, giving the agent no useful context
- Fixed: calls `vn.get_related_documentation(question)` (ChromaDB retrieval, no LLM), returns top-5 schema docs as context
- Agent LLM now has grounded schema context to write a real answer

### Fix: `sql_cache` missing on non-streaming `/chat` path (`app.py`)
- `AgentDeps(vanna=vn)` → `AgentDeps(vanna=vn, sql_cache=_sql_cache)` on line 210
- Streaming path (`/chat/stream`) already had this; non-streaming path was silently bypassing the cache on every call

---

## Session 7 — Gap Analysis (2026-03-24)

### Architecture review + Wren AI comparison
Full codebase scan to identify broken wiring, missing implementations, silent failures, and deploy blockers. Researched Wren AI architecture (MDL, SQL correction loop, instructions registry). No code changed — all findings documented in tasklist.md.

### Design decision — Instruction registry
Root cause of wrong SQL: LLM picks between `revenue`, `amount`, `line_total` with no explicit rule. Solution: static YAML instruction registry loaded into Vanna's system prompt at startup.
- `vanna/instructions/global.yml` — layer priority rule: marts → staging → raw
- `vanna/instructions/layers/marts.yml` — term → SQL mappings for canonical metrics layer
- `vanna/instructions/layers/raw.yml` — term → SQL mappings per raw source (grows as more sources land)
- Team-based dynamic overrides (load from Lightdash user API) — deferred until Lightdash auth is wired

### P0 gaps found (broken right now)
- `answer_semantic` tool in `router.py` echoes the question unchanged — no LLM answer generated
- `merge_guides()` in `instructor.py` generates a new guide from scratch on every call — existing README narrative is lost
- `sql_cache` not passed to `AgentDeps` on non-streaming `/chat` path in `app.py` — caching is inconsistent
- `needs_new_model: True` case in `app.py` is a stub — any PRD requiring a new dbt model is completely blocked

### P1 gaps found (silent failures on VPS)
- `localhost` hardcoded as default in `vn.py`, `app.py`, `housekeeper.py` — works locally, breaks on VPS
- Docker socket failure in `lightdash.py` caught by bare `except Exception` — user sees nothing
- `GEMINI_API_KEY`, `HOST_DBT_PATH`, `DOCKER_NETWORK_NAME` missing from `.env.example`
- `asyncio.run()` in `housekeeper.py` — deadlock risk if called from async context

### P2 gaps found (fragile)
- No `meta.grain` declared on any dbt model — builder.py falls back to `_needs_customer_grain()` keyword heuristic
- Designer hardcodes `'deepseek-chat'` instead of reading `VANNA_MODEL` env var
- Zero Flask route tests or agent pipeline integration tests
- `_scan_models()` re-parses all schema YAMLs on every dashboard build
- Housekeeper makes cascading sequential Lightdash API calls (no batching or caching)

---

## Session 6 — Completed Work (2026-03-10)

### Bug fixes
- **CSV export only 20 rows** — two root causes fixed:
  - `app.js`: switched from form POST to `fetch+blob` (form encoding corrupted SQL with %, & etc.)
  - `app.py`: strip trailing `LIMIT N` from SQL in `/export` before re-executing (DeepSeek may add one)
- **Data Modeler misses staging table for customer grain** — `builder.py`:
  - Added `_CUSTOMER_GRAIN` keyword set and `_needs_customer_grain()` function
  - If PRD metrics mention customer_id, leaderboard, per customer etc., restrict model candidates to models with `customer_id` column — prevents `daily_sales` (canonical, no customer_id) from winning over `stg_orders`

### Enhancements
- **Instructor README regeneration on `partial_uncovered`**:
  - `instructor.merge_guides()`: merges existing + new PRD into a combined guide
  - `lightdash.update_readme_tile()`: updates the README.md tab markdown tile in the existing dashboard YAML and triggers redeploy
  - `app.py`: wires both on `partial_uncovered` before proceeding with new build
  - README becomes a living doc reflecting all use cases for that dashboard
- **Housekeeper structural comparison** (field-level + model-level):
  - `_chart_field_keywords()`: loads metric/dimension field IDs from chart YAMLs per dashboard, merges into Jaccard fingerprint
  - `check()` now accepts `model_name`; same dbt model → score floored at `_PARTIAL_THRESHOLD`
  - `app.py`: reordered to run data modeler before housekeeper so model_name is available
  - PRD JSON fingerprints now store `model` field
- **DPM metrics vs dimensions distinction**:
  - `PRD.dimensions: list[str] = []` — new field for grouping fields (city, category, date)
  - DPM instructions updated: separate question 4 into metrics (aggregations) vs dimensions (grouping)
  - `builder.py`: `run_data_modeler` uses `metrics + dimensions` for coverage scoring
  - `lightdash.py`: `_plan_charts` receives dimensions and merges keywords into chart selection
  - `instructor.py`: guide prompt includes dimensions for richer tips
  - `app.js`: PRD card shows Dimensions section when present

---



## Current State (2026-03-07, session 4)

### Stack
- **Ingestion:** dlt → Prefect → ClickHouse (`default.raw___orders`)
- **Transformation:** dbt → `transformed_staging.stg_orders` (view) + `transformed_marts.daily_sales` (table)
- **OLAP:** ClickHouse with `bi_readonly` user for Lightdash + Vanna
- **BI:** Lightdash (pinned `0.2473.1`) with automated first-boot deploy
- **AI Service:** Vanna (pydantic.ai orchestrator) on port 8084 with floating chat widget

---

## Next Steps (priority order)

1. **Test suite** — pytest unit tests, dbt tests, smoke tests, GitHub Actions CI
2. **Deploy to VPS via Coolify** — push changes, redeploy all services

---

## Session 3 — Completed Work (2026-03-06)

### Semantic layer + auto-training
- Enriched `dbt/models/marts/schema.yml` with full dimension/metric metadata (labels, descriptions, groups, round)
- Added 3 derived metrics: `average_order_value`, `revenue_per_customer`, `units_per_order` (type: number, sql with `${ref}` expressions)
- `vanna/train_from_schema.py` — hash-based incremental trainer:
  - Generates Q&A pairs (104 pairs from 8 metrics × 3 dimensions)
  - Generates documentation strings (metric/dimension business context)
  - Reads PRD JSON files → documentation strings (semantic fingerprints for housekeeper)
  - Hash state stored at `/data/vanna-retrain-state.json` — skips unchanged files
  - Returns stats dict: `{qa_added, qa_skipped, docs_added, docs_skipped}`

### Schema validation
- `dbt/validate_schema.py` — validates canonical models on every PR/push:
  - Required fields: label, description, groups, round
  - Approved group names (enforced list)
  - Derived metric sql references resolve to real metric keys
- `.github/workflows/validate-schema.yml` — triggers on `dbt/models/**` changes
- `prefect/flows/vanna_retrain.py` — `validate_schema` task runs before retrain (runtime guard)

### Prefect flows refactored
- `prefect/flows/vanna_retrain.py` — new dedicated file with `validate_schema` + `retrain_vanna_schema` tasks
- `prefect/flows/main_pipeline.py` — cleaned up: imports from dedicated files, pipeline: `dlt → dbt → validate_schema → retrain_vanna_schema`

### Housekeeper improvements
- `check(prd, vn=None)` — now accepts vn for semantic disambiguation
- ChromaDB semantic search replaces LLM in ambiguous zone (0.5–0.7 Jaccard): `_chromadb_disambiguate()` queries `vn.get_related_documentation()` against stored PRD docs
- LLM retained as fallback only
- PRD docs in ChromaDB enable semantic duplicate detection (catches "regional revenue" = "city performance")

### GitHub Actions
- `.github/workflows/validate-schema.yml` — validate semantic layer on PR
- `.github/workflows/deploy-lightdash.yml` — `lightdash upload` on push to main when `dbt/lightdash/**` changes

### Lightdash deploy split
- GitHub Actions handles git→Lightdash (upload on merge) — immediate
- Prefect sync handles Lightdash→git (download UI changes every 15 min) — unchanged
- Entrypoint script keeps upload as fallback for fresh container starts

### Latency improvements
- **SSE streaming** — `/chat/stream` endpoint (thread+queue async→sync bridge)
  - Status event sent immediately ("Thinking…")
  - Text tokens stream into chat bubble word by word as LLM generates
  - Chart + table rendered on final result event
  - Frontend updated to use SSE (`fetch` with `ReadableStream`)
- **SQL cache** — `_sql_cache` in `app.py`, checked in `explore_data` tool before `vn.generate_sql()` call
  - Saves ~1-2s on repeated questions (skips DeepSeek SQL generation)
  - In-memory, resets on restart, shared across both endpoints

### RAM
- Lightdash: `NODE_OPTIONS=--max-old-space-size=640` — nudges V8 GC without hard kill
- No `mem_limit` set (Lightdash was at 830MB in docker stats — hard cap would OOM kill it)
- Vanna at 335MB — almost entirely ChromaDB ONNX model → target of zvec migration

---

## Session 4 — Completed Work (2026-03-07)

### BM25 migration complete
- `vanna/vec.py` — `BM25Store` with persistent JSON + BM25Okapi retrieval
- `vanna/vn.py` — `VannaLite` replacing full vanna+ChromaDB stack
- `docker/Dockerfile.vanna` — `rank-bm25` + `pandas` replace vanna package (no ONNX)
- Bug fixed in `vec.py:get_similar_question_sql` — dict sort tiebreaker using index
- RAM: **120MB** (down from 335MB — 215MB saved, ONNX model eliminated)
- Smoke test passed: "total revenue by category" → correct SQL + bar chart

### Test suite complete
- `tests/conftest.py` — sys.path + vn module stub (prevents ClickHouse at import)
- `tests/test_vec.py` — 8 tests: BM25Store add/retrieve, persistence, tiebreaker regression
- `tests/test_app_utils.py` — 7 tests: `_trim_to_user_turn`, `_strip_explore_rows` edge cases
- `tests/test_housekeeper.py` — 15 tests: `_normalise_field`, `_keywords`, `_jaccard`, `_slugify`, `check()` with mocked fingerprints
- **30/30 passing** inside vanna Docker container
- `dbt/tests/assert_daily_sales_has_rows.sql` — singular test (row count > 0)
- `dbt/models/marts/schema.yml` — added `accepted_values` for category
- `dbt/models/staging/schema.yml` — added `unique` + `not_null` for `order_id`
- `.github/workflows/pytest.yml` — runs on push/PR touching `vanna/**` or `tests/**`
- Also fixed: BM25 `score > 0` filter drops all results in small corpora (negative IDF) — removed filter

---

## Session 5 — Completed Work (2026-03-08)

### PostgreSQL migration complete (ClickHouse removed)
- Replaced `clickhouse` service with `analytics-db` (postgres:15, separate from Prefect's postgres)
- `docker/analytics-db-init/01-readonly-user.sh` — creates `bi_readonly` user with `pg_read_all_data` role
- `vanna/vn.py` — `clickhouse_connect` → `psycopg2`, lazy reconnect, PostgreSQL system prompt
- `docker/Dockerfile.vanna` — `clickhouse-connect` → `psycopg2-binary`
- `docker/Dockerfile.lightdash-deploy` — `dbt-clickhouse` → `dbt-postgres`
- `requirements.prefect.txt` — `dlt[clickhouse]` + `dbt-clickhouse` → `dlt[postgres]` + `dbt-postgres`
- `prefect/flows/dlt_ingestion.py` — `dlt.destinations.clickhouse(...)` → `dlt.destinations.postgres(credentials=url)`
- `dbt/profiles.yml` — clickhouse adapter → postgres adapter
- `dbt/models/staging/sources.yml` — `schema: default`, `raw___orders` → `schema: raw`, `orders` (dlt PostgreSQL naming)
- `dbt/models/staging/stg_orders.sql` — `toDate(order_date)` → `order_date::date`
- `vanna/train.py` — all SQL translated: DATE_TRUNC, LAG, CURRENT_DATE, INTERVAL '1 month', NULLIF
- `.env.example` — CLICKHOUSE_* → ANALYTICS_DB_*, added ParadeDB upgrade note
- `docker-compose.yml` — removed clickhouse, added analytics-db, updated all env var references
- Full pipeline verified: dlt → dbt PASS=2, schema validation passed, Lightdash sync complete
- Note: upgrade to ParadeDB if GROUP BY query latency >500ms at >1M rows — drop-in compatible

## Pending / Known Issues

- VPS deployment not yet done — all changes local only

---

## Completed Work (feature summary)

### Vanna chat widget
- Full-page Lightdash iframe + floating chat bubble (bottom-right, `#7262ff`)
- Popup and right side-panel modes (toggle via expand button)
- 3-intent routing via pydantic.ai: `explore_data`, `answer_semantic`, `clarify`
- Markdown rendering: headers, bold, italic, inline code, bullet/ordered lists, tables
- Plotly charts: bar, line, grouped_bar, heatmap, kpi — driven by server-side Data Visualizer Agent
- KPI scorecard for single-value results (1 row × 1 numeric col)
- SSE streaming: text tokens stream word by word, full result on completion
- SQL cache: repeated questions skip LLM SQL generation (~1-2s saved)
- Collapsible SQL block, data table with formatted numbers, row count
- CSV export button
- 👍 👎 feedback buttons (below data, after user sees results)
- Stop button (AbortController) to cancel in-flight requests
- Session limit: warning at 19, disabled at 20 exchanges

### Security
- Server-side session storage: `sessions` dict in `app.py`, keyed by UUID
- Client holds only `session_id`
- `bi_readonly` ClickHouse user: SELECT-only

### Agents
- Router (pydantic-ai): explore / semantic / clarify
- DPM/Planner: multi-turn PRD creation from exploration history
- Data Modeler: finds existing dbt model for PRD metrics
- Lightdash: generates chart + dashboard YAMLs, triggers upload
- Housekeeper: Jaccard + ChromaDB semantic disambiguation; advisory-only; full/partial_covered/partial_uncovered/none
- Storyteller: deterministic Minto Pyramid layout (KPI top, bars mid, trend full-width)
- Instructor: generates DashboardGuide (overview, use cases, tips) embedded as README.md tab
- Designer: server-side chart spec (type, x, y, group) for chat widget

### Semantic layer
- `dbt/models/marts/schema.yml` — full metric/dimension metadata with groups, descriptions
- `vanna/train_from_schema.py` — hash-based incremental trainer (Q&A pairs + docs + PRD docs)
- `dbt/validate_schema.py` — convention enforcer (CI + runtime)
- PRD persistence: `dbt/lightdash/prd/<slug>.json` after every successful build

### ClickHouse
- `order_date` cast via `toDate()` at dbt staging layer
- `lagInFrame()` instead of standard `LAG()` (ClickHouse 24.3)
- All `GROUP BY` use column expressions not aliases

### History stability
- `_trim_to_user_turn()`: fast-forwards to first `UserPromptPart` after sliding window
- `_strip_explore_rows()`: removes large data payloads from history
