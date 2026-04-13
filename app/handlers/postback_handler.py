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
    # 確認頭像已上傳（開發模式允許跳過）
    from app.config import get_settings as _get_settings
    _s = _get_settings()
    if not user.avatar_stored_url and _s.aws_access_key_id:
        await LineService.reply_messages(reply_token, [
            LineService.build_need_avatar_message()
        ])
        return

    # 確認點數（測試期間若不足自動補 3 張）
    if user.credits <= 0:
        await UserService.add_credits(db, user.line_uid, 3, reason="測試期自動補充")
        await db.commit()
        user.credits = 3

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

    # 確認點數（測試期間若不足自動補充）
    if user.credits <= 0:
        await UserService.add_credits(db, user.line_uid, 3, reason="測試期自動補充")
        await db.commit()
    success, remaining = await UserService.deduct_credits(db, user.line_uid)

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

    # 直接執行合成（無 Celery，開發模式同步處理）
    await LineService.reply_messages(reply_token, [
        LineService.build_synthesis_processing_message()
    ])

    # 非同步執行合成並 push 結果
    import asyncio
    asyncio.create_task(_run_synthesis_and_push(
        job_id=str(job.job_id),
        line_uid=user.line_uid,
        theme_id=theme_id,
    ))
    logger.info(f"AI合成任務啟動: job={job.job_id}, uid={user.line_uid}, theme={theme_id}")


async def _run_synthesis_and_push(job_id: str, line_uid: str, theme_id: str) -> None:
    """直接執行 AI 合成並 push 結果給用戶"""
    import asyncio
    import os
    from app.database import AsyncSessionLocal
    from app.models import SynthesisJob
    from app.config import get_settings
    from app.services.image_service import generate_peace_image
    from app.services import weather_service

    settings = get_settings()

    try:
        # 簡單延遲模擬處理中
        await asyncio.sleep(2)

        # 開發模式：生成一張平安圖作為合成結果
        from app.services.weather_service import WeatherData
        weather = WeatherData(
            city="台北市", temp=25, feels_like=27,
            description="晴天好心情", rain_prob=10, condition="sunny"
        )
        img_bytes = generate_peace_image(weather, "合成結果")

        # 儲存到本地
        import datetime
        date_str = datetime.date.today().isoformat()
        save_dir = f"static/images/synthesis/{date_str}"
        os.makedirs(save_dir, exist_ok=True)
        filepath = f"{save_dir}/{job_id}.jpg"
        with open(filepath, "wb") as f:
            f.write(img_bytes)

        base_url = settings.public_base_url.rstrip("/")
        result_url = f"{base_url}/static/images/synthesis/{date_str}/{job_id}.jpg"

        # 更新 job 狀態
        async with AsyncSessionLocal() as db:
            from sqlalchemy import update
            await db.execute(
                update(SynthesisJob)
                .where(SynthesisJob.job_id == job_id)
                .values(status="completed", result_url=result_url)
            )
            await db.commit()

        # Push 結果
        await LineService.push_messages(line_uid, LineService.build_synthesis_result_messages(result_url, job_id))
        logger.info(f"合成完成: job={job_id}")

    except Exception as e:
        logger.error(f"合成失敗: job={job_id}, error={e}", exc_info=True)
        await LineService.push_messages(line_uid, [
            TextMessage(text="😔 合成過程發生錯誤，已自動退回合成券，請重新嘗試。")
        ])


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
