"""
資料庫模型（對應設計書「資料模型」章節）
"""
import uuid
from datetime import datetime, date

from sqlalchemy import (
    Boolean, Column, Date, DateTime, ForeignKey,
    Integer, JSON, Numeric, String, Text, func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.database import Base


def new_uuid() -> str:
    return str(uuid.uuid4())


# ─────────────────────────────────────────
# 使用者
# ─────────────────────────────────────────
class User(Base):
    __tablename__ = "users"

    line_uid        = Column(String(64), primary_key=True, index=True)
    display_name    = Column(String(100), nullable=False)
    avatar_url      = Column(Text, nullable=True)        # LINE 原始頭貼
    avatar_stored_url = Column(Text, nullable=True)      # 上傳後的 S3 URL（供合成用）
    credits         = Column(Integer, default=0, nullable=False)  # 剩餘合成券
    total_earned    = Column(Integer, default=0, nullable=False)  # 累積獲得
    referrer_uid    = Column(String(64), ForeignKey("users.line_uid"), nullable=True)
    is_blocked      = Column(Boolean, default=False)
    is_blacklisted  = Column(Boolean, default=False)
    registered_at   = Column(DateTime(timezone=True), server_default=func.now())
    updated_at      = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    # 關聯
    daily_free_logs  = relationship("DailyFreeLog", back_populates="user")
    synthesis_jobs   = relationship("SynthesisJob", back_populates="user")
    transactions     = relationship("Transaction", back_populates="user")
    referrals_sent   = relationship("Referral", foreign_keys="Referral.inviter_uid", back_populates="inviter")
    referrals_received = relationship("Referral", foreign_keys="Referral.invitee_uid", back_populates="invitee")


# ─────────────────────────────────────────
# 每日免費平安圖紀錄
# ─────────────────────────────────────────
class DailyFreeLog(Base):
    __tablename__ = "daily_free_log"

    id           = Column(UUID(as_uuid=False), primary_key=True, default=new_uuid)
    line_uid     = Column(String(64), ForeignKey("users.line_uid"), nullable=False, index=True)
    date         = Column(Date, nullable=False, index=True)
    generated_at = Column(DateTime(timezone=True), server_default=func.now())
    weather_data = Column(JSON, nullable=True)
    location     = Column(String(100), nullable=True)
    result_url   = Column(Text, nullable=True)

    user = relationship("User", back_populates="daily_free_logs")


# ─────────────────────────────────────────
# AI 合成任務
# ─────────────────────────────────────────
class SynthesisJob(Base):
    __tablename__ = "synthesis_jobs"

    job_id       = Column(UUID(as_uuid=False), primary_key=True, default=new_uuid)
    line_uid     = Column(String(64), ForeignKey("users.line_uid"), nullable=False, index=True)
    theme_id     = Column(String(50), ForeignKey("themes.theme_id"), nullable=False)
    status       = Column(String(20), default="pending")  # pending / processing / done / failed / refunded
    result_url   = Column(Text, nullable=True)
    cost_credits = Column(Integer, default=1)
    created_at   = Column(DateTime(timezone=True), server_default=func.now())
    completed_at = Column(DateTime(timezone=True), nullable=True)
    error_msg    = Column(Text, nullable=True)

    user  = relationship("User", back_populates="synthesis_jobs")
    theme = relationship("Theme")


# ─────────────────────────────────────────
# 邀請裂變
# ─────────────────────────────────────────
class Referral(Base):
    __tablename__ = "referrals"

    id           = Column(UUID(as_uuid=False), primary_key=True, default=new_uuid)
    inviter_uid  = Column(String(64), ForeignKey("users.line_uid"), nullable=False, index=True)
    invitee_uid  = Column(String(64), ForeignKey("users.line_uid"), nullable=False, unique=True)
    is_valid     = Column(Boolean, default=False)   # 被邀請者完成頭像上傳後才 True
    rewarded_at  = Column(DateTime(timezone=True), nullable=True)
    created_at   = Column(DateTime(timezone=True), server_default=func.now())

    inviter = relationship("User", foreign_keys=[inviter_uid], back_populates="referrals_sent")
    invitee = relationship("User", foreign_keys=[invitee_uid], back_populates="referrals_received")


# ─────────────────────────────────────────
# 金流交易
# ─────────────────────────────────────────
class Transaction(Base):
    __tablename__ = "transactions"

    tx_id          = Column(UUID(as_uuid=False), primary_key=True, default=new_uuid)
    line_uid       = Column(String(64), ForeignKey("users.line_uid"), nullable=False, index=True)
    amount         = Column(Numeric(10, 2), nullable=False)  # NT$
    credits_added  = Column(Integer, nullable=False)
    package_type   = Column(String(20), nullable=False)  # single / bundle_10 / subscription
    newebpay_token = Column(String(200), nullable=True)
    status         = Column(String(20), default="pending")  # pending / success / failed / refunded
    created_at     = Column(DateTime(timezone=True), server_default=func.now())
    paid_at        = Column(DateTime(timezone=True), nullable=True)

    user = relationship("User", back_populates="transactions")


# ─────────────────────────────────────────
# 合成主題
# ─────────────────────────────────────────
class Theme(Base):
    __tablename__ = "themes"

    theme_id    = Column(String(50), primary_key=True)
    name        = Column(String(50), nullable=False)
    preview_url = Column(Text, nullable=True)
    category    = Column(String(30), nullable=False)  # 風景名勝 / 花卉祝福 / 財富好運 / 節慶特輯
    is_active   = Column(Boolean, default=True)
    is_seasonal = Column(Boolean, default=False)
    price_credits = Column(Integer, default=1)
    sort_order  = Column(Integer, default=0)
    created_at  = Column(DateTime(timezone=True), server_default=func.now())
