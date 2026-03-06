# CLAUDE.md — Agentic BI Stack

## Project Overview
Self-hosted agentic BI stack on a Contabo VPS (7.8GB RAM, ~5GB available) managed via Coolify. Stakeholders ask questions in plain English via vanna.ai, which explores data and generates persistent Lightdash dashboards by writing `.yml` files into the dbt project.

## Stack
- **Ingestion:** dlt (as Prefect tasks inside `prefect/flows/`)
- **Orchestration:** Prefect (already deployed on VPS via Coolify — do not reinstall)
- **Transformation:** dbt core
- **OLAP:** ClickHouse
- **BI:** Lightdash
- **AI Service & Widget:** vanna.ai (conversational exploration + dashboard generation)
- **Deployment:** Coolify on Contabo VPS

## Architecture Decisions (finalized — do not re-suggest alternatives)
- **ClickHouse over DuckDB** — more robust OLAP backend; DuckDB file-sharing across Docker services was a friction point
- **Lightdash over Superset** — Superset REST API too unreliable for programmatic dashboard creation; Lightdash uses `.yml` files in the dbt project which suits agentic code generation
- **vanna.ai over custom FastAPI + Vanilla JS widget** — replaces the old multi-agent pipeline (Relevance → Intent → Clarify → SQL → Visual); not reinventing the wheel
- **Dashboard generation via `.yml` commits** — AI generates Lightdash `.yml` definitions and writes them into the dbt project; Lightdash picks them up automatically
- **Everything on one VPS** — services share the same Docker network (`data-network`) and host volumes; cannot be split across hosts
- **Prefect already running** — server and worker deployed on VPS via Coolify (~300MB RAM); in local dev use `.fn()` to run tasks directly

## VPS Resource Constraints
- Total RAM: 7.8GB, ~5GB available
- Already running: n8n, Memos, Uptime Kuma, Prefect server + worker, PostgreSQL (x2), Coolify stack (~1.3GB consumed)
- Before spinning up any new Docker container, check estimated RAM against the available limit

## Repo Structure
```
DATA-PLATFORM/
├── dbt/
├── docker/
│   ├── Dockerfile.prefect
│   ├── Dockerfile.lightdash
│   └── Dockerfile.vanna
├── prefect/
│   └── flows/
│       ├── dbt_transformation.py
│       ├── dlt_ingestion.py
│       ├── main_pipeline.py
│       └── path_setup.py
├── vanna/
├── prompt/
│   ├── CLAUDE.md
│   ├── initial-prompt.md
│   ├── progress.md
│   └── tasklist.md
├── .env
├── .env.example
├── prefect.yaml
├── docker-compose.yml
├── docker-compose.coolify.yml
└── requirements.txt
```

## Task Management
- Current tasks and status: `prompt/tasklist.md`
- Session history and decisions: `prompt/progress.md`
- Always read both files at the start of a session to orient yourself

## Agent Design Principles

Rules to follow every time an agent is built or modified in this project.

### 1. Function first, LLM last
If something can be computed deterministically, write a function — not an agent.
- Column classification → `analyze_result()` (type inference, no LLM)
- Chart filtering → `match_catalog()` (rule-based, no LLM)
- Auto-assign when 1 option → `_auto_assign()` (no LLM)
- LLM only when 2+ options require genuine semantic judgment

### 2. Shortlist before calling the agent
Pre-filter options with a function. The agent picks from a shortlist, never the full set.
- 0 options → return default, skip agent entirely
- 1 option → auto-assign, skip agent entirely
- 2+ options → call agent with the shortlist only

### 3. Agent context contains only the decision inputs
The agent prompt contains only what's needed for its specific decision — nothing else.
- No raw data rows in context — store in `AgentDeps`, read after run
- No full schema in routing agent — schema lives in Vanna's ChromaDB
- No full catalog — only pre-filtered compatible options
- Metadata only: row_count, column names, inferred types

### 4. Data plane ≠ reasoning plane
Query results (rows) are data plane — they live in Python, never pass through LLM context.
- LLM produces: routing decision, narrative text, column assignments
- Python produces: SQL execution, row fetching, rendering

### 5. Agent output is minimal
LLM generates only what it uniquely contributes.
- Routing agent → `{intent, text, sql}` only
- Data Visualizer → `{type, x, y, group, title}` only
- Never have the agent echo back data it received as a tool return

### 6. One agent, one concern
Each agent has a single responsibility.
- Routing agent: classify intent only
- Data Visualizer: chart selection only
- Do not bundle routing + SQL generation + summarisation into one agent

### 7. Keep tool descriptions to one line
`@agent.tool` docstrings are included in the JSON schema sent to the model on every request.
One clear sentence is enough — avoid paragraphs.

## Interaction Rules (STRICT — enforce every session)
1. **Terminal Commands:** Run local `docker-compose` and build commands directly without asking. For VPS-affecting or destructive operations (force push, data deletion, redeployment in production), confirm first.
2. **Review Every File:** Before creating or modifying any file, show me the full content or a diff and explain why the change is necessary — I need time to review before committing to git
3. **One Task at a Time:** Complete exactly ONE sub-task from `tasklist.md`, update `progress.md`, then STOP — do not start the next task until I say so
4. **Resource Awareness:** If a task involves a new Docker container, state the estimated RAM usage before proceeding
5. **Commit Often:** After completing a feature or fix, stage and commit relevant files with a descriptive message. Do not let untracked/modified files accumulate across sessions.

## Deferred / Backlog
- Wren AI (revisit at v2 if text-to-SQL accuracy becomes a concern)
- Evidence (deprecated, to be fully removed from VPS)
- Old `ai-service/` and `widget/` directories (removed, replaced by `vanna/`)
- `evidence_rebuild.py` Prefect flow (deprecated)