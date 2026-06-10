"""Print the disruption-score weight backtest as a keep/drop table.

Run from the repo root (locally with a copy of obsyd.db, or on the VPS)::

    python -m backend.scripts.backtest_disruption_weights            # 7d horizon
    python -m backend.scripts.backtest_disruption_weights 1 7 30     # several horizons

For each component it shows the in-sample rank IC vs forward Brent returns,
the HAC (Newey-West) t-stat, the current vs IC-fitted weight, and an
out-of-sample drop-one-out verdict. With little history the honest output is
"insufficient data" — that is the point, not a bug.
"""

from __future__ import annotations

import sys

from backend.analytics.validation.weights import MIN_CONFIDENT_N, backtest_disruption
from backend.database import SessionLocal


def _fmt(x, width=9, nd=3):
    if x is None:
        return "—".rjust(width)
    return f"{x:.{nd}f}".rjust(width)


def _print_report(res: dict) -> None:
    h = res["horizon_days"]
    n = res.get("n", 0)
    print(f"\n=== Disruption-score weight backtest — {h}d forward Brent ===")
    print(f"observations (one per day): {n}", end="")
    if not res.get("confident"):
        print(f"   [PRELIMINARY — need n>={MIN_CONFIDENT_N} before any public claim]")
    else:
        print("   [confident sample]")

    if "note" in res:
        print(f"note: {res['note']}")

    oos = res.get("oos_ic")
    if oos:
        print(f"out-of-sample composite IC (test n={res.get('oos_n')}):")
        print(f"    current weights     : {_fmt(oos.get('current'))}")
        print(f"    equal weights       : {_fmt(oos.get('equal'))}")
        print(f"    IC-fitted (OOS)     : {_fmt(oos.get('ic_proportional_oos'))}")

    comps = res.get("components")
    if not comps:
        return
    print()
    header = f"{'component':<14}{'IC':>9}{'HAC t':>9}{'w_cur':>9}{'w_fit':>9}{'oos_drop':>10}{'verdict':>9}"
    print(header)
    print("-" * len(header))
    for c in comps:
        print(
            f"{c['name']:<14}"
            f"{_fmt(c.get('ic_in_sample'))}"
            f"{_fmt(c.get('hac_t'))}"
            f"{_fmt(c.get('weight_current'))}"
            f"{_fmt(c.get('weight_ic_fit'))}"
            f"{_fmt(c.get('oos_ic_delta_if_dropped'), width=10)}"
            f"{c.get('verdict', '—'):>9}"
        )
    print(
        "\nverdict 'drop?' = removing the component improved out-of-sample IC "
        "(candidate for pruning); 'keep' = it helps or is neutral."
    )


def main(argv: list[str]) -> int:
    horizons = [int(a) for a in argv[1:]] or [7]
    db = SessionLocal()
    try:
        for h in horizons:
            res = backtest_disruption(db, horizon_days=h)
            _print_report(res)
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
