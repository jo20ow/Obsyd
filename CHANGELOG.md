# Changelog

Notable changes to OBSYD. Versions correspond to GitHub releases (and, from 1.0.0
on, to the Zenodo-archived releases referenced in [CITATION.cff](CITATION.cff)).
The Python client is versioned separately (`clients/python`, tags `client-vX.Y.Z`).

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
