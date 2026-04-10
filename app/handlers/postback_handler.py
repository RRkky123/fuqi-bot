"""
處理 Postback 事件（Rich Menu 按鈕、Quick Reply 按鈕等）
"""
from urllib.parse import parse_qs

from loguru import logger
from linebot.v3.webhooks import PostbackEvent
from linebot.v3.messaging import TextMessage
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.services.line_service import LineService
from app.services.user_service import UserService
from app.utils.redis_client import UserState, get_daily_synthesis_count, set_user_state

settings = get_settings()


async def handle_postback(event: PostbackEvent, db: AsyncSession) -> None:
    line_uid = event.source.user_id
    reply_token = event.reply_token
    data = parse_qs(event.postback.data)

    action = data.get("action", [""])[0]

    user = await UserService.get_by_uid(db, line_uid)
    if not user:
        await LineService.reply_messages(reply_token, [
            TextMessage(text="請先重新加入好友 🙏")
        ])
        return

    # ── 今日平安圖 ─────────────────────────────────────────────
    if action == "daily_peace_image":
        await _handle_daily_peace_image(reply_token, line_uid, user)

    # ── AI 合成開始 ────────────────────────────────────────────
    elif action == "start_ai_synthesis":
        await _handle_start_synthesis(reply_token, db, user)

    # ── 主題選擇 ───────────────────────────────────────────────
    elif action == "select_theme":
        theme_id = data.get("theme_id", ["spring_sakura"])[0]
        await _handle_theme_selected(reply_token, db, user, theme_id)

    # ── 邀請好友 ───────────────────────────────────────────────
    elif action == "share_referral":
        await _handle_share_referral(reply_token, user)

    # ── 我的點數 ───────────────────────────────────────────────
    elif action == "my_credits":
        await _handle_my_credits(reply_token, user)

    # ── 聯絡客服 ───────────────────────────────────────────────
    elif action == "contact_support":
        await _handle_contact_support(reply_token)

    else:
        logger.warning(f"未知 postback action: {action}, data={event.postback.data}")


# ─────────────────────────────────────────────────────────────

async def _handle_daily_peace_image(reply_token: str, line_uid: str, user) -> None:
    """觸發每日平安圖流程"""
    from app.utils.redis_client import set_user_state
    await set_user_state(line_uid, UserState.WAITING_LOCATION)
    await LineService.reply_messages(reply_token, [
        LineService.build_location_request_message()
    ])


async def _handle_start_synthesis(reply_token: str, db: AsyncSession, user) -> None:
    """開始 AI 合成流程"""
    # 確認頭像已上傳
    if not user.avatar_stored_url:
        await LineService.reply_messages(reply_token, [
            LineService.build_need_avatar_message()
        ])
        return

    # 確認點數
    if user.credits <= 0:
        liff_url = f"https://liff.line.me/{settings.liff_id}"
        await LineService.reply_messages(reply_token, [
            LineService.build_insufficient_credits_message(liff_url)
        ])
        return

    # 確認每日合成上限（成本控制）
    daily_count = await get_daily_synthesis_count()
    if daily_count >= settings.max_daily_synthesis:
        await LineService.reply_messages(reply_token, [
            TextMessage(text=(
                "😅 今日 AI 合成名額已滿...\n\n"
                "⏰ 明天早上 8 點重新開放，\n"
                "請明天再來試試！"
            ))
        ])
        return

    # 顯示主題選擇
    await LineService.reply_messages(reply_token, [
        TextMessage(text="✨ 請選擇您喜歡的合成主題 👇"),
        LineService.build_theme_carousel(),
    ])


async def _handle_theme_selected(reply_token: str, db: AsyncSession, user, theme_id: str) -> None:
    """使用者選擇主題，扣除點數並啟動 Celery 任務"""
    from app.models import SynthesisJob
    from app.utils.redis_client import increment_daily_synthesis_count

    # 再次確認點數（防 race condition）
    success, remaining = await UserService.deduct_credits(db, user.line_uid)
    if not success:
        liff_url = f"https://liff.line.me/{settings.liff_id}"
        await LineService.reply_messages(reply_token, [
            LineService.build_insufficient_credits_message(liff_url)
        ])
        return

    # 建立 Job 記錄
    job = SynthesisJob(
        line_uid=user.line_uid,
        theme_id=theme_id,
        status="pending",
        cost_credits=1,
    )
    db.add(job)
    await db.flush()  # 取得 job_id

    await increment_daily_synthesis_count()

    # 啟動 Celery 非同步任務
    from workers.tasks import run_ai_synthesis
    run_ai_synthesis.delay(
        job_id=str(job.job_id),
        line_uid=user.line_uid,
        avatar_url=user.avatar_stored_url,
        theme_id=theme_id,
    )

    # 立即回覆（LINE Webhook 5 秒限制）
    await LineService.reply_messages(reply_token, [
        LineService.build_synthesis_processing_message()
    ])
    logger.info(f"AI合成任務送出: job={job.job_id}, uid={user.line_uid}, theme={theme_id}")


async def _handle_share_referral(reply_token: str, user) -> None:
    """產生邀請連結"""
    referral_link = UserService.build_referral_link(user.line_uid, settings.liff_id)
    await LineService.reply_messages(reply_token, [
        LineService.build_referral_message(user.display_name, referral_link)
    ])


async def _handle_my_credits(reply_token: str, user) -> None:
    """查詢點數"""
    liff_url = f"https://liff.line.me/{settings.liff_id}"
    await LineService.reply_messages(reply_token, [
        LineService.build_wallet_flex(user.display_name, user.credits, liff_url)
    ])


async def _handle_contact_support(reply_token: str) -> None:
    """客服選單"""
    await LineService.reply_messages(reply_token, [
        TextMessage(
            text=(
                "💬 客服中心\n\n"
                "常見問題：\n"
                "• 合成券怎麼用？\n"
                "  → 點「AI 變身」並選擇主題即可！\n\n"
                "• 合成失敗怎麼辦？\n"
                "  → 系統會自動退回點數，請重新嘗試。\n\n"
                "• 如何退款？\n"
                "  → 未使用的套組可在 7 日內申請退款\n"
                "     請 Email 至 support@fuqibot.tw\n\n"
                "📧 support@fuqibot.tw"
            )
        )
    ])
