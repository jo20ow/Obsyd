# Price-convergence index: definition, edge choices, validation (2026-09-13)

The first analytics product of the quality doctrine ("name the statistic, cite the
canonical definition, validate before shipping"). Implementation:
`backend/power/convergence.py`; series `conv.{full,low,hours,spread}.<TO>` under the
border's sorted-first zone; endpoint `/api/power/convergence`; panel on the EUROPE tab.

## The canonical definition

ACER, Market Monitoring Report 2024 ("Progress of EU electricity wholesale market
integration", Figure 29 legend): price convergence on **hourly day-ahead prices**,
expressed as **% of hours**, in three bands:

- **full** price convergence: spread ≤ 1 EUR/MWh
- **moderate**: > 1 and < 10 EUR/MWh
- **low**: > 10 EUR/MWh

ACER's own footnote (11) travels with every rendering of the number: *"Reaching full
price convergence is not an objective, as it would require overinvestment in network
infrastructure."* 100% is not the target.

## Edge choices (documented, so the number is reproducible)

1. **Exactly 10.00 EUR/MWh** is unassigned in ACER's legend (moderate "<10", low
   ">10"). We count it **moderate** (`low` is strictly `> 10`).
2. **1.00 is inclusive** in full (`≤ 1`), matching ACER's "≤".
3. The desk-wide "coupled" threshold (`borders.py::COUPLED_EPS_EUR`) was aligned from
   the house value 0.5 to ACER's 1.0 in the same change — ONE convergence vocabulary.
4. Daily storage keeps **counts, not shares** (DST days have 23/25 hours; counts
   aggregate exactly over any window; moderate derives as `hours − full − low`).
5. Only hours priced on **both** sides count. Decoupling incidents (2024-06-25) are
   not excluded — published prices are published prices.
6. Since 2025-10 SDAC clears 15-min MTUs; `price.dayahead` is the hourly mean of four
   QH prices, so post-break counts measure convergence of hourly means. Documented
   structural break, not corrected away.
7. Magnus Energy's SDAC Market Watch uses different cut points (0–2 / 2–15 / >15 on
   CCR-average spreads) — same concept, different convention. We ship ACER's.

## Validation against the prod store (read-only, before deploy)

Independent re-derivation (inline SQL + Python on the prod DB, not the module) over
calendar 2024, summer 2025 (Jun–Aug), and the trailing 12 months to 2026-09-13:

| Border | window | hours | full% | mod% | low% | Ø spread |
|---|---|---|---|---|---|---|
| ES–PT | 2024 | 7 815 | **96.3** | 1.9 | 1.9 | 0.59 |
| IT_CENTRO_NORD–IT_NORD | 2024 | 8 457 | **91.2** | 3.6 | 5.2 | 1.97 |
| DK1–DK2 | 2024 | 8 716 | 75.8 | 13.2 | 10.9 | 3.65 |
| NO1–NO2 | 2024 | 8 630 | 54.7 | 18.2 | 27.1 | 8.95 |
| DE_LU–NL | 2024 | 8 736 | 8.4 | 55.0 | 36.6 | 11.05 |
| DE_LU–DK1 | 2024 | 8 746 | 8.2 | 50.3 | 41.6 | 14.08 |
| DE_LU–FR | 2024 | 8 655 | 5.2 | 35.3 | 59.5 | 25.41 |
| CH–DE_LU (no SDAC) | 2024 | 8 737 | 8.6 | 46.7 | 44.7 | 18.86 |
| CH–FR (no SDAC) | 2024 | 8 609 | 5.7 | 34.0 | 60.3 | 23.43 |

Plausibility anchors that PASS:
- **MIBEL (ES–PT) ≈ 96% full** — the Iberian coupling famously clears as one market.
- **Italian internal borders > 90% full**; DK1–DK2 ~76%; NO1–NO2 ~55%.
- **Core borders in single-digit full %** with €11–25 mean spreads in 2024 —
  consistent with ACER's post-crisis reporting of low full convergence in Core.
- ES–PT collapses to 17.9% full over the trailing 12 months — the market record
  after the April-2025 Iberian events; reported as published, not smoothed.

What did NOT reproduce, and why it changed the product:
- The research brief carried a secondary claim (Magnus) that non-SDAC borders (CH)
  show **<0.5%** "good convergence". Under ACER's ≤1 band we measure CH borders at
  **5–9%** — chance proximity of two independent auctions, indistinguishable in the
  band statistic from a congested coupled border (DE_LU–NL is 8.4%). The exact-
  equality check separates MIBEL-style two-zone coupling (ES–PT: 95.6% of 2024 hours
  exactly equal) from CH (0.1%) — but NOT flow-based coupling (DE_LU–NL also 0.1%
  exact: under FBMC any binding element anywhere splits all zone prices slightly,
  which is exactly why ACER uses a band). **Consequence:** the coupling distinction
  is carried by an explicit `sdac: false` flag on CH borders (excluded from the
  headline aggregate), never inferred from the band numbers; the unreproduced <0.5%
  figure is not cited anywhere in the product.

## What is deliberately NOT shipped

- No CCR-level aggregation (ACER's per-CCR max-minus-min needs CCR membership
  tables and a different statistic; the EU headline is share of border-hours,
  unweighted, and says so).
- No convergence "score", ranking judgment, or target framing (the ACER footnote).
- No econometric convergence tests (Zachmann-style unit-root/Kalman) — time-series
  claims, not descriptive statistics; would break Posture B.

Re-run the validation: `scratchpad/validate_convergence.py` pattern — inline band
count over `price.dayahead` pairs on the prod DB, compare against
`/api/power/convergence?days=365` after a backfill.
