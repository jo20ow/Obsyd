from pydantic_settings import BaseSettings
from typing import Optional


class Settings(BaseSettings):
    # Database
    database_url: str = "sqlite:///./obsyd.db"

    # EIA (Public Domain, no key required but recommended)
    eia_api_key: Optional[str] = None
    eia_base_url: str = "https://api.eia.gov/v2"

    # FRED (Public Domain)
    fred_api_key: Optional[str] = None
    fred_base_url: str = "https://api.stlouisfed.org/fred"

    # BYOK APIs
    finnhub_api_key: Optional[str] = None
    alpha_vantage_api_key: Optional[str] = None

    # AIS
    aisstream_api_key: Optional[str] = None
    aishub_username: Optional[str] = None
    aishub_api_key: Optional[str] = None

    # NASA FIRMS (BYOK)
    firms_api_key: Optional[str] = None

    # LLM (BYOK)
    openai_api_key: Optional[str] = None
    anthropic_api_key: Optional[str] = None

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
