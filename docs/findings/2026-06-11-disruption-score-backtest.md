# Findings — first disruption-score backtest (2026-06-11)

First run of the signal validation framework (`backend/scripts/backtest_disruption_weights.py`,
see [signal-validation.md](../signal-validation.md)) against a snapshot of the
production `obsyd.db`.

**TL;DR:** The framework earned its keep immediately — it found a dead component
(a real bug, now fixed) and showed that, on the data available, the disruption
score has **no validated predictive relationship** with forward Brent returns.
That is the honest state, not a failure: we now know it with numbers.

## Setup

- Data: production snapshot, `disruption_score_history` = 1177 rows over **93
  distinct days** (2026-03-10 → 2026-06-10), one regime. FRED Brent back to 2019
  (then current through 2026-06-01).
- Method: one obs/day (last row), forward log Brent returns at 1/7/30d with
  strict no-look-ahead, rank IC + Newey-West HAC t-stats, single train/test
  split for out-of-sample weight comparison, drop-one-out ablation.
- After alignment: n = 77 (7d), n = 54 (30d).

## Result 1 — a dead component (bug, fixed in #3)

The `backwardation` component had **nan IC (zero variance)** across all 93 days:
it returned a constant 0.0. Root cause was a dict-shape mismatch — it read
`_fetch_structure()` through the `"curves"` wrapper that only
`get_market_structure()` adds, so the lookup always missed. Fixed in PR #3;
live it now returns ~48 instead of 0 (≈7 composite points at weight 0.15).

**Implication:** every historical `disruption_score_history` value was computed
with one of six components dead, so the stored composites are systematically
**~7 points low**. yfinance gives no free historical front/next spreads, so the
past cannot be backfilled; treat pre-2026-06-11 composite history as biased low.

## Result 2 — no validated predictive edge (yet)

Out-of-sample composite rank IC vs forward Brent return:

| horizon | n (test) | current weights | equal weights | IC-fitted (OOS) |
|---|---|---|---|---|
| 7d  | 31 | **−0.010** | −0.010 | 0.309 |
| 30d | 22 | **−0.104** | −0.104 | — (n too small) |

Per-component (rank IC / HAC t-stat), 7d horizon:

| component | IC | HAC t | current w | verdict |
|---|---|---|---|---|
| hormuz        |  0.084 |  0.50 | 0.25 | drop? (ablation +0.47) |
| cape          | −0.099 | −1.10 | 0.20 | keep |
| storage       | −0.042 | −1.77 | 0.10 | keep |
| crack         |  0.005 | −0.99 | 0.15 | keep |
| backwardation |  —     |  —    | 0.15 | (dead — see Result 1) |
| sentiment     |  0.301 |  1.46 | 0.15 | keep |

Reading it honestly:

1. **Nothing is statistically significant.** No `|HAC t| > 2` at any horizon
   once overlapping windows are corrected. With ~50–77 obs in a single regime,
   "insufficient evidence" is the correct conclusion — not "signals don't work".
2. **The hand-set weights show ≈0 / slightly negative OOS IC** — indistinguishable
   from equal weighting. No evidence the tuning adds predictive value.
3. **`sentiment` is the only consistently positive component** (IC 0.21/0.30/0.22
   across 1/7/30d) and the IC-fit concentrates weight on it — but even it is
   `|t| < 1.5`.
4. **`hormuz`** is the highest weight (0.25) yet ablation flagged it `drop?` at
   7d (removing it improved OOS IC by 0.47). A yellow flag to watch, not act on
   at n=77.
5. Do **not** headline "IC-fitted hit 0.309 at 7d" — that's a single split, small
   test n, and 1-of-18 tests; exactly the multiple-testing trap.

## Decisions

- **Frame the disruption score as descriptive, not predictive.** It is a "how
  stressed is the supply chain right now" index. No public claim that it predicts
  oil prices until a scorecard earns it (`confident=1` + FDR-survived).
- **No weight changes now.** The current weights aren't beaten by anything with
  evidence behind it; changing them on this sample would be overfitting.
- **Re-run after the data matures**, ideally across a regime change, with the
  backwardation component live. Trigger: **n ≥ 120 daily obs** (≈4 more months).
  P2 (scorecard table + weekly job) will make this automatic and gate the UI.

## Caveats (why the numbers are weak by construction)

Single 3-month regime · small n · one component dead for the whole sample ·
single train/test split (OOS IC is fragile) · FRED price lag drops recent dates ·
18 tests → expect some spurious `drop?`/positive ICs by chance.
