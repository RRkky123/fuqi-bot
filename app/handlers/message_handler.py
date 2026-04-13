"""
處理使用者訊息：文字、圖片、位置
"""
import io
from datetime import date

import httpx
from loguru import logger
from linebot.v3.webhooks import (
    ImageMessageContent,
    LocationMessageContent,
    MessageEvent,
    TextMessageContent,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import DailyFreeLog, User
from app.services import weather_service
from app.services.image_service import generate_peace_image
from app.services.line_service import LineService, get_line_blob_api
from app.services.user_service import UserService
from app.utils.redis_client import (
    UserState,
    clear_user_state,
    get_user_state,
    get_user_state_extra,
    increment_daily_synthesis_count,
    set_user_state,
)

settings = get_settings()

# 縣市名稱清單（供文字輸入判斷）
VALID_CITIES = {
    "台北市", "臺北市", "新北市", "桃園市", "台中市", "臺中市",
    "台南市", "臺南市", "高雄市", "基隆市", "新竹市", "新竹縣",
    "苗栗縣", "彰化縣", "南投縣", "雲林縣", "嘉義市", "嘉義縣",
    "屏東縣", "宜蘭縣", "花蓮縣", "台東縣", "臺東縣", "澎湖縣",
    "金門縣", "連江縣",
}


async def handle_message(event: MessageEvent, db: AsyncSession) -> None:
    line_uid = event.source.user_id
    reply_token = event.reply_token
    msg = event.message

    user = await UserService.get_by_uid(db, line_uid)
    if not user:
        # 未在 DB 找到 → 自動建立（可能錯過 follow 事件）
        user, _ = await UserService.get_or_create(db, line_uid, "朋友")
        await db.commit()
        await set_user_state(line_uid, UserState.WAITING_AVATAR)
        messages = LineService.build_welcome_messages(user.display_name)
        await LineService.reply_messages(reply_token, messages)
        return

    state = await get_user_state(line_uid)

    # ── 圖片訊息：頭像上傳 ────────────────────────────────────
    if isinstance(msg, ImageMessageContent):
        await _handle_image_upload(event, db, user, state)
        return

    # ── 位置訊息：天氣查詢 ────────────────────────────────────
    if isinstance(msg, LocationMessageContent):
        if state in (UserState.WAITING_LOCATION, UserState.IDLE):
            await _handle_location(event, db, user, msg)
        return

    # ── 文字訊息 ──────────────────────────────────────────────
    if isinstance(msg, TextMessageContent):
        text = msg.text.strip()

        # 等待城市輸入
        if state == UserState.WAITING_CITY or text in VALID_CITIES:
            if text in VALID_CITIES:
                await _generate_peace_image_by_city(reply_token, db, user, text)
            else:
                await LineService.reply_messages(reply_token, [
                    LineService.build_location_request_message()
                ])
            return

        # 一般文字 → 引導到功能
        await LineService.reply_messages(reply_token, [
            LineService.build_location_request_message()
        ])


# ─────────────────────────────────────────────────────────────
# 圖片上傳（頭像）
# ─────────────────────────────────────────────────────────────

async def _handle_image_upload(event: MessageEvent, db: AsyncSession, user: User, state: str) -> None:
    line_uid = user.line_uid
    reply_token = event.reply_token

    # 取得圖片內容
    blob_api = get_line_blob_api()
    try:
        image_bytes = await blob_api.get_message_content(event.message.id)
    except Exception as e:
        logger.error(f"取得圖片失敗: {e}")
        await LineService.reply_messages(reply_token, [
            LineService.build_from_text("😔 圖片取得失敗，請再試一次。")
        ])
        return

    # 內容審核
    is_safe, reason = await UserService.moderate_avatar(image_bytes)
    if not is_safe:
        await LineService.reply_messages(reply_token, [
            _text_msg(f"⚠️ {reason}\n\n請重新上傳一張清晰的正面照片。")
        ])
        return

    # 上傳至 S3
    stored_url = await UserService.upload_avatar(line_uid, image_bytes)
    await UserService.save_avatar_url(db, line_uid, stored_url)

    # 處理邀請獎勵（頭像上傳 = 完成新戶註冊）
    from app.utils.redis_client import increment_daily_referral_count
    inviter_uid = await UserService.process_referral_reward(db, line_uid)
    if inviter_uid:
        # 通知邀請人
        await LineService.push_messages(inviter_uid, [
            _text_msg(f"🎉 您邀請的好友已完成頭像上傳！\n已獲得 1 張合成券 🎫")
        ])

    # 清除等待狀態
    await clear_user_state(line_uid)

    # 回覆：引導體驗 AI 合成
    await LineService.reply_messages(reply_token, [
        _text_msg(
            "📸 頭像已儲存！\n\n"
            "🎁 新手禮已自動發送 1 張免費合成券！\n\n"
            "現在要幫您體驗 AI 變身嗎？"
        ),
        LineService.build_theme_carousel(),
    ])

    logger.info(f"頭像上傳完成: {line_uid}")


# ─────────────────────────────────────────────────────────────
# 位置 → 天氣 → 平安圖
# ─────────────────────────────────────────────────────────────

async def _handle_location(
    event: MessageEvent,
    db: AsyncSession,
    user: User,
    location_msg: LocationMessageContent,
) -> None:
    lat = location_msg.latitude
    lon = location_msg.longitude
    city = weather_service.lat_lon_to_city(lat, lon)
    await _generate_peace_image_by_city(event.reply_token, db, user, city, lat=lat, lon=lon)


async def _generate_peace_image_by_city(
    reply_token: str,
    db: AsyncSession,
    user: User,
    city: str,
    lat: float = None,
    lon: float = None,
) -> None:
    line_uid = user.line_uid
    today = date.today()

    # 檢查今日已生成
    result = await db.execute(
        select(DailyFreeLog).where(
            DailyFreeLog.line_uid == line_uid,
            DailyFreeLog.date == today,
        )
    )
    if result.scalar_one_or_none():
        await LineService.reply_messages(reply_token, [
            LineService.build_already_generated_today_message()
        ])
        return

    # 取天氣
    weather = await weather_service.get_weather(lat=lat, lon=lon, city=city)

    # 生成平安圖
    img_bytes = generate_peace_image(weather, user.display_name)

    # 上傳圖片至 CDN
    image_url = await _upload_temp_image(img_bytes, line_uid, today.isoformat())

    # 寫入每日紀錄
    log = DailyFreeLog(
        line_uid=line_uid,
        date=today,
        weather_data=weather.to_dict(),
        location=city,
        result_url=image_url,
    )
    db.add(log)

    await clear_user_state(line_uid)

    # 回傳圖片
    await LineService.reply_messages(reply_token, LineService.build_peace_image_message(image_url))
    logger.info(f"平安圖生成: {line_uid} @ {city}")


async def _upload_temp_image(img_bytes: bytes, line_uid: str, date_str: str) -> str:
    """上傳平安圖：有 AWS 就上 S3，否則存本地靜態目錄透過 ngrok 回傳"""
    import boto3
    import os
    from app.config import get_settings
    settings = get_settings()

    if not settings.aws_access_key_id:
        # 開發模式：存到本地 static 目錄，透過 ngrok URL 回傳
        save_dir = f"static/images/daily/{date_str}"
        os.makedirs(save_dir, exist_ok=True)
        filename = f"{line_uid}.jpg"
        filepath = f"{save_dir}/{filename}"
        with open(filepath, "wb") as f:
            f.write(img_bytes)
        base_url = settings.public_base_url.rstrip("/")
        return f"{base_url}/static/images/daily/{date_str}/{filename}"

    s3 = boto3.client(
        "s3",
        aws_access_key_id=settings.aws_access_key_id,
        aws_secret_access_key=settings.aws_secret_access_key,
        region_name=settings.aws_region,
    )
    key = f"daily/{date_str}/{line_uid}.jpg"
    s3.put_object(
        Bucket=settings.aws_s3_bucket,
        Key=key,
        Body=img_bytes,
        ContentType="image/jpeg",
        ACL="public-read",
    )
    return f"{settings.cdn_base_url}/{key}"


def _text_msg(text: str):
    from linebot.v3.messaging import TextMessage
    return TextMessage(text=text)
