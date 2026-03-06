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
- [ ] Add "Save Dashboard" button (appears after first explore result)
- [ ] Add dashboard creation mode (separate from normal chat flow)
- [ ] DPM conversation UI — multi-turn clarification within widget
- [ ] Show PRD summary before committing to build
- [ ] Return dashboard URL when complete

### DPM Agent (`vanna/agents/dpm.py`)
- [ ] Read full session history and compile exploration story
- [ ] Ask clarifying questions: objective, audience, action items
- [ ] Validate that current metrics support the objective — suggest alternatives if not
- [ ] Produce structured PRD (title, objective, audience, metrics, filters, action items)

### Data Modeler Agent (`vanna/agents/data_modeler.py`)
- [ ] Read PRD, determine required grain (smallest granularity for filters + group + aggregation)
- [ ] Check if existing dbt model covers it — reuse or create new
- [ ] Generate SQL via Vanna, validate against ClickHouse
- [ ] Write `.sql` and `schema.yml` to `dbt/models/dashboards/`
- [ ] Trigger dbt run for new model only

### Data Visualizer Agent (`vanna/agents/data_visualizer.py`)
- Dual use: (1) chat widget chart selection, (2) full dashboard YAML generation
- [x] Build chart template library (bar, line, grouped bar, KPI card, heatmap)
- [x] Chat widget mode: takes columns + data sample → returns chart spec (type, x, y, group) or null
- [x] Replace rule-based `detectChart` in index.html with agent call
- [ ] Dashboard mode: takes PRD + model output → generates full Lightdash `dashboard.yml`
- [ ] Trigger `lightdash deploy` after dashboard YAML written
- [ ] Return dashboard URL

### Deploy + version control
- [ ] Option A: write `.yml` directly → deploy (fast, ~45s)
- [ ] Background git commit after deploy succeeds (version history without blocking UX)

---

## Up Next — Semantic Layer (MetricFlow + Vanna)

### Overview
Use MetricFlow YAML format to define canonical metrics (MRR, churn, revenue).
Parse definitions to auto-generate Vanna training data — no manual train.py updates needed.

- [ ] Define key metrics in `dbt/models/metrics/` using MetricFlow YAML format
- [ ] Build parser: MetricFlow `.yml` → Vanna training pairs (question + SQL)
- [ ] Auto-update `build_schema_context()` to include metric definitions
- [ ] Integrate parser into `train.py` — metrics become part of ChromaDB on retrain
- [ ] Document metric naming conventions for client onboarding

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

### Latency — swap agent model for routing + text summary
- [ ] Replace DeepSeek with a faster/cheaper model for the pydantic-ai agent (routing + text response)
      — candidates: Gemini 2.0 Flash ($0.10/M), GPT-4o-mini
      — keep DeepSeek only for Vanna's SQL generation (where accuracy matters most)
      — agent routing is a simple 3-way classification, doesn't need a 67B model
      — expected gain: agent call 3–5× faster, cost drops significantly

### Feedback loop review workflow
- [ ] Periodically review `/data/vanna-feedback.jsonl`
- [ ] Promote good question/SQL pairs into `train.py`
- [ ] Wipe ChromaDB and retrain: `docker-compose run vanna python train.py`

### Chart improvements (resolved by Data Visualizer Agent)
- Replaced by Data Visualizer Agent — handles grouped bars, heatmap, pivot, opt-in logic
- Rule-based `detectChart` in index.html to be removed once agent is live

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
