"""
LINE Messaging API 封裝
- Reply / Push 訊息
- 各種 Flex Message 樣板（設計書規格）
"""
from typing import Optional

import httpx
from linebot.v3 import WebhookParser
from linebot.v3.messaging import (
    AsyncApiClient,
    AsyncMessagingApi,
    AsyncMessagingApiBlob,
    Configuration,
    FlexBubble,
    FlexBox,
    FlexButton,
    FlexImage,
    FlexMessage,
    FlexText,
    ImageMessage,
    Message,
    PostbackAction,
    QuickReply,
    QuickReplyItem,
    ReplyMessageRequest,
    PushMessageRequest,
    TextMessage,
    URIAction,
)
from loguru import logger

from app.config import get_settings

settings = get_settings()

# LINE API 設定
_configuration = Configuration(access_token=settings.line_channel_access_token)
_parser = WebhookParser(settings.line_channel_secret)


def get_line_api() -> AsyncMessagingApi:
    client = AsyncApiClient(_configuration)
    return AsyncMessagingApi(client)


def get_line_blob_api() -> AsyncMessagingApiBlob:
    client = AsyncApiClient(_configuration)
    return AsyncMessagingApiBlob(client)


def get_parser() -> WebhookParser:
    return _parser


class LineService:

    # ─────────────────────────────────────────
    # 推播訊息
    # ─────────────────────────────────────────

    @staticmethod
    async def push_messages(line_uid: str, messages: list[Message]) -> None:
        api = get_line_api()
        try:
            await api.push_message(PushMessageRequest(to=line_uid, messages=messages[:5]))
        except Exception as e:
            logger.error(f"推播失敗 {line_uid}: {e}")

    @staticmethod
    async def reply_messages(reply_token: str, messages: list[Message]) -> None:
        api = get_line_api()
        try:
            await api.reply_message(ReplyMessageRequest(reply_token=reply_token, messages=messages[:5]))
        except Exception as e:
            logger.error(f"回覆失敗: {e}")

    # ─────────────────────────────────────────
    # 歡迎訊息（加好友）
    # ─────────────────────────────────────────

    @staticmethod
    def build_welcome_messages(display_name: str) -> list[Message]:
        text = TextMessage(
            text=(
                f"🎉 歡迎 {display_name}！\n\n"
                "我是「福氣天天領」機器人 🌸\n"
                "每天為您送上專屬平安圖，帶入當地天氣與您的名字！\n\n"
                "📌 請先上傳一張您的美照\n"
                "👉 上傳後立即送您 1 張免費 AI 合成券！\n\n"
                "（請直接傳送照片給我 📸）"
            )
        )
        return [text]

    # ─────────────────────────────────────────
    # 平安圖相關
    # ─────────────────────────────────────────

    @staticmethod
    def build_location_request_message() -> Message:
        return TextMessage(
            text="🌤️ 要幫您合成今日平安圖囉！\n\n請傳送您的位置（點選下方「傳送位置資訊」），讓我抓取當地天氣 ☁️\n\n若不方便分享位置，請直接輸入縣市名稱（例如：台北市）",
            quick_reply=QuickReply(
                items=[
                    QuickReplyItem(
                        action=URIAction(
                            label="📍 傳送位置資訊",
                            uri="line://nv/location",
                        )
                    ),
                ]
            ),
        )

    @staticmethod
    def build_peace_image_message(image_url: str) -> list[Message]:
        img_msg = ImageMessage(
            original_content_url=image_url,
            preview_image_url=image_url,
        )
        text_msg = TextMessage(
            text="🌸 今日平安圖已送達！\n祝您今天平安健康、一切順心 🙏",
            quick_reply=QuickReply(
                items=[
                    QuickReplyItem(
                        action=PostbackAction(
                            label="📤 分享賺點數",
                            data="action=share_referral",
                        )
                    ),
                    QuickReplyItem(
                        action=PostbackAction(
                            label="✨ AI 變身照 $10",
                            data="action=start_ai_synthesis",
                        )
                    ),
                ]
            ),
        )
        return [img_msg, text_msg]

    @staticmethod
    def build_already_generated_today_message() -> Message:
        return TextMessage(
            text=(
                "今天的平安圖已經領過囉 🌸\n\n"
                "明天早上再來領下一張！\n\n"
                "想要更特別的體驗？試試「✨ AI 變身照」，\n"
                "把您的頭像合成到世界名勝 📸"
            ),
            quick_reply=QuickReply(
                items=[
                    QuickReplyItem(
                        action=PostbackAction(
                            label="✨ AI 變身照 $10",
                            data="action=start_ai_synthesis",
                        )
                    ),
                ]
            ),
        )

    # ─────────────────────────────────────────
    # AI 合成相關
    # ─────────────────────────────────────────

    @staticmethod
    def build_theme_carousel() -> Message:
        """Carousel 主題選擇卡片"""
        # 實際部署時從 DB 讀取主題清單，這裡預設 4 個示範
        themes = [
            {"id": "spring_sakura", "name": "🌸 花見春日",    "desc": "在絕美的日本櫻花樹下留影"},
            {"id": "tokyo_tower",   "name": "🗼 東京鐵塔",    "desc": "東京地標，旅遊必拍留念"},
            {"id": "flower_bloom",  "name": "🌺 花開富貴",    "desc": "花海中的幸福時光"},
            {"id": "fortune_god",   "name": "🌟 財神到府",    "desc": "財神爺來拜訪，好運連連"},
            {"id": "lunar_new_year","name": "🧧 農曆新年",    "desc": "喜氣洋洋，恭喜發財"},
            {"id": "cloud_sea",     "name": "☁️ 天空雲海",    "desc": "雲端之上，夢幻仙境"},
        ]

        # Flex Message Carousel
        bubbles = []
        for theme in themes:
            bubble = {
                "type": "bubble",
                "body": {
                    "type": "box",
                    "layout": "vertical",
                    "contents": [
                        {"type": "text", "text": theme["name"], "weight": "bold", "size": "lg"},
                        {"type": "text", "text": theme["desc"], "size": "sm", "color": "#888888", "wrap": True},
                    ],
                },
                "footer": {
                    "type": "box",
                    "layout": "vertical",
                    "contents": [
                        {
                            "type": "button",
                            "action": {
                                "type": "postback",
                                "label": "選擇這個主題",
                                "data": f"action=select_theme&theme_id={theme['id']}",
                            },
                            "style": "primary",
                            "color": "#D62B2B",
                        }
                    ],
                },
            }
            bubbles.append(bubble)

        return FlexMessage(
            alt_text="請選擇 AI 合成主題",
            contents={"type": "carousel", "contents": bubbles},
        )

    @staticmethod
    def build_synthesis_processing_message() -> Message:
        return TextMessage(
            text="✨ 已收到！AI 正在幫您合成中...\n\n⏱️ 預計需要約 30 秒，請稍候 🙏\n合成完成後會立即傳給您！"
        )

    @staticmethod
    def build_synthesis_result_messages(result_url: str, job_id: str) -> list[Message]:
        img_msg = ImageMessage(
            original_content_url=result_url,
            preview_image_url=result_url,
        )
        text_msg = TextMessage(
            text="🎉 AI 變身照完成！\n快分享給好友，讓大家看看您的帥氣/美麗！",
            quick_reply=QuickReply(
                items=[
                    QuickReplyItem(
                        action=PostbackAction(label="✨ 再做一張", data="action=start_ai_synthesis")
                    ),
                    QuickReplyItem(
                        action=PostbackAction(label="📤 分享賺點數", data="action=share_referral")
                    ),
                ]
            ),
        )
        return [img_msg, text_msg]

    @staticmethod
    def build_synthesis_failed_messages() -> list[Message]:
        return [
            TextMessage(
                text=(
                    "😔 很抱歉，這次合成遇到一點問題...\n\n"
                    "✅ 合成券已退還\n\n"
                    "請稍後再試一次，或聯絡客服。"
                ),
                quick_reply=QuickReply(
                    items=[
                        QuickReplyItem(
                            action=PostbackAction(label="再試一次", data="action=start_ai_synthesis")
                        ),
                        QuickReplyItem(
                            action=PostbackAction(label="聯絡客服", data="action=contact_support")
                        ),
                    ]
                ),
            )
        ]

    # ─────────────────────────────────────────
    # 點數錢包 Flex Message
    # ─────────────────────────────────────────

    @staticmethod
    def build_wallet_flex(display_name: str, credits: int, liff_url: str) -> Message:
        credit_color = "#D4AC0D" if credits > 0 else "#E74C3C"
        credit_text = str(credits)

        bubble = {
            "type": "bubble",
            "body": {
                "type": "box",
                "layout": "vertical",
                "backgroundColor": "#FFF8E7",
                "contents": [
                    {"type": "text", "text": "🎫 我的點數錢包", "weight": "bold", "size": "lg", "color": "#633806"},
                    {"type": "separator", "margin": "md"},
                    {
                        "type": "box",
                        "layout": "vertical",
                        "margin": "lg",
                        "contents": [
                            {"type": "text", "text": "剩餘合成券", "size": "sm", "color": "#888888"},
                            {
                                "type": "box",
                                "layout": "horizontal",
                                "contents": [
                                    {
                                        "type": "text",
                                        "text": credit_text,
                                        "size": "5xl",
                                        "weight": "bold",
                                        "color": credit_color,
                                        "flex": 1,
                                    },
                                    {"type": "text", "text": "張", "size": "lg", "color": "#888888", "align": "end"},
                                ],
                            },
                        ],
                    },
                    {
                        "type": "text",
                        "text": f"合成券自獲得日起 180 天有效",
                        "size": "xs",
                        "color": "#AAAAAA",
                        "margin": "md",
                    },
                ],
            },
            "footer": {
                "type": "box",
                "layout": "vertical",
                "contents": [
                    {
                        "type": "button",
                        "action": {
                            "type": "postback",
                            "label": "立即使用",
                            "data": "action=start_ai_synthesis",
                        },
                        "style": "primary",
                        "color": "#D62B2B",
                    } if credits > 0 else {
                        "type": "button",
                        "action": {
                            "type": "uri",
                            "label": "購買合成券",
                            "uri": liff_url,
                        },
                        "style": "primary",
                        "color": "#8E44AD",
                    },
                ],
            },
        }

        return FlexMessage(alt_text=f"您目前有 {credits} 張合成券", contents=bubble)

    # ─────────────────────────────────────────
    # 邀請好友
    # ─────────────────────────────────────────

    @staticmethod
    def build_referral_message(display_name: str, referral_link: str) -> Message:
        text = (
            f"🧧 邀請好友，一起領福氣！\n\n"
            f"您的專屬邀請連結：\n{referral_link}\n\n"
            "📌 好友加入並上傳頭像後\n"
            "👉 您立即獲得 1 張免費合成券！\n\n"
            "快分享給家人朋友吧 🎉"
        )
        return TextMessage(text=text)

    # ─────────────────────────────────────────
    # 每日早安推播
    # ─────────────────────────────────────────

    @staticmethod
    def build_morning_push_message(display_name: str, credits: int) -> Message:
        credit_info = f"您目前有 {credits} 張合成券可用 🎫" if credits > 0 else ""
        return TextMessage(
            text=(
                f"☀️ 早安，{display_name}！\n\n"
                "今天的專屬平安圖已經準備好了 🌸\n"
                f"{credit_info}\n\n"
                "點選「今日平安圖」開始領取！"
            )
        )

    # ─────────────────────────────────────────
    # 通用
    # ─────────────────────────────────────────

    @staticmethod
    def build_need_avatar_message() -> Message:
        return TextMessage(
            text=(
                "📸 請先上傳一張您的美照！\n\n"
                "上傳後系統會幫您儲存，\n"
                "之後 AI 合成就能用您的臉啦 😊\n\n"
                "（直接傳送照片給我就可以了）"
            )
        )

    @staticmethod
    def build_insufficient_credits_message(liff_url: str) -> Message:
        return TextMessage(
            text=(
                "😅 您的合成券不足...\n\n"
                "💡 購買方案：\n"
                "• 單張：NT$10\n"
                "• 10 張套組：NT$80（省 NT$20！）\n\n"
                "點下方按鈕立即購買 👇"
            ),
            quick_reply=QuickReply(
                items=[
                    QuickReplyItem(
                        action=URIAction(label="💳 購買合成券", uri=liff_url)
                    ),
                    QuickReplyItem(
                        action=PostbackAction(label="📤 邀請好友賺點數", data="action=share_referral")
                    ),
                ]
            ),
        )
