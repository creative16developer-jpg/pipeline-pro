"""
Run this once to create all database tables.
Usage:
    cd artifacts/pipeline
    set DATABASE_URL=postgresql://postgres:YOUR_PASSWORD@localhost:5432/pipeline_pro
    python create_tables.py
"""
import asyncio
from dotenv import load_dotenv
load_dotenv()

from database import engine, Base
import models.models  # noqa: F401 — registers all models


async def main():
    print("Creating tables...")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    print("Done! All tables created successfully.")
    await engine.dispose()


asyncio.run(main())
