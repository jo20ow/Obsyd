# Top-bottom spread index (TB1/TB2/TB4): naming, parameters, validation (2026-09-13)

Second analytics product of the quality doctrine. Implementation:
`backend/power/derived_stats.py::store_tb_spreads`; series `spread.tb1/tb2/tb4`
(EUR/MW-day, one point per UTC day); panel on the ANALYTICS tab.

**Status: PREMIUM PREVIEW** (owner decision 2026-09-13) — methodology public (this
doc, AGPL code); the served series and the panel are gated behind a pro session
(`backend/premium.py`) while the product is tested.

## The naming decision — the whole point

The methodology research (2026-09) established that a "battery revenue benchmark"
would be wrong in BOTH directions simultaneously:

- **Not an upper bound:** GB 2-hour systems earned ~1.42× TB2 in 2025
  (~£73k/MW/yr; Modo Energy) by stacking Balancing Mechanism, ancillary and
  intraday revenue. Any "theoretical maximum" label is falsifiable on sight.
- **Not achievable either:** naive day-ahead trading does not capture the
  perfect-foresight spread (Modo applies an ~80% calibration factor to bring
  simulated revenue in line with realised performance; Hornek et al. 2025 find
  rolling-window intraday strategies reach ~89% of perfect foresight).

So the product is the honest subset: **the price-spread statistic itself**, named
as such everywhere (catalog label, panel title, API.md, HowToRead), with the
1.4×-TB2 fact carried in the caveat text. Real-revenue benchmarking requires
metered settlement data and per-market stacks we do not have and cannot get
(Modo's FCA-regulated moat).

## Declared parameters (all five, per the research checklist)

| Parameter | Choice |
|---|---|
| Duration mapping | TBn = n highest − n lowest hourly prices; n = 1/2/4 (1h/2h/4h at 1 cycle/day) |
| Efficiency | **none** (Modo's TB convention — pure arithmetic; an 85%-RTE variant would be a model) |
| Cycles | 1 per day, by construction |
| Foresight | perfect within the published day (it is a statistic on published prices, not a strategy) |
| Markets / price series | hourly `price.dayahead` only; UTC-day buckets uniformly across zones (Modo buckets local days — declared difference); days < 20 priced hours skipped; GB absent (no auction series; TB on the MID index would be a different statistic) |

Negative prices are inside the statistic on purpose: charging at −€50 and
discharging at +€100 is a €150 TB1 — Germany's TB1 is partly a negative-price
index, and saying so beats flooring it.

## Validation (prod store, read-only, before deploy — calendar 2025)

| Zone | days | TB1 Σ | TB2 Σ | TB4 Σ | TB2 mean/day |
|---|---|---|---|---|---|
| DE_LU | 365 | €43.5k/MW | **€82.0k/MW** | €143.3k/MW | €225 |
| GR | 347 | €56.4k | €103.8k | €177.3k | €299 |
| ES | 351 | €34.1k | €64.5k | €114.9k | €184 |
| FI | 363 | €31.2k | €58.3k | €101.0k | €161 |

Plausibility anchors that pass:
- **TB1 < TB2 < TB4** monotone everywhere (by construction, verified on data).
- **DE_LU TB2 ≈ €82k/MW-yr** sits below Modo's DE 2h realised benchmark
  (~€240k/MW/yr in 2025, ~55% ancillary → wholesale portion ~€110k, intraday-led)
  — consistent with Modo's own observation that the DE benchmark has been
  "narrowing in on the TB2 spread". The gap IS the point of the naming doctrine.
- Ranking GR > DE > ES > FI matches the known volatility ordering.

## Deliberately NOT shipped

- No €/MW/yr "battery revenue" framing anywhere (tested: the catalog label must
  not contain "revenue" — `test_tb_wiring`).
- No dispatch model, no LP, no RTE variant, no cycle optimisation — those are
  models with parameter choices that swing the answer ~2×; the statistic is
  parameter-free arithmetic once the five declarations above are fixed.
- No GB series.
