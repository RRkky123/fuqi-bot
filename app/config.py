"""
福氣天天領 — 全域設定
"""
from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    # LINE Bot
    line_channel_access_token: str = ""
    line_channel_secret: str = ""

    # LIFF
    liff_id: str = ""
    liff_base_url: str = "https://your-domain.com"

    # Database
    database_url: str = "postgresql+asyncpg://fuqi:fuqi_secret@localhost:5432/fuqi_bot"
    database_url_sync: str = "postgresql://fuqi:fuqi_secret@localhost:5432/fuqi_bot"

    # Redis
    redis_url: str = "redis://localhost:6379/0"

    # AWS
    aws_access_key_id: str = ""
    aws_secret_access_key: str = ""
    aws_region: str = "ap-northeast-1"
    aws_s3_bucket: str = "fuqi-bot-images"
    cdn_base_url: str = ""

    # 天氣 API
    cwb_api_key: str = ""
    openweathermap_api_key: str = ""

    # AI 合成
    replicate_api_token: str = ""
    remaker_api_key: str = ""

    # 藍新金流
    newebpay_merchant_id: str = ""
    newebpay_hash_key: str = ""
    newebpay_hash_iv: str = ""
    newebpay_api_url: str = "https://ccore.newebpay.com/MPG/mpg_gateway"

    # App
    app_env: str = "development"
    app_secret_key: str = "change-me-in-production"
    base_url: str = "https://your-domain.com"

    # 業務參數
    daily_free_limit: int = 1
    credit_price_single: int = 10
    credit_price_bundle_10: int = 80
    credit_expiry_days: int = 180
    max_daily_synthesis: int = 500
    max_daily_referrals: int = 10
    synthesis_timeout_sec: int = 90

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()
