# Findings: free daily EUA (CO₂) + coal price source — A0 research spike

**Date:** 2026-06-24 · **Status:** CLOSED — decision: no clean free source today → A3 (clean/dark spread, merit-order fuel legs) stays parked.

## Question
Is there a **free, programmatically-ingestable, redistributable, daily** price series for EU ETS
allowances (EUA, EUR/tCO₂) — and for coal (API2/ARA) — to power the deferred energy syntheses
(clean-spark `power − gas·heatrate − CO₂·EF`, dark-spread, merit-order fuel legs)? Obsyd's data
principle requires *freely redistributable* sources for the visible core (CLAUDE.md).

## Sources evaluated

| Source | EUA daily? | Free + programmatic? | Redistributable? | Verdict |
|---|---|---|---|---|
| **yfinance** | — | — | — | **Dead.** `ECF=F`/`CO2=F`/`EUA=F` (EUA) + `MTF=F`/`API2`/`ARA=F` (coal) all return empty/delisted. Only `KRBN` (a USD carbon ETF, wrong instrument/currency) works. |
| **EEX EU ETS auctions** | yes (auction days) | no | **no** | Authoritative, but webshop/permission-gated; ToS: *"systematic republication … only with express permission"* — blocks redistribution. |
| **ICAP Allowance Price Explorer** | yes | partly (JS UI download) | restricted | Download exists but JS-driven, not a stable file/API; Terms-of-Use restrict reuse. Sandbag's viewer sources from here. |
| **Databento** (ICE EUA futures) | yes | API | paid | Commercial. |
| **Fraunhofer Energy-Charts API** (`api.energy-charts.info`) | **electricity yes; CO₂ unconfirmed** | **yes — free, no token, CC BY 4.0** | **yes (CC BY 4.0)** | Best general source. `/price` (day-ahead) + `/cbpf` (cross-border flows) confirmed. Fraunhofer's press release says the "prices" category *includes CO₂ certificate prices*, but the public `/price?bzn=CO2` is **invalid** and the exact CO₂ API code could not be confirmed (not in the OpenAPI spec; likely website-UI-only). |
| **EEA EU ETS data viewer** | no (annual/aggregate) | csv | yes | Verified emissions/allowances by year — not a daily price. |

## Decision
**No confirmed free + programmatic + redistributable *daily EUA* feed exists today** (and **no free
daily coal/API2 feed at all**). Therefore the clean/dark-spread + merit-order fuel legs (A3 / Slice 4)
**stay parked**; `SparkSpreadHistory.co2_price` / `clean_spark_spread` stay null. This is consistent
with the open-data principle — we do not build the visible product on a license-restricted or
unconfirmed feed.

## Unblock paths (ranked, for when revisited)
1. **Pin the Energy-Charts CO₂ code** (most promising; CC BY 4.0 = usable): inspect energy-charts.info's
   network calls for the CO₂ chart, or email Fraunhofer (`leonhard.gandhi@ise.fraunhofer.de`). If a
   public daily CO₂ series exists → wire `EnergyPrice(symbol="EUA")` via the existing collector
   pattern and clean-spark ships immediately.
2. **Periodic CSV stopgap** (Bruegel-pattern, `data/gas/bruegel_weekly.csv` precedent): ingest ICAP/
   Sandbag EUA settlements into a CSV with explicit source + last-updated labeling. Manual freshness —
   acceptable only with honest staleness display.
3. **Paid feed** (Databento/ICE) — only if revenue justifies; conflicts with the free-core principle
   for the *visible* layer (could be a Pro-only input).

## Byproduct opportunity (not acted on here)
**Energy-Charts (CC BY 4.0, free, no-auth)** is a clean source for European **day-ahead prices** and
**cross-border physical flows** — it could cross-check/complement our ENTSO-E power data and notably
may cover the **FR↔NL** cross-border flow that ENTSO-E's A11 endpoint returned empty for (PR #25).
Worth considering as a secondary/redundancy source in a future power slice.
