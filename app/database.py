"""
資料庫連線
- Railway 自動注入 DATABASE_URL（postgresql://...）→ 自動轉 asyncpg
- 無 DATABASE_URL 時 fallback 到 SQLite（本地開發）
"""
import os
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase


def _resolve_db_url() -> str:
    """
    解析最終的 DB URL：
    1. Railway 注入的 DATABASE_URL（postgresql://）→ 轉成 postgresql+asyncpg://
    2. 有設 DATABASE_URL 且已是 asyncpg 格式 → 直接用
    3. 都沒有 → SQLite（本地開發）
    """
    url = os.environ.get("DATABASE_URL", "")

    if url.startswith("postgresql://") or url.startswith("postgres://"):
        # Railway 給的格式，asyncpg 需要 postgresql+asyncpg://
        url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
        url = url.replace("postgres://", "postgresql+asyncpg://", 1)
        return url

    if url.startswith("postgresql+asyncpg://"):
        return url

    # 無 DB URL → SQLite fallback（開發 / Railway 尚未加 PostgreSQL 時）
    return "sqlite+aiosqlite:///./fuqi_dev.db"


DB_URL = _resolve_db_url()

_is_sqlite = "sqlite" in DB_URL

engine = create_async_engine(
    DB_URL,
    echo=False,
    **( {"connect_args": {"check_same_thread": False}} if _is_sqlite else
        {"pool_size": 5, "max_overflow": 10} )
)

AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


class Base(DeclarativeBase):
    pass


async def get_db() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def init_db():
    async with engine.begin() as conn:
        from app.models import Base as ModelBase  # noqa: F401
        await conn.run_sync(ModelBase.metadata.create_all)
