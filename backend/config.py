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

    # LLM (BYOK)
    openai_api_key: Optional[SecretStr] = None
    anthropic_api_key: Optional[SecretStr] = None
    finnhub_api_key: Optional[SecretStr] = None

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}


settings = Settings()
