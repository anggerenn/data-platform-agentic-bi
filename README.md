# data-platform-agentic-bi

Self-hosted data platform with an agentic BI layer — full pipeline from ingestion to transformation to BI, with AI agents that let stakeholders ask questions in plain English and automatically generate persistent dashboards.

## Stack

| Layer | Tool |
|-------|------|
| Ingestion | dlt (via Prefect) |
| Orchestration | Prefect |
| Transformation | dbt core |
| Database | PostgreSQL (analytics-db) |
| BI | Lightdash |
| AI / Chat | Vanna (ChromaDB + DeepSeek) + pydantic-ai agents |
| Deployment | Coolify on Contabo VPS |

## How it works

1. **Ask a question** in the Vanna chat widget (embedded in Lightdash via nginx)
2. The router agent classifies intent: `explore` (SQL), `semantic` (schema answer), or `clarify`
3. For explore: Vanna generates and executes SQL, returns results + chart
4. Click **Save as Dashboard** — triggers a 3-agent pipeline:
   - **DPM Agent** — clarifies objective, audience, metrics, and definitions via multi-turn chat
   - **Data Modeler Agent** — finds or scaffolds the right dbt model (grain-aware)
   - **Data Visualizer Agent** — generates Lightdash `.yml` chart and dashboard files
5. Dashboard is deployed to Lightdash and a URL is returned

## Pipeline flow

```
dlt (ingest) → PostgreSQL → dbt (transform) → Lightdash (visualize)
                                                     ↑
                              Vanna agents write .yml files into dbt/
```

## Repo structure

```
data-platform-agentic-bi/
├── dbt/
│   ├── models/           # staging + marts dbt models
│   └── lightdash/        # charts, dashboards, PRDs (agent-generated)
├── docker/               # Dockerfiles + entrypoint scripts
├── prefect/flows/        # Orchestration flows (ingestion, transformation, retrain)
├── vanna/
│   ├── agents/           # DPM, builder, designer, lightdash, housekeeper, instructor
│   ├── static/           # Chat widget (HTML/JS/CSS)
│   ├── app.py            # Flask API
│   ├── vn.py             # VannaLite wrapper
│   └── train.py          # Seed training data (run once after stack is up)
├── tests/                # pytest unit tests
├── nginx/                # Lightdash reverse proxy config
├── docker-compose.yml
└── .env.example
```

## Local development

```bash
# 1. Set up environment
cp .env.example .env
# Fill in ANALYTICS_DB_PASSWORD, LIGHTDASH_SECRET, DEEPSEEK_API_KEY, GEMINI_API_KEY

# 2. Start the stack
docker-compose up -d

# 3. Seed Vanna training data (first boot only)
docker-compose exec vanna python train.py

# 4. Deploy dbt models to Lightdash (first boot only)
docker-compose run lightdash-deploy

# On first boot, copy the Lightdash PAT printed to the lightdash-deploy logs
# and set LIGHTDASH_API_KEY in .env, then restart vanna:
docker-compose restart vanna
```

**Service URLs (local):**

| Service | URL |
|---------|-----|
| Lightdash + Chat widget | http://localhost:8080 |
| Vanna API | http://localhost:8084 |
| Prefect UI | http://localhost:4200 |

## Environment variables

| Variable | Description |
|----------|-------------|
| `ANALYTICS_DB_USER` | Analytics DB username (default: `analytics`) |
| `ANALYTICS_DB_PASSWORD` | Analytics DB password |
| `ANALYTICS_DB_READONLY_PASSWORD` | Read-only user password for Lightdash |
| `LIGHTDASH_SECRET` | Lightdash JWT secret |
| `LIGHTDASH_EMAIL` | Lightdash admin email |
| `LIGHTDASH_PASSWORD` | Lightdash admin password |
| `LIGHTDASH_API_KEY` | Lightdash PAT (set after first boot) |
| `DEEPSEEK_API_KEY` | DeepSeek API key (SQL generation + agents) |
| `GEMINI_API_KEY` | Gemini API key (routing + DPM agent) |
| `VANNA_MODEL` | Vanna LLM model (default: `deepseek-chat`) |
| `ANALYTICS_PIPELINES_DIR` | Host path for dlt pipeline state |

## Deployment to Coolify (VPS)

1. Push repo to GitHub
2. In Coolify: new project → Docker Compose → point to `docker-compose.coolify.yml`
3. Set all environment variables from the table above
4. Add volume: `/var/run/docker.sock:/var/run/docker.sock` on the vanna service
5. Deploy — on first boot:
   - Copy the Lightdash PAT from `lightdash-deploy` container logs
   - Set `LIGHTDASH_API_KEY` in Coolify env vars
   - Restart the vanna service
6. Seed training data: exec into vanna container → `python train.py`

## Retraining Vanna

```bash
# Clear and reseed (run inside vanna container or via docker exec)
python train.py

# Or trigger via Prefect flow
prefect deployment run vanna-retrain/vanna-retrain
```
