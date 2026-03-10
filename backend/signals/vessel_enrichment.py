"""
Vessel Enrichment — Extract metadata from AIS static data.

Uses ShipStaticData messages from AISStream and AISHub vessel rows
to populate VesselRegistry with dimensions, IMO, and computed fields
(ship_class, estimated DWT).

All data comes from the existing AIS data stream — zero API cost.
"""

import logging
from datetime import datetime, timezone

from backend.database import SessionLocal
from backend.models.vessels import VesselRegistry

logger = logging.getLogger(__name__)

# Ship class thresholds based on LOA (Length Overall)
# These are approximate — better than nothing when DWT is unknown
CLASS_THRESHOLDS = [
    (300, "VLCC"),
    (250, "Suezmax"),
    (220, "Aframax"),
    (180, "Panamax"),
    (0, "MR/Handysize"),
]

# Block coefficient for tanker DWT estimation
TANKER_BLOCK_COEFF = 0.82


def classify_by_dimensions(length: float | None, beam: float | None) -> str | None:
    """Classify vessel by length (LOA). Returns class name or None."""
    if not length or length <= 0:
        return None
    for threshold, cls_name in CLASS_THRESHOLDS:
        if length >= threshold:
            return cls_name
    return "MR/Handysize"


def estimate_dwt(length: float | None, beam: float | None, draft: float | None) -> float | None:
    """Estimate DWT from dimensions using block coefficient method.

    DWT ~ Length x Beam x Draft x BlockCoefficient
    If draft unknown, estimate from length regression: Draft ~ 0.055 * Length
    """
    if not length or length <= 0 or not beam or beam <= 0:
        return None

    if not draft or draft <= 0:
        # Regression estimate for tanker draft
        draft = 0.055 * length

    dwt = length * beam * draft * TANKER_BLOCK_COEFF
    return round(dwt, 0)


def _detailed_type_name(ship_type: int) -> str | None:
    """Map AIS ship_type to descriptive name."""
    type_map = {
        80: "Tanker",
        81: "Tanker (Hazardous A)",
        82: "Tanker (Hazardous B)",
        83: "Tanker (Hazardous C)",
        84: "Tanker (Hazardous D)",
        85: "Tanker",
        86: "Tanker",
        87: "Tanker",
        88: "Tanker",
        89: "Tanker (No additional info)",
    }
    return type_map.get(ship_type)


def upsert_vessel_registry(
    mmsi: str,
    *,
    ship_name: str | None = None,
    ship_type: int | None = None,
    imo: str | None = None,
    length: float | None = None,
    beam: float | None = None,
    draft: float | None = None,
    destination: str | None = None,
    flag_state: str | None = None,
    db=None,
):
    """Upsert vessel metadata into VesselRegistry.

    Called from AISStream (ShipStaticData) and AISHub (poll).
    Caller can pass an existing db session, or we create one.
    """
    own_db = db is None
    if own_db:
        db = SessionLocal()

    try:
        existing = db.query(VesselRegistry).filter(VesselRegistry.mmsi == mmsi).first()

        if existing:
            # Update fields if new values are available
            if ship_name and ship_name.strip():
                existing.ship_name = ship_name.strip()
            if ship_type and ship_type > 0:
                existing.ship_type = ship_type
                existing.ship_type_detailed = _detailed_type_name(ship_type)
            if imo and imo.strip() and imo.strip() != "0":
                existing.imo = imo.strip()
            if length and length > 0:
                existing.length = length
            if beam and beam > 0:
                existing.beam = beam
            if draft and draft > 0:
                existing.draft = draft
            if destination and destination.strip():
                existing.destination = destination.strip()
            if flag_state and flag_state.strip():
                existing.flag_state = flag_state.strip()

            # Recompute derived fields
            eff_length = existing.length
            eff_beam = existing.beam
            eff_draft = existing.draft

            dim_class = classify_by_dimensions(eff_length, eff_beam)
            if dim_class:
                existing.ship_class = dim_class

            est_dwt = estimate_dwt(eff_length, eff_beam, eff_draft)
            if est_dwt:
                existing.dwt = est_dwt
                existing.dwt_estimated = not (eff_draft and eff_draft > 0)

            existing.last_updated = datetime.now(timezone.utc)
        else:
            # New entry
            dim_class = classify_by_dimensions(length, beam)
            est_dwt = estimate_dwt(length, beam, draft)

            db.add(
                VesselRegistry(
                    mmsi=mmsi,
                    imo=imo.strip() if imo and imo.strip() != "0" else None,
                    ship_name=(ship_name or "").strip(),
                    ship_type=ship_type or 0,
                    ship_type_detailed=_detailed_type_name(ship_type) if ship_type else None,
                    ship_class=dim_class,
                    dwt=est_dwt,
                    dwt_estimated=bool(est_dwt and (not draft or draft <= 0)),
                    length=length if length and length > 0 else None,
                    beam=beam if beam and beam > 0 else None,
                    draft=draft if draft and draft > 0 else None,
                    destination=(destination or "").strip() or None,
                    flag_state=(flag_state or "").strip() or None,
                    last_updated=datetime.now(timezone.utc),
                )
            )

        if own_db:
            db.commit()
    except Exception as e:
        if own_db:
            db.rollback()
        logger.debug("Vessel registry upsert failed for MMSI %s: %s", mmsi, e)
    finally:
        if own_db:
            db.close()
