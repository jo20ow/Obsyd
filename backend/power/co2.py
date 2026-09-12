"""CO₂ intensity of generation (estimated) — hourly gCO₂eq/kWh per bidding zone.

Two derived series in the canonical hourly store, computed from the A75 mix the
desk already holds (`gen.<PSR>` in `power_hourly`) times published per-technology
emission factors:

    co2.intensity.lifecycle   — life-cycle factors (construction/fuel chain/
                                operation/decommissioning); the cross-country
                                comparison standard.
    co2.intensity.direct      — direct (operational/combustion) factors; the
                                convention national inventories use. Renewables,
                                nuclear and (by the biogenic-carbon convention)
                                biomass are 0 here.

    CI(zone, hour) = Σ_psr gen_MW(psr) · EF(psr)  /  Σ_psr gen_MW(psr)

WHAT THESE NUMBERS ARE, AND ARE NOT
-----------------------------------
This is a PRODUCTION-based, technology-average estimate on the public record —
the same formula Electricity Maps (default path), Ember and lowcarbonpower use.
It is descriptive arithmetic over ENTSO-E's published mix, not a measurement:

  * Factors are per-technology medians, not per-plant. The honest error band vs
    plant-calibrated factors is roughly ±10–20 % on fossil-heavy hours
    (Unnewehr et al. 2021, arXiv:2110.07999 — EUTL-calibrated factors as the
    future upgrade path; the per-unit A73 table would support it).
  * PRODUCTION intensity: what the zone's own plants emit per kWh generated.
    It deliberately ignores imports — a flow-traced CONSUMPTION intensity
    (Tranberg et al. 2019, arXiv:1812.06679) is the documented v2; the flow
    series it needs are already in the store.
  * A75 coverage caveats apply and are inherited knowingly: units < 100 MW are
    aggregated or missing in some zones, CHP heat allocation is the TSO's, and
    "Other" is a real (thermal-assumed) bucket, not a rounding artifact.

FACTOR TABLE — every value carries its source
---------------------------------------------
Life-cycle medians are IPCC AR5 WGIII Annex III (Schlömer et al. 2014), taken
via Electricity Maps' `config/defaults.yaml` (electricitymaps-contrib,
AGPL-3.0 — the same license as this repo; used with attribution). Direct
factors are the same file's operational set (IPCC 2014 / BEIS 2021 / EIA 2020).
ENTSO-E PSR codes that have no class of their own anywhere are mapped to the
nearest fuel class ON PURPOSE, and each mapping is written down here rather
than hidden in a fallback:

  * B02 Lignite → coal class (IPCC AR5 publishes no lignite split; understates
    lignite somewhat — plant-level lignite runs above 820 direct).
  * B03 Coal-derived gas → coal class (its carbon enters with the coal, the
    same reasoning marginal.py uses to price it off coal).
  * B07 Oil shale → oil class (no producer among the served zones; documented
    understatement if one ever appears).
  * B08 Peat → coal class (the most coal-like fuel; no IPCC/EM class).
  * B15 Other renewable → biomass class (in ENTSO-E practice predominantly
    biogas/small biomass; mapping it to "unknown thermal" would be absurd,
    mapping it to 0 would greenwash).
  * B17 Waste → unknown-thermal class (waste-to-energy is a fossil/biogenic
    blend with no defensible single factor; Electricity Maps' convention).
  * B20 Other → unknown-thermal class (assumes thermal — Electricity Maps'
    convention: 700 lifecycle / 575 direct).

STORAGE (B10 pumped hydro, B25 batteries) IS EXCLUDED — and that is not a
dodge: pricing a storage MWh at the same hour's production average and then
averaging is mathematically identical to leaving storage out of both sums
(adding mass at the mean does not move the mean). Exclusion is therefore the
self-consistent single-pass version of "discharge carries the grid's own
intensity". Electricity Maps' world-average discharge fallback (~300–360
gCO₂eq/kWh) would import a global constant into a zonal series. Pumping load
never enters: it lives under `consumption.<PSR>`, a different prefix.

Unrecognised PSR codes (B21–B24 are network elements, future codes unknown)
are skipped from BOTH sums and logged — guessing a factor for a code nobody
can name would be an invented claim (marginal.py's rule, applied here).

Hours whose total known generation is below MIN_TOTAL_MW are not written: a
ratio over a near-empty denominator is publication-lag noise, not a reading.
Upstream restatements are absorbed by recomputation — the scheduler recomputes
a trailing window, and the series is in REVISION_EXCLUDED_PREFIXES like
residual.*: it restates whenever its inputs restate, so ledgering it would
double-count every upstream revision.

Honesty follow-up (documented intent, not yet built): a yearly reconciliation
of these hourly means against the EEA country indicator ("GHG emission
intensity of electricity generation"), published as its own table — the
scoreboard treatment, applied to ourselves.

References:
  IPCC AR5 WGIII Annex III: ipcc.ch/site/assets/uploads/2018/02/ipcc_wg3_ar5_annex-iii.pdf
  Electricity Maps factors: github.com/electricitymaps/electricitymaps-contrib
      (config/defaults.yaml, AGPL-3.0)
  UNECE 2022 LCA (uncertainty ranges): unece.org LCA_3_FINAL March 2022
  Unnewehr et al. 2021 (error band, EUTL calibration): arXiv:2110.07999
  Tranberg et al. 2019 (flow tracing, the v2): arXiv:1812.06679
"""
from __future__ import annotations

import logging
from collections import defaultdict

from sqlalchemy.orm import Session

from backend.models.energy import SeriesDim
from backend.power.hourly_store import read_hourly, upsert_hourly

