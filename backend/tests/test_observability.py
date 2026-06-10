"""
Tests for the trace-ID middleware + structured logger.

We don't try to stress the JSON formatter end-to-end with capsys; we
just verify the contextvar is set during a request and the response
exposes X-Trace-Id.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.observability import TraceIDMiddleware, _JsonFormatter, trace_id_var


@pytest.fixture
def app_with_middleware():
    app = FastAPI()
    app.add_middleware(TraceIDMiddleware)

    @app.get("/_probe")
    def probe():
        return {"trace_id_in_handler": trace_id_var.get()}

    return app


def test_generates_trace_id_and_returns_header(app_with_middleware):
    client = TestClient(app_with_middleware)
    resp = client.get("/_probe")
    assert resp.status_code == 200
    trace_id = resp.headers["X-Trace-Id"]
    assert trace_id, "X-Trace-Id header missing"
    # Handler saw the same id via contextvar
    assert resp.json()["trace_id_in_handler"] == trace_id


def test_honours_inbound_trace_id_if_uuidish(app_with_middleware):
    client = TestClient(app_with_middleware)
    inbound = "abcdef1234567890abcdef1234567890"
    resp = client.get("/_probe", headers={"X-Trace-Id": inbound})
    assert resp.headers["X-Trace-Id"] == inbound
    assert resp.json()["trace_id_in_handler"] == inbound


def test_rejects_garbage_trace_id_and_makes_new(app_with_middleware):
    client = TestClient(app_with_middleware)
    resp = client.get("/_probe", headers={"X-Trace-Id": "drop table users;--"})
    assert resp.headers["X-Trace-Id"] != "drop table users;--"
    assert len(resp.headers["X-Trace-Id"]) >= 16


def test_context_var_is_cleared_after_request(app_with_middleware):
    client = TestClient(app_with_middleware)
    client.get("/_probe")
    # After the request returns, the var should fall back to default
    assert trace_id_var.get() is None


def test_json_formatter_emits_one_line_and_contains_trace_id():
    import logging
    from io import StringIO

    stream = StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(_JsonFormatter())

    logger = logging.getLogger("obsyd.test.json")
    logger.handlers = [handler]
    logger.setLevel(logging.INFO)
    logger.propagate = False

    token = trace_id_var.set("test-trace-abcd")
    try:
        logger.info("hello", extra={"event": "probe", "rule_id": 7})
    finally:
        trace_id_var.reset(token)

    raw = stream.getvalue().strip()
    assert "\n" not in raw, "JSON formatter must emit single-line records"

    import json

    payload = json.loads(raw)
    assert payload["msg"] == "hello"
    assert payload["lvl"] == "INFO"
    assert payload["trace_id"] == "test-trace-abcd"
    assert payload["event"] == "probe"
    assert payload["rule_id"] == 7
