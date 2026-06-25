from typing import Optional

from pydantic import SecretStr
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Deployment environment: "development" or "production".
    # In production, default secrets cause a hard startup failure.
    environment: str = "development"

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

    # GIE (AGSI gas storage + ALSI LNG) — one free key covers both
    gie_api_key: Optional[SecretStr] = None

    # ENTSO-E Transparency Platform (gas power burn). Free, but the token is
    # granted manually: register at transparency.entsoe.eu, then email
    # transparency@entsoe.eu to request "Restful API" access.
    entsoe_api_token: Optional[SecretStr] = None
    # CCGT fleet-average electrical efficiency for power-burn → gas conversion.
    # ~0.50 is the EU fleet average; ±5% systematic error. Configurable.
    gas_ccgt_efficiency: float = 0.50

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

    # TEMP paywall kill-switch. When true, require_pro is bypassed for every
    # request and all Pro endpoints return data. Reversible: flip back to false
    # (env: DISABLE_PRO_GATE). Pair with frontend VITE_DISABLE_PROGATE=1 to also
    # drop the visual ProGate overlay.
    disable_pro_gate: bool = False

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}


settings = Settings()
