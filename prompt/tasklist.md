# Agentic BI Stack — Task List

## Stack
- **Ingestion:** dlt → Prefect → ClickHouse
- **Transformation:** dbt core
- **OLAP:** ClickHouse
- **BI:** Lightdash
- **AI Service & Widget:** Vanna + pydantic.ai
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

## Up Next — Replace ChromaDB with zvec + BM25

### Overview
Vanna container uses ~335MB RAM, almost entirely the ChromaDB ONNX embedding model.
Replace with zvec (sparse/BM25 vector store) — no embedding model needed, ~60MB target.

- [ ] Implement `vanna/vec.py` — custom `VannaLite` class (same interface: generate_sql, run_sql, train, get_related_documentation)
  - BM25 sparse vectors via zvec (no neural embeddings, no API calls)
  - Persistent storage at `/data/vanna-zvec/`
  - State-aware: load existing index on startup
- [ ] Update `vanna/vn.py` to use `VannaLite` instead of `MyVanna(ChromaDB_VectorStore, OpenAI_Chat)`
- [ ] Remove `vanna` package from `docker/Dockerfile.vanna`, add `zvec` + `rank-bm25`
- [ ] Migrate existing ChromaDB training data to zvec index
- [ ] Rebuild + smoke test: `docker-compose up -d --build vanna`

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

### Latency — agent model
- [x] Routing + DPM agents switched to Gemini 2.0 Flash with DeepSeek fallback
- DeepSeek retained for Vanna SQL generation (accuracy matters most there)

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

### Chart improvements (resolved by Data Visualizer Agent)
- Replaced by Data Visualizer Agent — handles grouped bars, heatmap, pivot, opt-in logic
- Rule-based `detectChart` in index.html to be removed once agent is live
- [ ] Pivot table support in chat widget: for multi-dim breakdowns (e.g. category × city × revenue), render as pivot table instead of flat table

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
