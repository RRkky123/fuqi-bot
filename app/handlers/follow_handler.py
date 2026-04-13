"""
處理 follow / unfollow 事件
"""
from loguru import logger
from linebot.v3.webhooks import FollowEvent, UnfollowEvent
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import User, Referral
from app.services.line_service import LineService
from app.services.user_service import UserService
from app.utils.redis_client import set_user_state, UserState

settings = get_settings()


async def handle_follow(event: FollowEvent, db: AsyncSession) -> None:
    """新使用者加好友"""
    line_uid = event.source.user_id
    reply_token = event.reply_token

    # 取得 LINE Profile
    api = LineService.get_parser()
    try:
        line_api = LineService.__class__  # 取用 static methods
        display_name = "朋友"  # 預設值，實際從 profile API 取
    except Exception:
        display_name = "朋友"

    # 解析邀請碼（ref 參數，從 LINE 加好友 URL 傳入）
    referrer_uid = None
    # LINE follow event 不直接攜帶 ref，實際需透過 LIFF 或 Postback 傳遞
    # 此處預留邏輯，搭配 referral 系統

    # 建立或更新使用者
    user, is_new = await UserService.get_or_create(
        db=db,
        line_uid=line_uid,
        display_name=display_name,
        referrer_uid=referrer_uid,
    )

    # 新用戶贈送 3 張免費合成券（測試期間免費）
    if is_new:
        await UserService.add_credits(db, line_uid, 3, reason="新用戶贈送（測試期）")
        await db.commit()

    # 設定狀態：等待上傳頭像
    await set_user_state(line_uid, UserState.WAITING_AVATAR)

    # 發送歡迎訊息
    messages = LineService.build_welcome_messages(user.display_name)
    await LineService.reply_messages(reply_token, messages)

    logger.info(f"{'新用戶' if is_new else '舊用戶回歸'}: {line_uid} ({display_name})")


async def handle_unfollow(event: UnfollowEvent, db: AsyncSession) -> None:
    """使用者封鎖"""
    from sqlalchemy import update
    line_uid = event.source.user_id
    await db.execute(
        update(User)
        .where(User.line_uid == line_uid)
        .values(is_blocked=True)
    )
    logger.info(f"使用者封鎖: {line_uid}")
