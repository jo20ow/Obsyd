"""Unified physical-energy situation endpoint.

GET /api/situation — molecules (oil chokepoints) + gas balance + electrons (power
grid) fused into one descriptive top-line. Public, read-only, descriptive.
"""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from backend.database import get_db
from backend.situation.physical import build_physical_situation

router = APIRouter(prefix="/api", tags=["situation"])


@router.get("/situation")
async def get_physical_situation(db: Session = Depends(get_db)):
    return build_physical_situation(db)
