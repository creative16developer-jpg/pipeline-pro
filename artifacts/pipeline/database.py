from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase
import os
import ssl
from urllib.parse import urlparse, urlunparse, parse_qs, urlencode


def _build_engine_url(raw_url: str) -> tuple[str, dict]:
    """
    Convert a standard postgres:// URL to an asyncpg-compatible URL.
    asyncpg does not accept 'sslmode' as a query param — we strip it out
    and pass ssl context via connect_args instead.
    """
    if not raw_url:
        raise RuntimeError("DATABASE_URL is not set")

    url = raw_url
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql+asyncpg://", 1)
    elif url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+asyncpg://", 1)

    parsed = urlparse(url)
    qs = parse_qs(parsed.query)

    ssl_mode = qs.pop("sslmode", ["disable"])[0]
    connect_args: dict = {}

    if ssl_mode in ("require", "verify-ca", "verify-full"):
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        connect_args["ssl"] = ctx

    new_query = urlencode({k: v[0] for k, v in qs.items()})
    clean_parsed = parsed._replace(query=new_query)
    clean_url = urlunparse(clean_parsed)

    return clean_url, connect_args


_raw_url = os.environ.get("DATABASE_URL", "")
_url, _connect_args = _build_engine_url(_raw_url)

engine = create_async_engine(
    _url,
    echo=False,
    pool_size=5,
    max_overflow=10,
    connect_args=_connect_args,
)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


class Base(DeclarativeBase):
    pass


async def get_db():
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()
