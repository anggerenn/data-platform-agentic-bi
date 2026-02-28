from fastapi import FastAPI
from routers import ask, dashboard, chat
from fastapi.middleware.cors import CORSMiddleware


app = FastAPI(title="Agentic BI - AI Service", version="0.1.0")

app.include_router(ask.router)
app.include_router(dashboard.router)
app.include_router(chat.router)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
@app.get("/health")
def health():
    return {"status": "ok"}