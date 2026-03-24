# Agentic BI Stack — Task List

## Stack
- **Ingestion:** dlt → Prefect → PostgreSQL
- **Transformation:** dbt core
- **OLAP:** PostgreSQL (ParadeDB drop-in upgrade path if needed)
- **BI:** Lightdash
- **AI Service & Widget:** Vanna (ChromaDB + DeepSeek) + pydantic-ai agents (Gemini Flash Lite / DeepSeek fallback)
- **Deployment:** Coolify on Contabo VPS (7.8GB RAM, ~5GB available)

---

## Active — E2E Local Deployment

### Step 1: Bring up full stack
- [x] `docker-compose up -d` — all services healthy
- [x] Verify ClickHouse, Lightdash, Vanna, Prefect all reachable

### Step 2: Run the pipeline
- [x] Trigger dlt ingestion via Prefect (or `.fn()` directly)
- [x] Verify `default.raw___orders` has data in ClickHouse
- [x] Run dbt transformation — verify `transformed_marts.daily_sales` populated

### Step 3: Deploy to Lightdash
- [x] Run `docker-compose up lightdash-deploy`
- [x] Verify dbt models visible in Lightdash UI
- [x] Confirm `bi_readonly` creds stored (Lightdash uses them for queries)

### Step 4: Smoke test Vanna widget
- [x] Open `http://localhost:8084`
- [x] Test explore, semantic, clarify intents via chat
- [x] Test feedback buttons (👍 → ChromaDB, 👎 → feedback.jsonl)
- [x] Test side panel toggle (browser only — not API-testable)

---

## Up Next — Feature: Persist as Dashboard (multi-agent pipeline)

### Overview
After exploring with Vanna, user clicks "Save Dashboard". Three agents collaborate:
1. **DPM Agent** — reads conversation history, asks clarifying questions (objective, audience, action items), validates metrics alignment, produces a PRD
2. **Data Modeler Agent** — reads PRD, finds smallest granularity table, writes SQL via Vanna, creates/updates dbt model
3. **Data Visualizer Agent** — reads PRD, selects chart types, generates Lightdash `.yml`, triggers deploy

### Chat widget
- [x] Add "Save Dashboard" button (appears after first explore result)
- [x] Add dashboard creation mode (separate from normal chat flow)
- [x] DPM conversation UI — multi-turn clarification within widget
- [x] Show PRD summary before committing to build
- [x] Return dashboard URL when complete

### DPM Agent (`vanna/agents/planner.py`)
- [x] Read full session history and compile exploration story
- [x] Ask clarifying questions: objective, audience, action items
- [x] Produce structured PRD (title, objective, audience, metrics, filters, action items)

### Data Modeler Agent (`vanna/agents/builder.py`)
- [x] Read PRD, determine required grain (smallest granularity for filters + group + aggregation)
- [x] Check if existing dbt model covers it — reuse or create new
- [x] Generate SQL via Vanna, validate against ClickHouse

### Data Visualizer Agent (`vanna/agents/designer.py`)
- [x] Build chart template library (bar, line, grouped bar, KPI card, heatmap)
- [x] Chat widget mode: takes columns + data sample → returns chart spec (type, x, y, group) or null
- [x] Replace rule-based `detectChart` in index.html with agent call
- [x] Dashboard mode: takes PRD + model output → generates full Lightdash `dashboard.yml`
- [x] Trigger `lightdash upload` after dashboard YAML written
- [x] Return dashboard URL

### Deploy + version control
- [x] Write `.yml` directly → upload (fast, ~45s)
- [ ] Background git commit after deploy succeeds (version history without blocking UX)
- [x] Housekeeper agent: Jaccard similarity on dbt YAML metric fingerprints; advisory-only (never blocks); verdicts: full / partial_covered / partial_uncovered / none
- [x] Instructor agent: generates overview, use-case questions, tips from PRD; embedded as markdown tile in README.md tab of every dashboard
- [x] Storyteller: dropped LLM, fully deterministic layout (users reorder in Lightdash)
- [x] Dashboard cleanup: removed 3 duplicate dashboards, kept City Revenue Performance Dashboard
- [x] README.md tab added to City Revenue Performance Dashboard with instructor guide

