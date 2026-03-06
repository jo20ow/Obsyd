"""
Price Provider — abstraction layer for commodity price data.

Routes requests to the configured primary provider,
falls back to the fallback provider on failure.

Config (from settings / runtime settings.json):
  PRICE_PROVIDER=yfinance  (primary — real futures, no API key)
  PRICE_FALLBACK=fred      (historical daily from FRED)
"""

import json
import logging
from pathlib import Path

from backend.config import settings
from backend.providers import alphavantage_provider, fred_provider, twelvedata_provider, yfinance_provider

logger = logging.getLogger(__name__)

SETTINGS_PATH = Path(__file__).parent.parent.parent / "data" / "settings.json"

PROVIDERS = {
    "yfinance": yfinance_provider,
    "twelvedata": twelvedata_provider,
    "alphavantage": alphavantage_provider,
    "fred": fred_provider,
}


def _load_runtime_settings() -> dict:
    """Load runtime settings from settings.json (overrides .env for provider choice)."""
    if SETTINGS_PATH.exists():
        try:
            return json.loads(SETTINGS_PATH.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def _save_runtime_settings(data: dict):
    """Persist runtime settings."""
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_PATH.write_text(json.dumps(data, indent=2))


def get_active_providers() -> tuple[str, str | None]:
    """Return (primary, fallback) provider names."""
    rt = _load_runtime_settings()
    primary = rt.get("price_provider") or settings.price_provider
    fallback = rt.get("price_fallback") or settings.price_fallback
    if fallback == primary:
        fallback = None
    return primary, fallback


def set_providers(primary: str, fallback: str | None = None):
    """Update runtime provider config."""
    if primary not in PROVIDERS:
        raise ValueError(f"Unknown provider: {primary}")
    if fallback and fallback not in PROVIDERS:
        raise ValueError(f"Unknown fallback: {fallback}")
    rt = _load_runtime_settings()
    rt["price_provider"] = primary
    rt["price_fallback"] = fallback
    _save_runtime_settings(rt)
    logger.info(f"Price provider changed: primary={primary}, fallback={fallback}")


async def get_live_prices() -> dict:
    """
    Live commodity futures prices.

    Primary: yfinance (CL=F, BZ=F, NG=F, GC=F, SI=F, HG=F) — no API key needed
    Fallback: FRED daily oil prices, then Alpha Vantage if configured
    """
    # Step 1: Try yfinance (all 6 commodities, real futures prices)
    try:
        yf_result = await yfinance_provider.get_live_prices()
        yf_prices = yf_result.get("prices", {})
        if len(yf_prices) >= 3:
            return {"available": True, "source": "yfinance", "prices": yf_prices}
    except Exception as e:
        logger.warning(f"yfinance failed: {e}")

    # Step 2: Fallback chain — FRED (daily), then AV, then TD
    commodity_prices = {}

    for provider_name in ("fred", "alphavantage"):
        provider = PROVIDERS.get(provider_name)
        if not provider:
            continue
        try:
            result = await provider.get_live_prices()
            p = result.get("prices", {})
            if p:
                commodity_prices = dict(p)
                source = provider_name
                break
        except Exception as e:
            logger.warning(f"Commodities from {provider_name} failed: {e}")
    else:
        source = None

    if settings.twelvedata_api_key and "GOLD" not in commodity_prices:
        try:
            td_result = await twelvedata_provider.get_live_prices()
            td_prices = td_result.get("prices", {})
            if "GOLD" in td_prices:
                commodity_prices["GOLD"] = td_prices["GOLD"]
        except Exception as e:
            logger.warning(f"Twelve Data gold failed: {e}")

    if not commodity_prices:
        return {"available": False, "source": None, "prices": {}}

    return {"available": True, "source": source or "fallback", "prices": commodity_prices}


async def get_intraday(symbol: str, interval: str = "15min", outputsize: int = 96) -> dict:
    """
    Fetch intraday data. Tries yfinance first (real futures),
    then falls back to Twelve Data (ETF proxies).
    """
    # yfinance: real futures intraday
    try:
        result = await yfinance_provider.get_intraday(symbol, interval, outputsize)
        if result.get("data"):
            return result
    except Exception as e:
        logger.warning(f"Intraday from yfinance failed: {e}")

    # Fallback: configured providers (twelvedata ETF proxies, etc.)
    primary, fallback = get_active_providers()
    for provider_name in (primary, fallback):
        if not provider_name or provider_name == "yfinance":
            continue
        provider = PROVIDERS.get(provider_name)
        if provider:
            try:
                result = await provider.get_intraday(symbol, interval, outputsize)
                if result.get("data"):
                    return result
            except Exception as e:
                logger.warning(f"Intraday from {provider_name} failed: {e}")

    return {"source": None, "symbol": symbol, "interval": interval, "data": []}


def get_settings() -> dict:
    """Return current provider configuration for the settings API."""
    primary, fallback = get_active_providers()
    credits = twelvedata_provider.get_credits_used()
    return {
        "price_provider": primary,
        "price_fallback": fallback,
        "available_providers": list(PROVIDERS.keys()),
        "yfinance_available": True,
        "twelvedata_credits": credits,
        "twelvedata_key_set": bool(settings.twelvedata_api_key),
        "alphavantage_key_set": bool(settings.alpha_vantage_api_key),
        "fred_key_set": bool(settings.fred_api_key),
    }
