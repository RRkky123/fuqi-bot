"""
Celery 任務定義
"""
import asyncio
from datetime import datetime
from typing import Optional

from loguru import logger

from workers.celery_app import celery_app


def run_async(coro):
    """在同步 Celery worker 中執行 async 函式"""
    return asyncio.get_event_loop().run_until_complete(coro)


# ─────────────────────────────────────────
# AI 合成任務
# ─────────────────────────────────────────
@celery_app.task(bind=True, max_retries=2, default_retry_delay=10)
def run_ai_synthesis(self, job_id: str, line_uid: str, avatar_url: str, theme_id: str):
    """
    非同步執行 AI 合成，完成後推送 LINE 訊息。
    """
    logger.info(f"[Celery] AI合成開始: job_id={job_id}, uid={line_uid}, theme={theme_id}")
    try:
        result_url = run_async(_do_synthesis(job_id, line_uid, avatar_url, theme_id))
        if result_url:
            run_async(_push_synthesis_result(line_uid, job_id, result_url))
        else:
            run_async(_push_synthesis_failed(line_uid, job_id))
    except Exception as exc:
        logger.error(f"[Celery] AI合成失敗: {exc}")
        run_async(_push_synthesis_failed(line_uid, job_id, str(exc)))
        raise self.retry(exc=exc)


async def _do_synthesis(job_id: str, line_uid: str, avatar_url: str, theme_id: str) -> Optional[str]:
    from app.config import get_settings
    from app.database import AsyncSessionLocal
    from app.models import SynthesisJob
    from app.services.ai_service import AISynthesisService
    from sqlalchemy import update

    settings = get_settings()

    async with AsyncSessionLocal() as db:
        # 更新狀態為 processing
        await db.execute(
            update(SynthesisJob)
            .where(SynthesisJob.job_id == job_id)
            .values(status="processing")
        )
        await db.commit()

    # 呼叫 AI API
    prediction_id = await AISynthesisService.start_synthesis(job_id, avatar_url, theme_id)
    result_url = await AISynthesisService.poll_result(prediction_id, settings.synthesis_timeout_sec)

    async with AsyncSessionLocal() as db:
        if result_url:
            await db.execute(
                update(SynthesisJob)
                .where(SynthesisJob.job_id == job_id)
                .values(status="done", result_url=result_url, completed_at=datetime.utcnow())
            )
        else:
            # 退還合成券
            from app.services.user_service import UserService
            await UserService.add_credits(db, line_uid, 1, reason="AI合成失敗退券")
            await db.execute(
                update(SynthesisJob)
                .where(SynthesisJob.job_id == job_id)
                .values(status="refunded", error_msg="合成逾時或失敗")
            )
        await db.commit()

    return result_url


async def _push_synthesis_result(line_uid: str, job_id: str, result_url: str):
    from app.services.line_service import LineService
    messages = LineService.build_synthesis_result_messages(result_url, job_id)
    await LineService.push_messages(line_uid, messages)


async def _push_synthesis_failed(line_uid: str, job_id: str, error: str = ""):
    from app.services.line_service import LineService
    messages = LineService.build_synthesis_failed_messages()
    await LineService.push_messages(line_uid, messages)


# ─────────────────────────────────────────
# 每日早安推播（排程任務）
# ─────────────────────────────────────────
@celery_app.task
def send_daily_morning_push():
    """
    每日早上 8 點提醒有剩餘點數的使用者來領平安圖。
    注意：LINE API 推播按則計費，需控制量。
    """
    run_async(_do_morning_push())


async def _do_morning_push():
    from app.database import AsyncSessionLocal
    from app.models import User
    from app.services.line_service import LineService
    from sqlalchemy import select

    logger.info("[排程] 每日早安推播開始")
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(User.line_uid, User.display_name, User.credits)
            .where(User.is_blocked == False, User.is_blacklisted == False)  # noqa: E712
            .limit(500)  # 控制推播量
        )
        users = result.fetchall()

    count = 0
    for uid, name, credits in users:
        try:
            msg = LineService.build_morning_push_message(name, credits)
            await LineService.push_messages(uid, [msg])
            count += 1
        except Exception as e:
            logger.warning(f"推播失敗 {uid}: {e}")

    logger.info(f"[排程] 早安推播完成，共推送 {count} 人")
