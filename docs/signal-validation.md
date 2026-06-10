# Signal Validation Framework — Design

> **Status:** P1 shipped — `backend/analytics/validation/` (metrics, prices,
> disruption weight backtest) + `backend/scripts/backtest_disruption_weights.py`,
> 18 unit tests. P2–P4 (scorecard table, API, event-study generalization,
> frontend badges) still open.

## Context & goal

Obsyd has ~20 signals (disruption score, tonne-miles, rerouting index, crack
spread, floating storage, EIA prediction, …). They look plausible but none
carries a **verifiable claim** that it predicts anything. That's the actual
moat at the €15 price point: not data completeness (Kpler/Vortexa win that),
but *validated, transparent, backtested* indicators with a published track
record.

Goal: turn each signal from "plausible dashboard" into "indicator with a
measured forward-return relationship, an honest sample size, and a track
record" — and use the same evidence to **prune** signals/components that add
nothing (the discipline already applied to the disabled ValueKick lineup
layer).

This is deliberately the same rigor as ValueKick: measure against ground
truth, compare side-by-side with a naive baseline, drop layers that don't
improve.

## What already exists (leverage, don't rebuild)

| Asset | Use |
|---|---|
| `signals/alert_outcomes.py` | FRED price lookup (`_price_on_or_before`), T+0/1/7/30 snapshot pattern. Reuse the price-access helpers. |
| `DisruptionScoreHistory` (every 2h, 6 components + composite per date) | Ready-made panel dataset for the **weight backtest** — no new collection needed. |
| `EIAPredictionHistory` (`prediction`/`actual_eia_change`/`correct`, `pearson_r`, `optimal_lag_days`) | The template for a per-signal scorecard row. |
| `FreightProxyHistory` (`brent_corr_30d`, `rerouting_corr_30d`) | Precedent for storing rolling correlations alongside a signal. |
| FRED `DCOILBRENTEU`, `DCOILWTICO` (daily) | Price ground truth for forward returns. |

## Two evaluation modes

Signals come in two shapes; each needs a different metric.

**A. Continuous signals** (disruption score, tonne-miles, rerouting index,
freight proxy) — a level/index per day.
- Metric: **rank information coefficient (IC)** = Spearman correlation between
  the signal level at day *D* and the forward Brent return over horizon *h*.
- Plus directional **hit rate**: of the days the signal was in its top tercile,
  how often was the forward return positive (or whatever the signal claims)?

**B. Event / threshold signals** (alerts, "rerouting index > 110", anomaly
fires) — discrete events.
- Metric: **event study**. Mean (and distribution of) forward return in the
  *h* days after the event vs an unconditional baseline; hit rate; lead time.
- This is exactly what `alert_outcomes` already collects per alert — generalize
  it from `alerts` to any threshold crossing on a `*_history` series.

## Architecture

```
backend/analytics/validation/
  __init__.py
  prices.py        # forward-return series from FRED (reuse alert_outcomes helpers)
  metrics.py       # rank IC, hit rate, event-study, Newey-West t-stat — pure functions, unit-tested
  scorecards.py    # per-signal evaluation -> SignalScorecard rows
  weights.py       # disruption-score weight backtest (walk-forward)
models/validation.py  # SignalScorecard table
routes/validation.py  # GET /api/validation/scorecards, /api/validation/disruption-weights
```

### New table: `SignalScorecard`

One row per (signal, horizon, as_of) — rolling, recomputed weekly.

```python
class SignalScorecard(Base):
    __tablename__ = "signal_scorecards"
    id: int (pk)
    signal: str            # "disruption_score", "rerouting_index", ...
    horizon_days: int       # 1 / 7 / 30
    as_of: str              # YYYY-MM-DD of computation
    n: int                  # sample size (observations / events)
    mode: str               # "continuous" | "event"
    ic: float | None        # Spearman rank IC (continuous)
    hit_rate: float | None  # directional hit rate
    mean_fwd_high: float | None  # mean fwd return, signal high tercile / post-event
    mean_fwd_base: float | None  # unconditional baseline
    t_stat: float | None    # Newey-West adjusted (overlapping windows)
    p_value: float | None
    confident: int          # 1 iff n >= MIN_N and gates pass
    created_at: datetime
```

### Schedule + API