---

## Up Next — Replace ChromaDB with BM25

### Overview
Vanna container uses ~335MB RAM, almost entirely the ChromaDB ONNX embedding model.
Replace with BM25 (rank-bm25) — no embedding model needed, ~60MB target.

- [x] Implement `vanna/vec.py` — `BM25Store` class (persistent JSON + BM25Okapi retrieval)
- [x] Rewrite `vanna/vn.py` — `VannaLite` (BM25Store + direct OpenAI client + clickhouse_connect)
- [x] Remove `vanna` package from `docker/Dockerfile.vanna`, add `rank-bm25` + `pandas`
- [x] Rebuild + smoke test: `docker-compose up -d --build vanna`
- [x] Re-seed BM25 store: run `train.py` then `POST /retrain/schema` inside container

## Completed — Semantic Layer

- [x] Enriched `dbt/models/marts/schema.yml` with full dimension/metric metadata
- [x] `vanna/train_from_schema.py` — hash-based incremental trainer (Q&A pairs + docs + PRD docs)
- [x] `dbt/validate_schema.py` — convention enforcer with approved groups list
- [x] `prefect/flows/vanna_retrain.py` — dedicated flow with validate + retrain tasks
- [x] `.github/workflows/validate-schema.yml` — CI gate on schema changes
- [x] `.github/workflows/deploy-lightdash.yml` — upload on merge to main
- [x] Housekeeper: ChromaDB semantic search replaces LLM in ambiguous zone
- [x] SSE streaming: `/chat/stream` with word-by-word text tokens
- [x] SQL cache: repeated questions skip LLM SQL generation

---

## Up Next — Test Suite + CI/CD

### pytest unit tests (`tests/`)
- [ ] Agent routing logic (explore / semantic / clarify / create_dashboard)
- [ ] `_trim_to_user_turn` edge cases
- [ ] `_strip_explore_rows` behaviour
- [ ] Chart detection logic (`detectChart`)
- [ ] Lightdash YAML validation (schema + required fields)
- [ ] MetricFlow parser correctness

### dbt tests (extend existing)
- [ ] Data freshness tests on raw source
- [ ] Accepted values for `category`, `city`
- [ ] Row count > 0 assertion on `daily_sales`
- [ ] Referential integrity staging → marts

### Smoke tests (extend `smoke_test.py`)
- [ ] DPM Agent: clarification flow returns PRD
- [ ] Data Modeler Agent: SQL valid + ClickHouse executes
- [ ] Data Visualizer Agent: valid Lightdash YAML generated
- [ ] End-to-end dashboard round-trip (save → deploy → URL accessible)

### GitHub Actions
- [ ] On push: pytest unit tests + dbt test (ClickHouse service container)
- [ ] On push: Lightdash YAML lint/validate
- [ ] Merge to main → Coolify deploys → smoke-test container gates the deploy

---

## Up Next — Gap Fixes (found 2026-03-24)

### P0 — Broken right now
- [x] **`answer_semantic` returns question unchanged** (`router.py`) — tool body does `return question`; should call LLM with schema context to produce a real answer
- [x] **`merge_guides()` is a no-op** (`instructor.py`) — generates new guide from scratch, ignores existing README; fix `_merge()` to combine both PRDs before generating
- [x] **`sql_cache` missing on non-streaming path** (`app.py`) — `AgentDeps(vanna=vn)` missing `sql_cache=_sql_cache`; caching broken on `/chat` endpoint
- [x] **`needs_new_model` unimplemented** (`app.py`) — stub returns error; implement minimal dbt model scaffolding or at least a clear user-facing message with the required SQL

### P1 — Silent failures on VPS
- [x] **Hardcoded `localhost` defaults** — remove defaults from `vn.py`, `app.py`, `housekeeper.py`; require explicit env vars
- [x] **Docker socket failure is silent** (`lightdash.py`) — replace bare `except Exception` with specific exception + surfaced error in API response
- [x] **Missing env vars in `.env.example`** — add `GEMINI_API_KEY`, `HOST_DBT_PATH`, `DOCKER_NETWORK_NAME`
- [x] **`asyncio.run()` in housekeeper** (`housekeeper.py`) — replace with sync HTTP or restructure to avoid blocking async event loop

