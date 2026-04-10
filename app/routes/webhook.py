"""
LINE Webhook 接收端點
設計原則：5 秒內回 200，實際業務邏輯全部非同步
"""
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.webhooks import (
    FollowEvent,
    MessageEvent,
    PostbackEvent,
    UnfollowEvent,
)
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.handlers.follow_handler import handle_follow, handle_unfollow
from app.handlers.message_handler import handle_message
from app.handlers.postback_handler import handle_postback
from app.services.line_service import get_parser

router = APIRouter()


@router.post("/webhook")
async def webhook(
    request: Request,
    x_line_signature: str = Header(None),
    db: AsyncSession = Depends(get_db),
):
    """
    LINE Webhook 主入口
    """
    body = await request.body()

    # 驗證簽章
    parser = get_parser()
    try:
        events = parser.parse(body.decode("utf-8"), x_line_signature)
    except InvalidSignatureError:
        logger.warning("LINE Webhook 簽章驗證失敗")
        raise HTTPException(status_code=400, detail="Invalid signature")
    except Exception as e:
        logger.error(f"Webhook 解析失敗: {e}")
        raise HTTPException(status_code=400, detail="Parse error")

    # 處理事件（必須快速，避免超過 5 秒）
    for event in events:
        try:
            if isinstance(event, FollowEvent):
                await handle_follow(event, db)

            elif isinstance(event, UnfollowEvent):
                await handle_unfollow(event, db)

            elif isinstance(event, MessageEvent):
                await handle_message(event, db)

            elif isinstance(event, PostbackEvent):
                await handle_postback(event, db)

            else:
                logger.debug(f"忽略事件類型: {type(event).__name__}")

        except Exception as e:
            # 個別事件失敗不影響整體回應
            logger.error(f"事件處理失敗 {type(event).__name__}: {e}", exc_info=True)

    return {"status": "ok"}
