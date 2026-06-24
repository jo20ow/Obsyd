"""ATLAS API — per-country data for the map/globe layer (read-only, ungated)."""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from backend.database import get_db
from backend.models.atlas import CountryEnergy

router = APIRouter(prefix="/api/atlas", tags=["atlas"])


@router.get("/energy")
def atlas_energy(
    product: str = Query("petroleum"),
    activity: str = Query("production"),
    db: Session = Depends(get_db),
):
    """Latest-year value per country for an energy product/activity (EIA International).

    Descriptive, honest: values are official reported annual figures (lagging — see `as_of`);
    only true countries are present (regional aggregates excluded at ingest). Feeds the
    upcoming country choropleth (join on ISO-3).
    """
    rows = (
        db.query(CountryEnergy)
        .filter(CountryEnergy.product == product, CountryEnergy.activity == activity)
        .all()
    )
    latest: dict[str, CountryEnergy] = {}
    for r in rows:
        cur = latest.get(r.iso3)
        if cur is None or r.period > cur.period:
            latest[r.iso3] = r

    items = [
        {"iso3": r.iso3, "country_name": r.country_name, "value": r.value, "unit": r.unit, "period": r.period}
        for r in latest.values()
    ]
    items.sort(key=lambda x: x["value"], reverse=True)
    return {
        "product": product,
        "activity": activity,
        "source": "EIA International Energy Statistics (public domain)",
        "as_of": max((r["period"] for r in items), default=None),
        "coverage": len(items),
        "countries": items,
    }