### P2 — Fragile / incomplete
- [x] **`meta.grain` not declared** (`dbt/models/*/schema.yml`) — add `meta.grain` and `meta.relationships` to all models; update `builder.py` to use them instead of `_needs_customer_grain()` heuristic
- [x] **Designer hardcodes model name** (`designer.py`) — replace `'deepseek-chat'` with `os.environ.get('VANNA_MODEL', 'deepseek-chat')`
- [x] **Flask route tests missing** (`tests/`) — add pytest tests for `/chat/stream`, `/dashboard/build`, `/export`, `/feedback`
- [x] **`_scan_models()` re-parses on every call** (`builder.py`) — cache result at module level, invalidate on file change
- [x] **Housekeeper API calls not batched** (`housekeeper.py`) — reduce cascading per-chart calls; fetch dashboard tiles in bulk where Lightdash API allows

---

## Up Next — VPS Deployment

### Deploy to Coolify
- [ ] Push all changes to git (main branch)
- [ ] Redeploy vanna + analytics-db + lightdash services via Coolify (ClickHouse removed — now PostgreSQL)
- [ ] Re-run `lightdash-deploy` on VPS
- [ ] Set `LIGHTDASH_API_KEY`, `GEMINI_API_KEY`, `ANALYTICS_DB_*` in Coolify env vars
- [ ] Mount `/var/run/docker.sock` in Vanna container via Coolify volume config
- [ ] Smoke test all services on VPS URLs

---

## Backlog

### Maintainer DX — schema.yml authoring
- [ ] **`docs/schema-template.yml`** — annotated reference file clients copy; every field commented with what it does, valid values, and what breaks if missing
- [ ] **Better validator error messages** in `validate_schema.py` — currently fails generically; should say exactly which field is missing, on which model, and point to the template. Example:
  ```
  ERROR: daily_sales missing meta.grain
  Expected: meta.grain: [col1, col2] — columns that form the surrogate key
  See: docs/schema-template.yml
  ```
- [ ] **JSON Schema for `schema.yml`** — drop in `.vscode/settings.json`; maintainer gets autocomplete + inline red underlines as they type, no CI run needed to catch mistakes

### Maintainer DX — Lightdash YAML authoring (charts, dashboards, PRD)
- [ ] **`docs/chart-template.yml`** and **`docs/dashboard-template.yml`** — annotated reference files showing every required field with comments; UUIDs/slugs explained; `metricQuery` structure documented with examples
- [ ] **`docs/prd-template.json`** — reference PRD JSON with all fields explained (metrics vs dimensions distinction, what `model` refers to, what `built_at` is)
- [ ] **Lightdash YAML validator** — extend `validate_schema.py` or add a separate `validate_lightdash.py` that checks:
  - chart YAMLs: required fields (`name`, `tableName`, `metricQuery`, `slug`, `chartConfig`, `spaceSlug`)
  - dashboard YAMLs: required fields, that all `chartSlug` references in tiles resolve to an existing chart file, tab UUIDs are valid
  - PRD JSONs: all required fields present, `model` references a real dbt model
- [ ] **JSON Schema for chart and dashboard YAMLs** — IDE autocomplete for Lightdash content-as-code format
- [ ] Add Lightdash YAML validation to `.github/workflows/validate-schema.yml` so bad YAMLs are caught on PR before they reach `lightdash upload`

