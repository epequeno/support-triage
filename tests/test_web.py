"""The API gate: a caller token on every /api route, and fail-closed without one."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from support_triage import backend, seed, web
from support_triage.models import Proposal, TriageRecord


@pytest.fixture()
def web_db(tmp_path, monkeypatch):
    path = tmp_path / "web.db"
    conn = backend.reset(path)
    seed.seed(conn)
    conn.close()
    monkeypatch.setattr(backend, "DB", path)
    yield path


def test_the_page_is_open_but_the_api_requires_a_token(web_db, monkeypatch):
    monkeypatch.setenv("APP_TOKEN", "smoke-token")
    client = TestClient(web.app)
    assert client.get("/").status_code == 200
    assert client.get("/api/cases").status_code == 401
    assert client.get("/api/cases", headers={"Authorization": "Bearer wrong"}).status_code == 401
    ok = client.get("/api/cases", headers={"Authorization": "Bearer smoke-token"})
    assert ok.status_code == 200
    assert isinstance(ok.json(), list) and ok.json()


def test_the_api_fails_closed_without_a_configured_token(web_db, monkeypatch):
    monkeypatch.delenv("APP_TOKEN", raising=False)
    client = TestClient(web.app)
    res = client.get("/api/cases")
    assert res.status_code == 503
    assert "APP_TOKEN" in res.json()["detail"]


def test_approve_requires_the_token_and_records_through_the_store(web_db, monkeypatch):
    monkeypatch.setenv("APP_TOKEN", "smoke-token")
    monkeypatch.delenv("APPROVALS_BUCKET", raising=False)
    proposal = Proposal(case_id="CASE-1041", action="escalate",
                        payload={"team": "on-call"}, rationale="policy says so")
    record = TriageRecord.model_construct(case_id="CASE-1041", proposals=[proposal])
    monkeypatch.setitem(web._records, "CASE-1041", record)
    client = TestClient(web.app)
    body = {"case_id": "CASE-1041", "index": 0, "approved_by": "R. Alvarez"}
    assert client.post("/api/approve", json=body).status_code == 401
    ok = client.post("/api/approve", json=body, headers={"Authorization": "Bearer smoke-token"})
    assert ok.status_code == 200
    trail = ok.json()["trail"]
    assert len(trail) == 1 and trail[0]["approved_by"] == "R. Alvarez"
    assert trail[0]["action"] == "escalate"
