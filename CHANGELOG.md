# Changelog

Notable changes to OBSYD. Versions correspond to GitHub releases (and, from 1.0.0
on, to the Zenodo-archived releases referenced in [CITATION.cff](CITATION.cff)).
The Python client is versioned separately (`clients/python`, tags `client-vX.Y.Z`).

## 1.2.0 — 2026-09-14

The open data base widens: a 38th zone outside ENTSO-E, an estimated CO₂ layer,
derived statistics as first-class series, and the desk's documentation moving
onto its own domain. Descriptive throughout, as ever.

- **Great Britain (38th zone)** — served from Elexon Insights + NESO embedded
  wind/solar estimates (folded into load/mix with the estimate published as its
  own transparent series). GB carries the Elexon Market Index (`price.mid`)
  instead of a day-ahead auction — GB auctions are licensed, and the label says
  so. History from 2019.
- **Estimated CO₂ intensity** (`co2.intensity.lifecycle` / `.direct`) for all 38
  zones, hourly: published generation mix × declared per-technology factors
  (IPCC AR5 / Electricity Maps set). Production-based, technology-average — an
  estimate, labeled as one, with the full factor table in the repo. Map layer,
  panel, records, and a client notebook included.
- **Derived series promoted to the catalog** — daily negative-price hours
  (`price.negative_hours`), monthly capture prices & value factors per
  technology (`capture.<PSR>.*` — solar cannibalisation as a queryable series),
  the imbalance-vs-day-ahead spread (`spread.da_imbalance`), and price/residual
  **duration curves** via `/api/power/duration`.
- **German congestion-management costs** (`congestion.cost.*`, SMARD/
  Bundesnetzagentur CC BY 4.0, monthly since 2022-07) and **intraday-auction
  prices** (`price.ida1-3.qh`) for the one zone that publishes them on the
  Transparency Platform (Spain, from 2026) — the coverage probe is documented.
- **Hourly history extended to ENTSO-E's 2015 floor** across the ENTSO-E zones
  (prices, load, mix, forecasts, flows where the source carries them). Bidding
  zones younger than that say so: DE-LU begins 2018-10 (the DE-AT-LU split) and
  a first partial year is named, never silently averaged into a full one.
- **Multi-series tooltips carry color marks**, the range selector gained **MAX**
  (everything on record), and cross-border flows on the map show direction.
- **Optional API keys** — free accounts (magic-link login at `/login`) can mint
  keys (`/api/v1/keys`, or the new `/account` page) and see their own usage
  (`/api/v1/keys/usage`). The public API stays keyless; keys exist for usage
  tracking and future higher-throughput tiers.
- **Docs on the product domain** — the full API reference now renders at
  `/docs/reference` (same file as `docs/API.md`), this changelog at
  `/changelog`, live coverage at `/status`, and embeds/badges are documented
  on `/docs`.

## 1.1.0 — 2026-08-05

The Honest Record: OBSYD now documents the official record's own behavior — and
publicly grades the official forecasts. Everything remains descriptive; the
scoreboard grades ENTSO-E's published D-1 forecasts and OBSYD makes none.

- **Revision ledger** — every write to the canonical hourly store now records
  restatements (old value → new value, when observed) and arrivals. Forward-only
  from this release; the source's provisional fill-ins are separated from mature
  restatements (observed >48 h after the hour) at read time.
- **Data-quality aggregates** — nightly per zone × series × UTC day: completeness
  vs expected intervals plus rule-based flags (solar at night, load flatlining at
  zero, outsized hourly steps, generation below load + exports where coverage
  allows the comparison). Each flag describes the feed, never the market.
- **Public quality API** — `GET /api/v1/quality/{summary,series,revisions}`:
  completeness matrix, per-day history with arrival-lag stats, and the revision
  ledger with a restated-hours roll-up.
- **Forecast scoreboard** — nightly MAE/RMSE/bias (MAPE for load) for ENTSO-E's
  published day-ahead load/residual/wind/solar forecasts vs its published
  actuals, with skill vs two naive yardsticks built from actuals alone
  (persistence, seasonal). `GET /api/v1/scoreboard/{summary,ranking,monthly,profile}`;
  wind/solar rank capacity-normalized (A68), zones a normalization cannot cover
  are listed signposted, never hidden.
- **Radar integration** — completeness drops vs a zone's own 30-day norm and
  major mature restatements surface in the anomaly radar and RSS.
- **Desk surfaces** — DATA QUALITY + REVISIONS LEDGER panels on EXPLORE,
  FORECAST SCOREBOARD on ANALYTICS, with methodology in HOW TO READ.

## 1.0.0 — 2026-07-29

First archived release. OBSYD is a free, open-source European power desk built on
the official record (ENTSO-E, Fraunhofer Energy-Charts CC BY 4.0, GIE): an
all-zones overview leading to per-zone detail, a versioned public data API over a
canonical hourly store, and an anomaly radar that flags deviations from each
zone's own history. Descriptive throughout — no forecasts, no black-box scoring;
every threshold runs in code you can read here.

Highlights at this release:

- 37 European bidding zones (27 EU + CH, NO1–5, SE1–4), config-only enablement
- Canonical hourly series store with a public catalog: day-ahead prices (hourly +
  15-min where SDAC trades it), load, per-fuel generation, residual load,
  forecasts, imbalance, cross-border flows, scheduled exchanges, net positions,
  hydro reservoirs, balancing energy/capacity prices
- Versioned data API (`/api/v1`: series, snapshot, genmix, capacity, units,
  catalog, meta, status) with JSON/CSV/Parquet export and an honest per-source
  freshness endpoint; pip-installable Python client (`obsyd` 0.2.1)
- Day-ahead NTC (A61) with border-utilization fields
- Per-unit generation output (A73) with per-unit publication-lag honesty
- Marginal-technology estimate alongside the price panels
- Ops hardening: per-IP rate limit, heavy-query semaphore, row-scan cap,
  freshness watchdog, retention + backup/heartbeat scripts
