# EU Gas Vertical — Frontend UI (Design Spec)

**Date:** 2026-06-24 · **Status:** approved, ready for implementation plan

## Context

Obsyd's EU gas-balance vertical (`backend/gas/`, `/api/gas/*`) is fully built and now
backfilled with real data (2023-01-01 → today: ENTSOG flows, AGSI storage, ALSI LNG,
ENTSO-E power burn, demand model, residual balance engine). It has **no frontend** — no
component fetches `/api/gas/*`. This is Roadmap-Schritt 1 in `CLAUDE.md`: surface the gas
vertical as the second visible "node" alongside the oil/maritime one.

The residual balance engine is, in the code's own words, "the product": implied ΔStorage
(supply − demand) vs. actual ΔStorage (AGSI), 7d-smoothed, z-scored, flagged. The goal of
this UI is to make that signal visible and credible, with its drivers shown transparently
(Obsyd's "transparency as a feature" principle).

## Decisions (from brainstorming)

- **Placement:** a new dedicated `GAS` tab (6th tab). The gas vertical is a standalone node.
- **Scope:** Core — the residual hero + its three drivers (Storage, Supply, Demand). ~4 panels.
- **Gating:** official raw-data driver panels are **free** (distribution/SEO magnet, per the
  "free = full official data" principle); the **residual synthesis is Pro** (the IP/edge).
- **Hero:** residual-focused (consistent with existing panels) with a **toggle** to an
  "implied vs actual ΔStorage" decomposition view inside the same panel.

## Components (4 new)

All follow existing conventions: `<Panel>` wrapper (`components/Panel.jsx`), data via
`useFetchWithError` (`hooks/useFetchWithError.js`), charts via `recharts`, dark monospace
theme with `cyan-glow` accent. Reference pattern: `components/DisruptionScorePanel.jsx`.
Standard states: fetch error → small red box; `!data?.available && !loading` → render null;
`loading` → pulse text; else render.

### 1. `GasBalancePanel.jsx` — the Pro hero
- **Endpoint:** `GET /api/gas/balance?days=120`
- **Gating:** wrapped in `<ProGate feature="EU Gas Balance">` in `App.jsx`.
- **Payload:** `{ available, latest: { date, supply_gwh, demand_gwh, exports_gwh,
  implied_delta, actual_delta, residual, residual_7d, z_score, flag }, active_flags, data: [...] }`
  (each `data` row has the same per-day fields).
- **Content:**
  - Hero header: `residual_7d` as the big number (GWh/7d), `z_score` badge, current `flag`
    (none/WATCH/SIGNAL) + count of `active_flags` / historically flagged days.
  - Default chart: recharts area of `residual` over 120d, zero baseline, with WATCH/SIGNAL
    points marked (color by flag severity).
  - **Toggle** (local `useState`): "RESIDUAL" ⇄ "IMPLIED vs ACTUAL" — the second view plots
    `implied_delta` and `actual_delta` as two lines (the gap = residual). Same payload, no
    extra fetch.

### 2. `GasStoragePanel.jsx` — free
- **Endpoint:** `GET /api/gas/storage`
- **Payload row:** `{ date, stock_twh, injection_gwh, withdrawal_gwh, fill_pct }`
- **Content:** latest `fill_pct` as the headline, `stock_twh`, net inject/withdraw direction,
  sparkline of `fill_pct` (or `stock_twh`).

### 3. `GasSupplyPanel.jsx` — free
- **Endpoint:** `GET /api/gas/supply` (returns `{ available, from, to, data }`)
- **Payload row:** `{ date, pipeline_gwh, production_gwh, lng_gwh, uk_net_gwh, supply_gwh }`.
  Note: the route calls `compute_daily_supply` with `include_production=False`, so
  `production_gwh` is `0.0` in this response — decomposition shown = pipeline + LNG + UK-net.
- **Content:** `supply_gwh` headline + pipeline / LNG / UK-net decomposition, sparkline of total.

### 4. `GasDemandPanel.jsx` — free
- **Endpoints:** `GET /api/gas/demand` **and** `GET /api/gas/power-burn` (two fetches).
- **Payloads:** demand row `{ date, heat_gwh, industrial_gwh, model_version }` + `note`;
  power-burn row `{ date, gen_gwh_el, implied_gas_gwh, efficiency }` + `note`.
- **Content:** total gas demand = `implied_gas_gwh` (power) + `heat_gwh` + `industrial_gwh`,
  shown as a stacked/decomposed value; surface `model_version` (e.g. `v1+power;…n=41`). This
  folds power burn in here, so no separate power-burn panel is needed for Core scope.

## Layout (GAS tab content in `App.jsx`)

```
Row 1:  <ProGate feature="EU Gas Balance"><GasBalancePanel /></ProGate>   (full width)
Row 2:  grid lg:grid-cols-3 gap-3:
          <GasStoragePanel /> <GasSupplyPanel /> <GasDemandPanel />        (all free)
```
Each panel wrapped in `<ErrorBoundary name="gas-*">` per the existing tab pattern. Add
`{ key: 'gas', label: 'GAS' }` to `TABS` and a `{activeTab === 'gas' && (…)}` block.

## Backend

No backend changes required — all endpoints exist and are backfilled. The `/api/gas/demand`
`note` string was already made dynamic (reflects whether power burn is included). The nightly
scheduler (`gas_balance_daily`) keeps the vertical current.

## Out of scope (Roadmap-Schritt 2)

- Separate LNG (ALSI) panel.
- Gas residual signal added to `backend/analytics/validation/scorecards.py` `SIGNAL_SPECS`
  + `TrackRecordBadge` on the balance panel (gives the gas vertical a track record).
- Compact residual teaser on the OVERVIEW tab.

## Verification

- `cd frontend && npm run build` succeeds.
- Manual smoke test (via `run` skill / local backend on :8000): open the GAS tab.
  - **As anonymous/free user:** Storage/Supply/Demand panels render real data; the
    GasBalancePanel is blurred behind the ProGate "Upgrade to Pro" overlay.
  - **As Pro user:** the residual hero renders; the RESIDUAL ⇄ IMPLIED vs ACTUAL toggle
    switches the chart; z-score badge and flag state show.
  - Empty-state sanity: panels handle `{available:false}` without crashing.
- No regression: existing tabs unaffected (the change is additive — one new tab + 4 new files).