- Weekly scheduler job `recompute_scorecards()` (cheap; reads `*_history` + FRED).
- `GET /api/validation/scorecards` → all signals' latest scorecards (public).
- `GET /api/validation/disruption-weights` → the weight-backtest table (Pro).

### Frontend

- A small **Track Record badge** on each panel header: `IC 0.31 · 7d · n=84`
  or, until the gate passes, `Track record: building (n=12/30)`.
- A `/validation` methodology page: how each metric is computed, the
  walk-forward setup, and the caveats below. Open-sourcing the methodology is
  itself a credibility feature.

## Flagship: disruption-score weight backtest

The highest-value first deliverable because the data already exists in
`DisruptionScoreHistory`. Mirrors the ValueKick "does this layer improve the
score?" test.

Steps (`weights.py`):
1. Collapse `DisruptionScoreHistory` to **one observation per day** (last row
   of each day — dedupes the 2-hourly cadence).
2. Align each day to **forward Brent return** at h ∈ {1,7,30}d from FRED.
3. Per component, compute marginal rank IC vs forward return.
4. **Walk-forward** weight evaluation (no in-sample fitting):
   - Split history into expanding windows; on each, derive weights from the
     *past* only, apply to the *next* block, record out-of-sample composite IC.
   - Compare four weightings out-of-sample: (a) current fixed weights,
     (b) equal weights, (c) IC-proportional weights, (d) drop-one-out ablations.
5. Output a recommendation table: each component's marginal contribution and a
   verdict — **"keep" / "drop (no OOS contribution)"**, exactly like the
   shelved lineup layer. A component whose ablation *improves* OOS IC is a
   removal candidate.

Honest expected outcome on day one: **n is small** (weeks–months, one Hormuz
regime). The first result may well be "insufficient data to claim predictive
power" — and saying that plainly *is* the product.

## Methodology guardrails (the part that makes it credible)

These are non-negotiable; without them the numbers are worse than useless.

1. **No look-ahead.** A signal value dated *D* must use only data known at *D*.
   The disruption components already use lagged PortWatch data, so they're
   conservative — but verify `date` reflects knowledge time, not compute time,
   and never fit on data after the return window.
2. **Overlapping windows.** Daily obs with 7/30d forward returns overlap →
   autocorrelation inflates t-stats. Use **Newey-West** standard errors (or
   non-overlapping sampling). `metrics.py` does this; unit-test it against a
   known case.
3. **Multiple testing.** ~20 signals × 3 horizons = 60 tests; some look great
   by chance. Report a **Benjamini-Hochberg FDR** flag and surface it on the
   methodology page. Don't cherry-pick the best signal and headline it.
4. **Walk-forward, not in-sample.** Any weight optimization is evaluated only
   out-of-sample. In-sample "optimized" performance is never shown.
5. **Regime honesty.** State the sample window and that it's dominated by one
   regime. n<30 → never `confident=1`, never a public claim.

## What we will NOT claim

- No "this signal predicts oil prices" until `confident=1` *and* FDR-survived.
- No precision we don't have: report ranges/sample sizes, not point forecasts.
- The EIA panel's existing hedge ("informational, not a prediction") stays
  until its scorecard earns the upgrade.

## Phased rollout

- **P1 — metrics core + disruption weight backtest (shippable alone).**
  `prices.py`, `metrics.py` (unit-tested), `weights.py`, a CLI/script that
  prints the weight-recommendation table. No schema/UI yet. Proves value and
  tells us which components to keep.
- **P2 — scorecards persistence + API.** `SignalScorecard` table + migration,
  `scorecards.py` over the continuous signals, weekly job, `/api/validation/*`.
- **P3 — generalize event-study** from `alert_outcomes` to threshold crossings
  on any `*_history` series; fold alert track record into scorecards.
- **P4 — frontend.** Track-record badges + `/validation` methodology page.

## Verification

- `metrics.py`: unit tests with synthetic series of known IC / known
  event-study effect / known Newey-West t-stat (deterministic, no network).
- `weights.py`: test that a planted predictive component ranks above pure-noise
  components, and that ablation flags the noise component for removal.
- Backtest on real history is run via script; results reviewed manually before
  any number is surfaced (no auto-publishing of unvetted claims).
- Existing suite stays green; new tests gate the math, not the data volume.
