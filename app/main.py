"""
福氣天天領 — FastAPI 主應用程式入口
"""
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from loguru import logger

from app.config import get_settings

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """啟動 / 關閉生命週期"""
    logger.info(f"🌸 福氣天天領啟動中... (env={settings.app_env})")

    # 確保必要目錄存在
    for d in ["static/images/daily", "static/images/avatars", "templates", "fonts"]:
        os.makedirs(d, exist_ok=True)

    # 初始化資料庫
    try:
        from app.database import init_db
        await init_db()
        logger.info("✅ 資料庫初始化完成")
    except Exception as e:
        logger.warning(f"資料庫初始化警告（繼續啟動）: {e}")

    # 寫入預設主題
    try:
        await _seed_themes()
        logger.info("✅ 主題資料種子完成")
    except Exception as e:
        logger.warning(f"主題種子警告: {e}")

    logger.info(f"✅ 服務啟動完成！公開 URL: {settings.public_base_url}")
    yield
    logger.info("🌸 福氣天天領關閉中...")


app = FastAPI(
    title="福氣天天領 API",
    description="LINE AI 長輩圖機器人後端服務",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://liff.line.me", "*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 路由
from app.routes import webhook, liff  # noqa: E402
app.include_router(webhook.router, tags=["LINE Webhook"])
app.include_router(liff.router, tags=["LIFF 購買"])

# 靜態檔案
try:
    app.mount("/static", StaticFiles(directory="static"), name="static")
except Exception:
    pass


@app.get("/health")
async def health_check():
    from app.database import DB_URL
    return {
        "status": "ok",
        "service": "福氣天天領",
        "version": "1.0.0",
        "env": settings.app_env,
        "db": "postgresql" if "postgresql" in DB_URL else "sqlite",
        "public_url": settings.public_base_url,
    }


async def _seed_themes():
    from app.database import AsyncSessionLocal
    from app.models import Theme
    from sqlalchemy import select

    themes_data = [
        {"theme_id": "tokyo_tower",        "name": "🗼 東京鐵塔",   "category": "風景名勝", "sort_order": 1},
        {"theme_id": "paris_tower",        "name": "🗼 巴黎鐵塔",   "category": "風景名勝", "sort_order": 2},
        {"theme_id": "kyoto_street",       "name": "🏯 京都古道",   "category": "風景名勝", "sort_order": 3},
        {"theme_id": "spring_sakura",      "name": "🌸 花見春日",   "category": "花卉祝福", "sort_order": 4},
        {"theme_id": "flower_bloom",       "name": "🌺 花開富貴",   "category": "花卉祝福", "sort_order": 5},
        {"theme_id": "fortune_god",        "name": "🌟 財神到府",   "category": "財富好運", "sort_order": 6},
        {"theme_id": "bamboo_forest",      "name": "🎋 竹林清幽",   "category": "風景名勝", "sort_order": 7},
        {"theme_id": "hawaii_beach",       "name": "🌊 夏威夷海灘", "category": "風景名勝", "sort_order": 8},
        {"theme_id": "new_year_fireworks", "name": "🎆 跨年煙火",   "category": "節慶特輯", "sort_order": 9,  "is_seasonal": True},
        {"theme_id": "lunar_new_year",     "name": "🧧 農曆新年",   "category": "節慶特輯", "sort_order": 10, "is_seasonal": True},
        {"theme_id": "mooncake_festival",  "name": "🎑 中秋賞月",   "category": "節慶特輯", "sort_order": 11, "is_seasonal": True},
        {"theme_id": "cloud_sea",          "name": "☁️ 天空雲海",   "category": "風景名勝", "sort_order": 12},
    ]

    async with AsyncSessionLocal() as db:
        for t in themes_data:
            result = await db.execute(select(Theme).where(Theme.theme_id == t["theme_id"]))
            if not result.scalar_one_or_none():
                db.add(Theme(**t))
        await db.commit()
