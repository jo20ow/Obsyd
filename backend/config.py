from typing import Optional

from pydantic import SecretStr
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Deployment environment: "development" or "production".
    # In production, default secrets cause a hard startup failure.
    environment: str = "development"

    # Database
    database_url: str = "sqlite:///./obsyd.db"

    # Process role: "api" (serve requests only, scheduler OFF), "ingest" (run the
    # scheduler/collectors as the sole DB writer), or "all" (both — single process).
    # Splitting into two systemd units (obsyd = api, obsyd-ingest = ingest) lets the
    # API scale workers without double-firing crons and keeps heavy ingest off the
    # request loop. Default "all" = the current single-process behavior (byte-identical).
    obsyd_role: str = "all"

    # Enabled bidding zones (comma-separated keys from ZONE_REGISTRY in
    # backend/power/zones.py). Adding a zone is config-only. The code default stays
    # lean (3 zones) for fast dev/CI; PRODUCTION enables the broader European core via
    # the ENABLED_ZONES env var in .env, e.g.:
    #   ENABLED_ZONES=DE_LU,FR,NL,BE,AT,ES,PT,PL,CZ
    # (all with clean Energy-Charts flow mappings + well-covered ENTSO-E data).
    enabled_zones: str = "DE_LU,FR,NL"

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

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}


settings = Settings()
