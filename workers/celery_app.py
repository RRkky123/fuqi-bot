"""
Celery 非同步任務：AI 合成、每日推播
LINE Webhook 必須在 5 秒內回 200，耗時操作全部走這裡
"""
import asyncio
from datetime import datetime

from celery import Celery
from loguru import logger

from app.config import get_settings

settings = get_settings()

celery_app = Celery(
    "fuqi_bot",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=["workers.tasks"],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="Asia/Taipei",
    enable_utc=True,
    task_track_started=True,
    task_acks_late=True,       # 任務完成後才 ack，避免重啟遺失
    worker_prefetch_multiplier=1,
    # 排程任務（每日推播）
    beat_schedule={
        "daily-morning-push": {
            "task": "workers.tasks.send_daily_morning_push",
            "schedule": 3600 * 8,  # 每天早上 8 點（需搭配 crontab 設定）
        },
    },
)
