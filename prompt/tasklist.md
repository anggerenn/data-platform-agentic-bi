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

## Up Next — VPS Deployment

### Deploy to Coolify
- [ ] Push all changes to git (main branch)
- [ ] Redeploy vanna + clickhouse services via Coolify
- [ ] Re-run `lightdash-deploy` on VPS
- [ ] Set `LIGHTDASH_API_KEY` in Coolify env vars after first boot
- [ ] Smoke test all services on VPS URLs

---

## Backlog

### Latency — reduce LLM round-trips
- Each explore query makes 3 sequential LLM calls: router intent (~400ms) + vanna generate_sql (~2700ms) + router summary (~400ms)
- ChromaDB ONNX retrieval adds ~967ms on top
- Options: merge routing + SQL into one call; replace ONNX with pgvector + DeepSeek embeddings
- [x] Routing + DPM agents switched to Gemini 2.0 Flash Lite with DeepSeek fallback
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

- [x] ClickHouse setup with `bi_readonly` user (SELECT-only, password from env)
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
