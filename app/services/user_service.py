"""
使用者服務：註冊、頭像管理、點數操作
"""
from datetime import datetime, timedelta
from typing import Optional

import boto3
import httpx
from loguru import logger
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import Referral, User
from app.utils.redis_client import get_user_state, set_user_state, UserState

settings = get_settings()


class UserService:

    # ─────────────────────────────────────────
    # 查詢 / 建立使用者
    # ─────────────────────────────────────────

    @staticmethod
    async def get_or_create(
        db: AsyncSession,
        line_uid: str,
        display_name: str,
        avatar_url: str = "",
        referrer_uid: Optional[str] = None,
    ) -> tuple[User, bool]:
        """
        取得或建立使用者。
        回傳 (user, is_new)
        """
        result = await db.execute(select(User).where(User.line_uid == line_uid))
        user = result.scalar_one_or_none()
        if user:
            # 更新顯示名稱與頭貼（LINE 可能異動）
            user.display_name = display_name
            user.avatar_url = avatar_url
            if user.is_blocked:
                user.is_blocked = False  # 重新加好友
            return user, False

        user = User(
            line_uid=line_uid,
            display_name=display_name,
            avatar_url=avatar_url,
            referrer_uid=referrer_uid,
            credits=0,
        )
        db.add(user)
        await db.flush()
        logger.info(f"新使用者註冊: {line_uid} ({display_name})")
        return user, True

    @staticmethod
    async def get_by_uid(db: AsyncSession, line_uid: str) -> Optional[User]:
        result = await db.execute(select(User).where(User.line_uid == line_uid))
        return result.scalar_one_or_none()

    # ─────────────────────────────────────────
    # 頭像上傳至 S3
    # ─────────────────────────────────────────

    @staticmethod
    async def upload_avatar(line_uid: str, image_bytes: bytes, content_type: str = "image/jpeg") -> str:
        """
        上傳頭像至 S3，回傳 CDN URL。
        若 AWS 未設定，回傳空字串（開發模式）。
        """
        if not settings.aws_access_key_id:
            logger.warning("AWS 未設定，跳過頭像上傳（開發模式）")
            return ""

        s3 = boto3.client(
            "s3",
            aws_access_key_id=settings.aws_access_key_id,
            aws_secret_access_key=settings.aws_secret_access_key,
            region_name=settings.aws_region,
        )
        key = f"avatars/{line_uid}.jpg"
        s3.put_object(
            Bucket=settings.aws_s3_bucket,
            Key=key,
            Body=image_bytes,
            ContentType=content_type,
            ACL="public-read",
        )
        url = f"{settings.cdn_base_url}/{key}"
        logger.info(f"頭像上傳完成: {line_uid} -> {url}")
        return url

    @staticmethod
    async def save_avatar_url(db: AsyncSession, line_uid: str, stored_url: str) -> None:
        await db.execute(
            update(User)
            .where(User.line_uid == line_uid)
            .values(avatar_stored_url=stored_url)
        )

    # ─────────────────────────────────────────
    # 內容安全審核
    # ─────────────────────────────────────────

    @staticmethod
    async def moderate_avatar(image_bytes: bytes) -> tuple[bool, str]:
        """
        使用 AWS Rekognition 審核頭像。
        回傳 (is_safe, reason)
        若 AWS 未設定，預設通過（開發模式）。
        """
        if not settings.aws_access_key_id:
            return True, ""

        rek = boto3.client(
            "rekognition",
            aws_access_key_id=settings.aws_access_key_id,
            aws_secret_access_key=settings.aws_secret_access_key,
            region_name=settings.aws_region,
        )

        # 1. 人臉數量檢查
        face_resp = rek.detect_faces(
            Image={"Bytes": image_bytes},
            Attributes=["DEFAULT"],
        )
        faces = face_resp.get("FaceDetails", [])
        high_conf_faces = [f for f in faces if f.get("Confidence", 0) >= 90]

        if len(high_conf_faces) == 0:
            return False, "未偵測到清晰人臉，請上傳含正面人臉的照片。"
        if len(high_conf_faces) > 1:
            return False, "偵測到多張人臉，請上傳只有您一個人的照片。"

        # 2. SafeSearch 審核
        mod_resp = rek.detect_moderation_labels(
            Image={"Bytes": image_bytes},
            MinConfidence=70,
        )
        blocked = {"Explicit Nudity", "Violence", "Visually Disturbing"}
        for label in mod_resp.get("ModerationLabels", []):
            if label.get("ParentName") in blocked or label.get("Name") in blocked:
                return False, "圖片內容不符合使用規範，請重新上傳。"

        return True, ""

    # ─────────────────────────────────────────
    # 點數操作
    # ─────────────────────────────────────────

    @staticmethod
    async def add_credits(db: AsyncSession, line_uid: str, amount: int, reason: str = "") -> int:
        """增加合成券，回傳新餘額"""
        result = await db.execute(select(User).where(User.line_uid == line_uid))
        user = result.scalar_one_or_none()
        if not user:
            raise ValueError(f"使用者不存在: {line_uid}")
        user.credits += amount
        user.total_earned += amount
        logger.info(f"點數增加: {line_uid} +{amount}（{reason}），餘額={user.credits}")
        return user.credits

    @staticmethod
    async def deduct_credits(db: AsyncSession, line_uid: str, amount: int = 1) -> tuple[bool, int]:
        """
        扣除合成券。
        回傳 (success, remaining_credits)
        """
        result = await db.execute(select(User).where(User.line_uid == line_uid))
        user = result.scalar_one_or_none()
        if not user or user.credits < amount:
            return False, user.credits if user else 0
        user.credits -= amount
        return True, user.credits

    # ─────────────────────────────────────────
    # 邀請裂變
    # ─────────────────────────────────────────

    @staticmethod
    def build_referral_link(line_uid: str, liff_id: str) -> str:
        """產生個人邀請連結"""
        return f"https://line.me/R/ti/p/@your_bot_id?ref={line_uid}"

    @staticmethod
    async def process_referral_reward(db: AsyncSession, invitee_uid: str) -> Optional[str]:
        """
        被邀請者完成頭像上傳後，獎勵邀請人。
        回傳邀請人 UID（若存在且尚未獎勵）。
        """
        # 查找尚未獎勵的 referral 記錄
        result = await db.execute(
            select(Referral).where(
                Referral.invitee_uid == invitee_uid,
                Referral.is_valid == False,  # noqa: E712
            )
        )
        referral = result.scalar_one_or_none()
        if not referral:
            return None

        # 標記為已獎勵
        referral.is_valid = True
        referral.rewarded_at = datetime.utcnow()

        # 給邀請人加 1 張券
        await UserService.add_credits(db, referral.inviter_uid, 1, reason="邀請好友獎勵")
        logger.info(f"邀請獎勵: {referral.inviter_uid} 邀請了 {invitee_uid}，獲得 1 張券")
        return referral.inviter_uid
