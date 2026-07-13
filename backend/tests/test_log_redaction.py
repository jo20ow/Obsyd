"""The ENTSO-E API key was in the logs. All of them. For as long as the project has existed.

httpx logs `HTTP Request: GET <full url>` at INFO, and ENTSO-E takes its key as a query
parameter — `?securityToken=…`. So every call, across 37 zones, several thousand a day, wrote
the credential in plaintext into journald. Counted on prod before the fix: **10,959 occurrences
in three days.** journald persists and rotates into archives, so the key outlived every process
that used it, and anyone who could read the logs had it.

Nothing about this is specific to A09 — it was simply visible in a backfill log while doing
something else. These tests are here so it cannot come back.
"""
from __future__ import annotations

import logging

import pytest

from backend.observability import install_log_redaction, setup_logging

#: The literal SHAPE httpx emits — with an obviously fake key in it.
#:
#: The first version of this file pasted the real token straight out of the prod log, which is
#: how a leak becomes a worse leak: the fixture would have carried the live credential into a
#: public repository. The shape is what the test needs; the value must never be a real one.
FAKE_TOKEN = "00000000-0000-4000-8000-000000000000"  # not a key: a uuid of zeros

HTTPX_LINE = (
    "HTTP Request: GET https://web-api.tp.entsoe.eu/api"
    f"?securityToken={FAKE_TOKEN}&documentType=A09"
    "&out_Domain=10YHU-MAVIR----U \"HTTP/1.1 200 OK\""
)


@pytest.fixture
def captured(caplog):
    setup_logging()          # installs the redaction filter on the root handler
    install_log_redaction()  # idempotent; also what the batch scripts call
    root = logging.getLogger()
    handler = root.handlers[0]

    def _emit(msg: str, *args) -> str:
        record = logging.LogRecord("httpx", logging.INFO, __file__, 1, msg, args, None)
        for f in handler.filters:
            f.filter(record)
        return record.getMessage()

    return _emit


def test_the_entsoe_token_never_reaches_a_handler(captured):
    out = captured(HTTPX_LINE)

    assert FAKE_TOKEN not in out
    assert "securityToken=<redacted>" in out
    # …and the rest of the line survives, because a log you cannot read is its own outage.
    assert "documentType=A09" in out
    assert "10YHU-MAVIR----U" in out


def test_a_token_in_an_exception_message_is_redacted_too(captured):
    """Silencing httpx would have fixed the symptom. The leak was never really about httpx —
    a raised URL, a debug line, a retry warning all carry the same query string."""
    out = captured("ENTSO-E fetch failed for %s", HTTPX_LINE)
    assert FAKE_TOKEN not in out


@pytest.mark.parametrize("param", ["securityToken", "api_key", "apikey", "token", "x-key"])
def test_every_credential_shaped_parameter_is_covered(captured, param):
    out = captured(f"GET https://example.test/api?{param}=SUPERSECRET&days=7")
    assert "SUPERSECRET" not in out
    assert "days=7" in out, "only the secret goes"


def test_an_ordinary_line_is_untouched(captured):
    """A redactor that mangles normal logs gets turned off, and then it protects nothing."""
    msg = "power daily grid ingest [DE_LU]: {'days': 7, 'written': 168}"
    assert captured(msg) == msg


def test_every_batch_script_installs_the_redaction():
    """The web app and the batch scripts configure logging separately, and it is the SCRIPTS
    that make thousands of ENTSO-E calls in a row. A redaction that only protects the app
    protects the smaller half."""
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[1]
    for path in [
        "scripts/power_backfill.py",
        "scripts/gas_backfill.py",
        "scripts/repair_storage_series.py",
        "scripts/backfill_gie_countries.py",
        "ingest_main.py",
    ]:
        src = (root / path).read_text()
        assert "logging.basicConfig" in src, f"{path} no longer configures logging — recheck"
        assert "install_log_redaction()" in src, f"{path} logs unredacted"
