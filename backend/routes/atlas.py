"""ATLAS API — per-country data for the map/globe layer (read-only, ungated)."""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from backend.database import get_db
from backend.models.atlas import CountryEnergy, CountryMacro, CountryResource

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


@router.get("/macro")
def atlas_macro(
    metric: str = Query("gdp_usd", description="gdp_usd / gdp_per_capita / gdp_growth / industry_pct_gdp / manufacturing_pct_gdp / trade_pct_gdp / population / inflation"),
    db: Session = Depends(get_db),
):
    """Latest-year value per country for a World Bank macro indicator.

    Descriptive context (official reported annual figures, lagging — see `as_of`); only true
    countries (aggregates excluded at ingest). Joins the energy layer on ISO-3.
    """
    rows = db.query(CountryMacro).filter(CountryMacro.metric == metric).all()
    latest: dict[str, CountryMacro] = {}
    for r in rows:
        cur = latest.get(r.iso3)
        if cur is None or r.period > cur.period:
            latest[r.iso3] = r

    items = [
        {"iso3": r.iso3, "country_name": r.country_name, "value": r.value, "period": r.period}
        for r in latest.values()
    ]
    items.sort(key=lambda x: x["value"], reverse=True)
    return {
        "metric": metric,
        "source": "World Bank Open Data (CC BY 4.0)",
        "as_of": max((r["period"] for r in items), default=None),
        "coverage": len(items),
        "countries": items,
    }


@router.get("/resources")
def atlas_resources(
    commodity: str = Query("lithium", description="lithium / gold / iron_ore / rare_earths / cobalt / copper / nickel / bauxite / zinc / potash"),
    db: Session = Depends(get_db),
):
    """Latest-year mine production per country for a mineral commodity (USGS MCS).

    Descriptive (official reported annual production, lagging — see `as_of`); only true
    countries (USGS aggregates excluded at ingest). Coverage is the producing countries USGS
    lists — non-producers/unlisted are simply absent, not zero.
    """
    rows = db.query(CountryResource).filter(CountryResource.commodity == commodity).all()
    latest: dict[str, CountryResource] = {}
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
        "commodity": commodity,
        "source": "USGS Mineral Commodity Summaries (public domain)",
        "as_of": max((r["period"] for r in items), default=None),
        "coverage": len(items),
        "countries": items,
    }
