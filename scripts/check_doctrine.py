#!/usr/bin/env python3
"""Deterministic doctrine gates — project rules enforced by grep/AST, not memory.

Every rule here exists because it was violated at least once and cost a debug
session. The gate runs in <1 s over the whole tree; it is wired into
githooks/pre-commit (install once: `git config core.hooksPath githooks`) and
into CI next to ruff. Rules are narrow ON PURPOSE: each matches exactly the
regression class it guards, so the gate stays green on legitimate code and a
red result is always actionable.

Exit 0 = clean; exit 1 prints one line per violation (file:line: rule — hint).
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
violations: list[str] = []


def flag(path: Path, lineno: int, rule: str, hint: str) -> None:
    violations.append(f"{path.relative_to(ROOT)}:{lineno}: {rule} — {hint}")


def iter_files(base: str, suffixes: tuple[str, ...]) -> list[Path]:
    root = ROOT / base
    if not root.exists():
        return []
    return [p for p in root.rglob("*") if p.suffix in suffixes and p.is_file()]


# ── Rule A: never spread CHART_TOOLTIP_STYLE ─────────────────────────────────
# CLAUDE.md #109: `{...CHART_TOOLTIP_STYLE}` spreads a contentStyle OBJECT as
# Tooltip props — silently wrong. `{...CHART_TOOLTIP_PROPS}` is the contract.
def rule_tooltip_spread() -> None:
    pat = re.compile(r"\{\s*\.\.\.\s*CHART_TOOLTIP_STYLE\s*\}")
    for p in iter_files("frontend/src", (".jsx", ".js")):
        for i, line in enumerate(p.read_text(errors="replace").splitlines(), 1):
            if pat.search(line):
                flag(p, i, "tooltip-spread", "use {...CHART_TOOLTIP_PROPS}, never spread CHART_TOOLTIP_STYLE")


# ── Rule B: no inline-hex chart chrome ───────────────────────────────────────
# Redesign #161–#164: grids/ticks/axis lines come from useChartTheme() so both
# themes stay legible. Narrow match: only CartesianGrid stroke="#…" and inline
# tick fill objects — data-series colors (fuels.js, powermap palettes, embeds)
# are exempt by path, they are deliberate canonical palettes.
# The DORMANT-vertical panels (oil/maritime/metals/crypto era, not rendered in
# the product since the 2026-07-03 refocus) predate the redesign and are
# preserved-not-maintained until the Phase-2 extraction — exempted BY NAME so
# any NEW panel is still gated.
_B_DORMANT = (
    "components/CryptoPanel.jsx", "components/TonneMilesPanel.jsx",
    "components/DisruptionScorePanel.jsx", "components/ChokePointMonitor.jsx",
    "components/TransitChart.jsx", "components/MacroPanel.jsx",
    "components/ReroutingIndex.jsx", "components/CopperPanel.jsx",
    "components/RatesPanel.jsx",
)
_B_EXEMPT = ("utils/fuels.js", "powermap/", "embed/", "utils/chart.js") + _B_DORMANT


def rule_inline_chart_hex() -> None:
    pats = (
        re.compile(r"<CartesianGrid[^>]*stroke=\"#"),
        re.compile(r"tick=\{\{\s*fill:\s*['\"]#"),
        re.compile(r"axisLine=\{\{\s*stroke:\s*['\"]#"),
    )
    for p in iter_files("frontend/src", (".jsx", ".js")):
        rel = str(p.relative_to(ROOT / "frontend/src"))
        if any(x in rel for x in _B_EXEMPT):
            continue
        for i, line in enumerate(p.read_text(errors="replace").splitlines(), 1):
            if any(pat.search(line) for pat in pats):
                flag(p, i, "inline-chart-hex", "grid/tick/axis colors come from useChartTheme()")


# ── Rule C: premium keys never on public static surfaces ─────────────────────
# Premium doctrine #186: hidden means hidden — not shown-but-locked. The SPA
# bundle may contain the pro panels (they render null anonymously), but static
# public surfaces (docs page source, sitemap, llms.txt, index.html) must never
# name a premium series.
_C_SURFACES = ("docs/API.md", "frontend/index.html")
_C_KEYS = re.compile(r"conv\.|spread\.tb|price_floor0")


def rule_premium_leak() -> None:
    targets = [ROOT / s for s in _C_SURFACES] + list((ROOT / "frontend/public").rglob("*"))
    for p in targets:
        if not p.is_file() or p.suffix in (".png", ".ico", ".mp4", ".gif", ".woff2"):
            continue
        for i, line in enumerate(p.read_text(errors="replace").splitlines(), 1):
            if _C_KEYS.search(line):
                flag(p, i, "premium-leak", "premium series named on a public static surface")


# ── Rule D: no async-def route without await ─────────────────────────────────
# #116: an `async def` route with sync-DB work runs ON the event loop and
# serializes the whole API (14 s zone switches). Sync handlers must be `def`
# so Starlette threadpools them.
def _has_await(fn: ast.AsyncFunctionDef) -> bool:
    return any(isinstance(n, (ast.Await, ast.AsyncFor, ast.AsyncWith)) for n in ast.walk(fn))


# Dormant route modules (oil/maritime/metals era, registered-dormant since the
# 2026-07-03 refocus) keep their pre-#116 async signatures until the Phase-2
# extraction — they serve no traffic. Exempt BY FILE so active modules stay
# gated; auth.py is deliberately NOT here (live login path, fixed 2026-09-20).
_D_DORMANT = {"signals.py", "thermal.py", "sentiment.py", "weather.py",
              "jodi.py", "email.py", "filings.py", "analytics.py", "prices.py"}


def rule_async_without_await() -> None:
    for p in iter_files("backend/routes", (".py",)):
        if p.name in _D_DORMANT:
            continue
        try:
            tree = ast.parse(p.read_text())
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.AsyncFunctionDef) and not _has_await(node):
                deco = any(
                    isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute)
                    and d.func.attr in ("get", "post", "put", "delete", "patch")
                    for d in node.decorator_list
                )
                if deco:
                    flag(p, node.lineno, "async-no-await", f"route `{node.name}` has no await — make it `def` (threadpool)")


# ── Rule E: tests read the UTC clock ─────────────────────────────────────────
# #111: `date.today()` is the LOCAL date — tests comparing against "today"
# failed nightly 00–02 local. datetime.now(timezone.utc).date() is the rule.
def rule_local_today_in_tests() -> None:
    pat = re.compile(r"\bdate\.today\(\)")
    for p in iter_files("backend/tests", (".py",)):
        for i, line in enumerate(p.read_text(errors="replace").splitlines(), 1):
            if pat.search(line):
                flag(p, i, "local-today", "use datetime.now(timezone.utc).date() — tests read the UTC clock")


def main() -> int:
    for rule in (rule_tooltip_spread, rule_inline_chart_hex, rule_premium_leak,
                 rule_async_without_await, rule_local_today_in_tests):
        rule()
    if violations:
        print(f"doctrine: {len(violations)} violation(s)", file=sys.stderr)
        for v in violations:
            print(f"  {v}", file=sys.stderr)
        return 1
    print("doctrine: clean")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
