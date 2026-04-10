"""
資料庫連線（開發模式自動切換 SQLite，正式環境用 PostgreSQL）
"""
import os

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.config import get_settings

settings = get_settings()

# 開發模式：自動用 SQLite（不需要 Docker）
if settings.app_env == "development" and "postgresql" not in settings.database_url:
    DB_URL = "sqlite+aiosqlite:///./fuqi_dev.db"
elif settings.app_env == "development":
    # 若 DATABASE_URL 明確設了 postgres，但連不上時 fallback
    DB_URL = settings.database_url
else:
    DB_URL = settings.database_url

engine = create_async_engine(
    DB_URL,
    echo=False,
    connect_args={"check_same_thread": False} if "sqlite" in DB_URL else {},
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
