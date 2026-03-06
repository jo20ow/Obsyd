"""
Price Provider — abstraction layer for commodity price data.

Routes requests to the configured primary provider,
falls back to the fallback provider on failure.

Config (from settings / runtime settings.json):
  PRICE_PROVIDER=twelvedata   (primary)
  PRICE_FALLBACK=alphavantage (on failure)
"""

import json
import logging
from pathlib import Path

from backend.config import settings
from backend.providers import alphavantage_provider, fred_provider, twelvedata_provider

logger = logging.getLogger(__name__)

SETTINGS_PATH = Path(__file__).parent.parent.parent / "data" / "settings.json"

PROVIDERS = {
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
    Fetch live prices using a hybrid approach:
    - Alpha Vantage provides real commodity prices (WTI $/bbl, Brent, NG)
    - Twelve Data provides Gold spot (XAU/USD) and ETF proxies for metals
    - FRED provides daily fallback for WTI/Brent

    Energy prices (WTI, BRENT, NG) always come from AV or FRED (real prices).
    Metals (GOLD, SILVER, COPPER) come from Twelve Data when available.
    """
    primary, fallback = get_active_providers()

    # Try primary provider first
    provider = PROVIDERS.get(primary)
    primary_result = None
    if provider:
        try:
            primary_result = await provider.get_live_prices()
        except Exception as e:
            logger.warning(f"Primary provider {primary} failed: {e}")

    # Try fallback if primary didn't deliver
    fallback_result = None
    if fallback and (not primary_result or not primary_result.get("prices")):
        provider = PROVIDERS.get(fallback)
        if provider:
            try:
                fallback_result = await provider.get_live_prices()
            except Exception as e:
                logger.warning(f"Fallback provider {fallback} failed: {e}")

    # Merge: use the best result, enrich with Twelve Data metals if available
    best = primary_result if primary_result and primary_result.get("prices") else fallback_result
    if not best or not best.get("prices"):
        return {"available": False, "source": None, "prices": {}}

    prices = dict(best.get("prices", {}))
    source = best.get("source", "unknown")

    # If primary source is NOT twelvedata, try to enrich with TD metals
    if source != "twelvedata" and settings.twelvedata_api_key:
        try:
            td_result = await twelvedata_provider.get_live_prices()
            td_prices = td_result.get("prices", {})
            # Add Gold (real spot price from XAU/USD)
            if "GOLD" in td_prices:
                prices["GOLD"] = td_prices["GOLD"]
            # Add ETF proxies for metals panel
            for key in ("SILVER_ETF", "COPPER_ETF"):
                if key in td_prices:
                    prices[key] = td_prices[key]
        except Exception as e:
            logger.warning(f"Twelve Data enrichment failed: {e}")

    return {"available": True, "source": source, "prices": prices}


async def get_intraday(symbol: str, interval: str = "15min", outputsize: int = 96) -> dict:
    """
    Fetch intraday data from the primary provider.
    Only Twelve Data supports this; others return empty data.
    """
    primary, fallback = get_active_providers()

    provider = PROVIDERS.get(primary)
    if provider:
        try:
            result = await provider.get_intraday(symbol, interval, outputsize)
            if result.get("data"):
                return result
        except Exception as e:
            logger.warning(f"Intraday from {primary} failed: {e}")

    if fallback:
        provider = PROVIDERS.get(fallback)
        if provider:
            try:
                return await provider.get_intraday(symbol, interval, outputsize)
            except Exception as e:
                logger.warning(f"Intraday fallback {fallback} failed: {e}")

    return {"source": None, "symbol": symbol, "interval": interval, "data": []}


def get_settings() -> dict:
    """Return current provider configuration for the settings API."""
    primary, fallback = get_active_providers()
    credits = twelvedata_provider.get_credits_used()
    return {
        "price_provider": primary,
        "price_fallback": fallback,
        "available_providers": list(PROVIDERS.keys()),
        "twelvedata_credits": credits,
        "twelvedata_key_set": bool(settings.twelvedata_api_key),
        "alphavantage_key_set": bool(settings.alpha_vantage_api_key),
        "fred_key_set": bool(settings.fred_api_key),
    }
