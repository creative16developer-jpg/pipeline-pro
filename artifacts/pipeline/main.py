"""
PipelinePro — Python FastAPI backend.

Replaces the Node.js/Express API server.
All endpoints match the existing OpenAPI contract so the React
dashboard works without changes.

Run:
    uvicorn main:app --host 0.0.0.0 --port $PORT --reload
"""

import os
from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from config import get_settings
from routers import dashboard, stores, products, jobs, sunsky

settings = get_settings()

app = FastAPI(
    title="PipelinePro API",
    description="WooCommerce import pipeline — Python backend",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(dashboard.router, prefix="/api")
app.include_router(stores.router, prefix="/api")
app.include_router(products.router, prefix="/api")
app.include_router(jobs.router, prefix="/api")
app.include_router(sunsky.router, prefix="/api")


@app.get("/api/healthz")
async def health():
    return {"status": "ok", "runtime": "python"}


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", settings.port))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=True)
