"""Every production type ENTSO-E can send has a name a human can read.

The Generation-Mix legend on ANALYTICS showed "gen.B03" between "Fossil Gas" and "Hard Coal":
B03 is Fossil Coal-derived gas, it is real, DE-LU burns it — and it was simply missing from
PSR_LABELS, so both the read-time label lookup and the stored psr_type fell back to the raw code.
"""
from __future__ import annotations

from backend.power.entsoe_grid import PSR_LABELS

# The generation types in the ENTSO-E codelist (A75/A68/A71 report these). B21-B24 are network
# elements (AC/DC link, substation, transformer) — never generation, never in a mix.
GENERATION_CODES = [f"B{i:02d}" for i in range(1, 21)]


def test_every_generation_psr_code_has_a_label():
    missing = [c for c in GENERATION_CODES if c not in PSR_LABELS]
    assert missing == [], f"raw codes leak into the UI as 'gen.{missing[0]}'" if missing else ""


def test_the_coal_derived_gas_that_leaked_is_named():
    assert PSR_LABELS["B03"] == "Fossil Coal-derived gas"


def test_the_migration_renames_rows_stored_under_the_raw_code(db_session, monkeypatch):
    """The ingest stored the fallback (the raw code) for years. Naming the code fixes NEW rows;
    the old ones must be renamed too, or the stacked mix draws one fuel as two."""
    import backend.migrations as migrations
    from backend.models.energy import PowerGenMix

    db_session.add(PowerGenMix(date="2026-07-01", zone="DE_LU", psr_type="B03", gen_mw=1_200.0))
    db_session.add(PowerGenMix(date="2026-07-01", zone="DE_LU", psr_type="Fossil Gas", gen_mw=9_000.0))
    db_session.commit()

    monkeypatch.setattr(migrations, "engine", db_session.get_bind())
    applied: list[str] = []
    migrations._relabel_raw_psr_codes(applied)
    db_session.expire_all()

    types = {r.psr_type for r in db_session.query(PowerGenMix).all()}
    assert types == {"Fossil Coal-derived gas", "Fossil Gas"}
    assert applied, "the rename must be reported, not silent"

    # Idempotent: a second pass has nothing left to do.
    again: list[str] = []
    migrations._relabel_raw_psr_codes(again)
    assert again == []
