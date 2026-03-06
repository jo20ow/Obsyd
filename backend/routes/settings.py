"""
Settings API — runtime provider configuration.
"""

from fastapi import APIRouter
from pydantic import BaseModel

from backend.providers import price_provider

router = APIRouter(prefix="/api/settings", tags=["settings"])


class ProviderUpdate(BaseModel):
    primary: str
    fallback: str | None = None


@router.get("")
async def get_settings():
    """Get current provider configuration."""
    return price_provider.get_settings()


@router.post("/provider")
async def set_provider(body: ProviderUpdate):
    """Change the active price provider."""
    try:
        price_provider.set_providers(body.primary, body.fallback)
        return {"status": "ok", **price_provider.get_settings()}
    except ValueError as e:
        return {"status": "error", "message": str(e)}


@router.get("/credits")
async def get_credits():
    """Get Twelve Data credit usage for today."""
    from backend.providers.twelvedata_provider import get_credits_used
    return get_credits_used()
