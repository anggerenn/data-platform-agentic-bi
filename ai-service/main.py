from fastapi import FastAPI
from routers import ask, dashboard, chat

app = FastAPI(title="Agentic BI - AI Service", version="0.1.0")

app.include_router(ask.router)
app.include_router(dashboard.router)
app.include_router(chat.router)

@app.get("/health")
def health():
    return {"status": "ok"}