logger = logging.getLogger(__name__)

SERIES_LIFECYCLE = "co2.intensity.lifecycle"
SERIES_DIRECT = "co2.intensity.direct"
UNIT = "gCO2eq/kWh"

#: gCO₂eq/kWh per ENTSO-E production type. Sources per value — IPCC AR5 Annex
#: III medians unless noted; mappings for classless codes per the module
#: docstring. Kept as two flat dicts (not tuples) so a test can assert each
#: table independently covers every generation PSR.
LIFECYCLE_G_PER_KWH: dict[str, float] = {
    "B01": 230.0,  # Biomass — IPCC 2014
    "B02": 820.0,  # Lignite → coal class — IPCC 2014
    "B03": 820.0,  # Coal-derived gas → coal class
    "B04": 490.0,  # Gas — IPCC 2014
    "B05": 820.0,  # Hard coal — IPCC 2014
    "B06": 650.0,  # Oil — UK POST 2014 (via EM defaults)
    "B07": 650.0,  # Oil shale → oil class
    "B08": 820.0,  # Peat → coal class
    "B09": 38.0,   # Geothermal — IPCC 2014
    "B11": 24.0,   # Hydro run-of-river — IPCC 2014
    "B12": 24.0,   # Hydro reservoir — IPCC 2014
    "B13": 17.0,   # Marine — IPCC 2014 (ocean median)
    "B14": 12.0,   # Nuclear — IPCC 2014
    "B15": 230.0,  # Other renewable → biomass class
    "B16": 45.0,   # Solar — IPCC 2014 (EM blend of utility/rooftop)
    "B17": 700.0,  # Waste → unknown-thermal class (EM convention)
    "B18": 12.0,   # Wind offshore — IPCC 2014
    "B19": 11.0,   # Wind onshore — IPCC 2014
    "B20": 700.0,  # Other → unknown-thermal (EM convention)
}

DIRECT_G_PER_KWH: dict[str, float] = {
    "B01": 0.0,    # Biomass — biogenic-carbon convention (BEIS 2021)
    "B02": 760.0,  # Lignite → coal class — IPCC 2014
    "B03": 760.0,  # Coal-derived gas → coal class
    "B04": 370.0,  # Gas — IPCC 2014
    "B05": 760.0,  # Hard coal — IPCC 2014
    "B06": 406.0,  # Oil — EIA 2020 / BEIS 2021
    "B07": 406.0,  # Oil shale → oil class
    "B08": 760.0,  # Peat → coal class
    "B09": 0.0,    # Geothermal — IPCC 2014
    "B11": 0.0,
    "B12": 0.0,
    "B13": 0.0,
    "B14": 0.0,    # Nuclear
    "B15": 0.0,    # Other renewable → biomass class (direct = 0)
    "B16": 0.0,    # Solar
    "B17": 575.0,  # Waste → unknown-thermal class
    "B18": 0.0,
    "B19": 0.0,
    "B20": 575.0,  # Other → unknown-thermal
}

#: Storage discharge: excluded ≡ priced at the same hour's production average
#: (see module docstring for the equivalence argument).
EXCLUDED_STORAGE_PSRS = frozenset({"B10", "B25"})

#: Below this total known generation an hour is publication-lag noise, not a
#: reading — the smallest zone's real hours run well above it.
MIN_TOTAL_MW = 50.0


def _gen_series_keys(db: Session) -> list[str]:
    """Every `gen.<PSR>` key that exists in the store (cheap: series_dim is tiny)."""
    return [k for (k,) in db.query(SeriesDim.key).filter(SeriesDim.key.like("gen.%")).all()]


def compute_and_store_range(db: Session, zone: str, start_ts: int, end_ts: int) -> int:
    """Recompute both intensity series for one zone over [start_ts, end_ts) and
    upsert them. Returns hours written (per series; both always move together).

    Full-recompute doctrine (records.py): every call rebuilds the window from
    the current mix, so upstream restatements are absorbed by simply covering
    the window — no incremental state to corrupt.
    """
    lifecycle_num: dict[int, float] = defaultdict(float)
    direct_num: dict[int, float] = defaultdict(float)
    total: dict[int, float] = defaultdict(float)
    unknown: set[str] = set()

    for key in _gen_series_keys(db):
        psr = key[len("gen."):]
        if psr in EXCLUDED_STORAGE_PSRS:
            continue
        if psr not in LIFECYCLE_G_PER_KWH:
            unknown.add(psr)
            continue
        for ts, mw in read_hourly(db, key, zone, start_ts, end_ts):
            if mw < 0:
                # A75 occasionally reports small negative aggregates (metering
                # corrections); a negative fuel share has no physical meaning
                # in this ratio and would corrupt both sums.
                continue
            lifecycle_num[ts] += mw * LIFECYCLE_G_PER_KWH[psr]
            direct_num[ts] += mw * DIRECT_G_PER_KWH[psr]
            total[ts] += mw

    if unknown:
        logger.warning("co2 %s: unrecognised PSR codes skipped: %s", zone, sorted(unknown))

    hours = [ts for ts, mw in total.items() if mw >= MIN_TOTAL_MW]
    if not hours:
        return 0
    lifecycle_points = [(ts, lifecycle_num[ts] / total[ts]) for ts in hours]
    direct_points = [(ts, direct_num[ts] / total[ts]) for ts in hours]
    upsert_hourly(db, SERIES_LIFECYCLE, zone, lifecycle_points, unit=UNIT)
    written = upsert_hourly(db, SERIES_DIRECT, zone, direct_points, unit=UNIT)
    return written