### SQL validation + correction loop (gap vs Wren AI)
- [ ] Currently `explore_data` executes SQL directly — if DeepSeek generates bad SQL it just errors with no retry
- [ ] Add a dry-run validation step before execution: run `EXPLAIN` (or execute with `LIMIT 0`) to catch syntax/schema errors without fetching data
- [ ] On validation failure, retry SQL generation with the error message as additional context — up to 3 attempts (same pattern as Wren's `SqlCorrectionPipeline`)
- [ ] Implementation: in `router.py` `explore_data` tool — wrap `vn.run_sql()` with a validate-then-execute pattern; pass error back to `vn.generate_sql()` on retry with a correction prompt

### MDL-style metric injection into Vanna training (Wren-inspired)
`meta.metrics` is defined in `schema.yml` and `train_from_schema.py` generates Q&A pairs from it, but the training docs don't explicitly tell the LLM *which expression to use and from which table* — so it still picks between `revenue`, `amount`, `line_total` arbitrarily.
- [ ] Update `train_from_schema.py` to generate explicit disambiguation docs per metric: `"To calculate revenue, use SUM(total_revenue) from marts.daily_sales. Do not use amount or line_total from raw.orders unless customer-level grain is required."`
- [ ] One doc per `meta.metrics` entry, one doc per raw source column that could be confused with a canonical metric
- [ ] These docs feed into `vn.add_documentation()` on retrain — no new infra needed, just better training content

### Question→SQL verified pairs (Wren-inspired)
In-memory `_sql_cache` already skips re-generation for repeated questions. The gap is that validated pairs from 👍 feedback are not promoted into a persistent, curated registry.
- [ ] Persist `_sql_cache` to `/data/sql_cache.json` on write, reload on startup — survives container restarts
- [ ] On 👍 feedback: in addition to training ChromaDB, append `{question, sql, timestamp}` to `/data/verified_pairs.jsonl` — a human-reviewable log of confirmed good queries
- [ ] `train.py` can ingest `verified_pairs.jsonl` on next retrain so confirmed pairs enter ChromaDB retrieval permanently

### Explicit join paths as Vanna training docs (Wren-inspired)
`meta.relationships` will be declared in `schema.yml` (see Data Modeler backlog), but they're only used by `builder.py` for model selection. The LLM generating SQL has no knowledge of join paths.
- [ ] After `meta.relationships` are declared, extend `train_from_schema.py` to emit join-path docs: `"To join daily_sales to stg_orders, use order_date, category, city. stg_orders.customer_id is the customer grain key not present in daily_sales."`
- [ ] One doc per relationship in `schema.yml` `meta.relationships`
- [ ] Feeds into `vn.add_documentation()` — no new infra, improves multi-table SQL accuracy

### Schema retrieval quality audit (Wren-inspired)
ChromaDB retrieval is only as good as what was indexed. Currently `train_from_schema.py` adds Q&A pairs and metric docs but there's no audit of what actually gets retrieved for a given question.
- [ ] Add a `vanna/scripts/audit_retrieval.py` script: given a sample question, print the top-K docs retrieved from ChromaDB — makes it visible what context the LLM sees before generating SQL
- [ ] Run audit against 5–10 known questions; identify gaps where wrong docs surface or relevant docs are missing
- [ ] Use audit results to improve training doc wording in `train_from_schema.py` and `train.py`

### Instruction registry (Wren-inspired, static)
Root cause of wrong queries: LLM picks between `revenue`, `amount`, `line_total` arbitrarily because no explicit rule says which to use and when. Fix: a static instruction registry loaded into Vanna's system prompt at startup.

**Layer priority rule (global, always applies)**
- [ ] Hard-code in `vanna/instructions/global.yml`: always prefer `marts` layer → `staging` → `raw`; only fall back when the required grain is absent from the higher layer

**Per-layer term registries**
- [ ] `vanna/instructions/layers/marts.yml` — maps business terms to canonical SQL expressions from `marts.daily_sales`:
  - `"revenue"` → `SUM(total_revenue)`
  - `"orders"` → `COUNT(DISTINCT order_id)` via `order_count`
  - `"customers"` → `COUNT(DISTINCT customer_id)` via `customer_count`
  - etc. — one entry per `meta.metrics` field in `schema.yml`
- [ ] `vanna/instructions/layers/raw.yml` — maps same terms to row-level expressions from `raw.orders` (used only when marts grain is insufficient):
  - `"revenue"` → `SUM(amount * quantity)` from `raw.orders`
  - Add a section per source as new raw tables land (customers, events, etc.)
  - Structure: `source_name`, `table`, `grain`, `term_mappings[]`

**Registry loader**
- [ ] `vanna/instructions/__init__.py` — `load_instructions() -> str` merges global + all layer YAMLs into a single plain-text block injected into Vanna's system prompt on startup
- [ ] Layer files are YAML, human-editable, no code change needed to add a new term or source
- [ ] On container start, `train.py` or `app.py` calls `load_instructions()` and passes result to `vn.add_documentation()`

**Deferred — team-based dynamic overrides**
- [ ] When Lightdash auth is wired: fetch user's group from Lightdash API, load `vanna/instructions/teams/{team}.yml` on top of global rules (e.g. finance team: use `net_revenue` not `total_revenue`); fall back to global if team unknown

### Latency — reduce LLM round-trips
- Each explore query makes 3 sequential LLM calls: router intent (~400ms) + vanna generate_sql (~2700ms) + router summary (~400ms)
- ChromaDB ONNX retrieval adds ~967ms on top
- Options: merge routing + SQL into one call; replace ONNX with pgvector + DeepSeek embeddings
- [x] Routing + DPM agents use DeepSeek (Gemini removed — free tier too small)
- DeepSeek retained for Vanna SQL generation (accuracy > speed for this step)

### Dashboard chart positioning
- [x] Storyteller agent: Minto Pyramid layout — KPI top, breakdowns side-by-side, trend full-width
- [x] 36-column Lightdash grid, LLM orders weight-2 bars by PRD relevance

### ClickHouse partitioning + clustering
- [ ] Add `PARTITION BY toYYYYMM(order_date)` to `daily_sales` dbt model for faster time-range scans
- [ ] Evaluate ClickHouse Keeper + Distributed engine if multi-node becomes needed

### Feedback loop review workflow
- [ ] Periodically review `/data/vanna-feedback.jsonl`
- [ ] Promote good question/SQL pairs into `train.py`
- [ ] Wipe ChromaDB and retrain: `docker-compose run vanna python train.py`

### Chat widget modularisation
- [ ] Split `vanna/static/app.js` into modules: `sse.js`, `chart.js`, `dashboard.js`, `chat.js`
- Same behaviour, no framework change — improves maintainability

### Chart improvements (resolved by Data Visualizer Agent)
- Replaced by Data Visualizer Agent — handles grouped bars, heatmap, pivot, opt-in logic
- Rule-based `detectChart` in index.html to be removed once agent is live
- [ ] Pivot table support in chat widget: for multi-dim breakdowns (e.g. category × city × revenue), render as pivot table instead of flat table

### Vanna widget auth
- [ ] No authentication on port 8084 — anyone who can reach vanna can query the DB
- [ ] Options: nginx basic auth in front of /vanna/, or token header check in Flask

### In-memory state (lost on container restart)
- [ ] `sessions` dict (conversation history) resets on restart — acceptable for now, use Redis or SQLite for persistence
- [ ] `_sql_cache` dict resets on restart — persist to disk (e.g. shelve or JSON) to survive deploys

### Background git commit after dashboard deploy
- [ ] After `lightdash-deploy` succeeds, commit new `.yml` files to git for version history
- [ ] Currently: `.yml` files written to disk but not committed automatically

### BM25 section (stale — reverted to ChromaDB)
- [x] ~~Replace ChromaDB with BM25~~ — reverted; BM25 lacks generalization, ChromaDB/ONNX retained

### Chat UX — date context awareness
- [ ] Agent doesn't understand follow-up questions about data period (e.g. "is it overall period?", "filter only march") — lacks awareness of what date range the previous query covered
- [ ] Every explore result should surface the date range it covers (e.g. "Data from 2026-01-01 to 2026-03-08") so users know what they're looking at without asking

### Chat UX — key takeaways quality
- [ ] `answer_semantic` LLM doesn't reason about data constraints (e.g. 100% multi-city customers → no comparison is possible)
- [ ] Options: pass summary statistics (counts, min/max of key columns) as context when routing to `answer_semantic`; or upgrade to a stronger model for semantic answers

### Housekeeper — add structural comparison layer
- [x] Field-level: chart YAML field IDs merged into Jaccard fingerprint per dashboard
- [x] Model-level: check() accepts model_name; same dbt model → score floored at partial threshold
- [x] app.py reordered: data modeler runs before housekeeper so model_name is available

### CSV export only downloads 20 rows
- [x] Fixed: switched export from form POST to fetch+blob (encoding corruption); strip trailing LIMIT in /export backend

### Data Modeler — grain-aware model selection
- [x] Partial fix: _needs_customer_grain() restricts candidates to models with customer_id when PRD mentions customer-level grain
- [ ] **Backlog — proper fix:** declare grain + relationships in `meta` block, use them for model selection
  - **Step 1:** add `meta.grain` and `meta.relationships` to all models in `schema.yml`:
    ```yaml
    - name: daily_sales
      meta:
        canonical: true
        grain: [order_date, category, city]
        relationships:
          - to: stg_orders
            type: many_to_one
            join_on: [order_date, category, city]
    - name: stg_orders
      meta:
        canonical: false
        grain: [order_id]
    ```
  - **Step 2:** update `validate_schema.py` to enforce `meta.grain` is declared on all models
  - **Step 3:** update `find_best_model()` in `builder.py` — pick coarsest model whose `meta.grain` ⊇ required PRD dimensions; fall back to lowest-grain (staging) only when no summary table covers it
  - **Step 4:** remove `_needs_customer_grain()` hardcoded approach
  - Why `meta` and not MetricFlow entities: avoids a second semantic layer alongside Lightdash `meta.metrics`; already the convention in this project; machine-readable by existing tooling (`builder.py`, `validate_schema.py`, `train_from_schema.py`)

### Instructor — update README when dashboard is enriched with new narrative
- [x] merge_guides() merges existing + new PRD; update_readme_tile() updates YAML + redeploys; wired in app.py on partial_uncovered

### DPM agent — metrics vs dimensions distinction
- [x] PRD.dimensions added; DPM instructions updated; builder/lightdash/instructor all use dimensions field

### Dashboard — customer detail table
- [ ] Dashboard builder should include a ranked table tile (customer_id, total_revenue, city, pct_of_city, rank) so sales teams can see who to reach out to
- [ ] The Data Visualizer agent currently favours chart tiles; needs to support table tiles for leaderboard use cases

### Polish for pitch/demo
- [ ] Prepare realistic sample business dataset
- [ ] Pre-build demo Lightdash dashboards
- [ ] Rehearse demo flow

---

## Completed

- [x] Analytics DB (PostgreSQL) setup with `bi_readonly` user (SELECT-only, password from env) — migrated from ClickHouse in session 5
- [x] dbt staging + marts models with `order_date` cast to `Date`
- [x] Prefect pipeline: dlt ingestion → dbt transformation
- [x] Lightdash with automated first-boot deploy + MinIO for file storage
- [x] Vanna pydantic.ai agent: 3-intent routing (explore / semantic / clarify)
- [x] Chat widget: markdown, charts, CSV export, stop button, side panel, session limit
- [x] Server-side session storage (no internals exposed to browser)
- [x] dbt schema context fix (`./dbt:/dbt:ro` volume mount)
- [x] Feedback loop: 👍 trains ChromaDB, 👎 logs to `feedback.jsonl`
- [x] E2E smoke test: all 3 intents verified locally
- [x] Data Visualizer Agent (chat widget mode): bar, line, grouped_bar, heatmap, kpi — replaces `detectChart`
- [x] KPI scorecard: single-value results rendered as large number card
- [x] Slow query UX hint: shown after 8s of waiting
- [x] Automated deployment: `pipeline-init` + `smoke-test` services in docker-compose.yml
- [x] nginx proxy: injects Vanna chat widget into Lightdash UI via `sub_filter`; widget opens as side panel and pushes Lightdash content
- [x] Gemini 2.0 Flash as default routing/DPM model; DeepSeek fallback
- [x] Save as Dashboard: full multi-agent flow (DPM → Data Modeler → Data Visualizer → Lightdash upload)
- [x] Dashboard URL returned in chat widget after build
- [x] dbt `meta.metrics` defined for Lightdash chart compatibility
