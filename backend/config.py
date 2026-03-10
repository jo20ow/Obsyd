from typing import Optional

from pydantic import SecretStr
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Database
    database_url: str = "sqlite:///./obsyd.db"

    # EIA (Public Domain, no key required but recommended)
    eia_api_key: Optional[SecretStr] = None
    eia_base_url: str = "https://api.eia.gov/v2"

    # FRED (Public Domain)
    fred_api_key: Optional[SecretStr] = None
    fred_base_url: str = "https://api.stlouisfed.org/fred"

    # Price provider
    price_provider: str = "yfinance"
    price_fallback: str = "fred"
    twelvedata_api_key: Optional[SecretStr] = None

    # BYOK APIs
    alpha_vantage_api_key: Optional[SecretStr] = None

    # AIS
    aisstream_api_key: Optional[SecretStr] = None
    aishub_username: Optional[str] = None
    aishub_api_key: Optional[SecretStr] = None

    # NASA FIRMS (BYOK)
    firms_api_key: Optional[SecretStr] = None

    # App secret (for HMAC tokens)
    secret_key: SecretStr = SecretStr("obsyd-change-me-in-production")

    # Resend (email)
    resend_api_key: Optional[SecretStr] = None

    # Lemon Squeezy (payments)
    lemonsqueezy_webhook_secret: Optional[SecretStr] = None
    lemonsqueezy_checkout_url: str = "https://obsyd.lemonsqueezy.com/buy/placeholder"

    # JWT
    jwt_secret: SecretStr = SecretStr("obsyd-jwt-change-me-in-production")
    jwt_expiry_days: int = 30

    # LLM (BYOK)
    openai_api_key: Optional[SecretStr] = None
    anthropic_api_key: Optional[SecretStr] = None
    finnhub_api_key: Optional[SecretStr] = None

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}


settings = Settings()
