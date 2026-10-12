"""The mock support organisation, backed by SQLite.

Deliberately a *system*: accounts with plans and entitlements, incidents, service status, case history.
The agent has to look things up, because the answers are not in the case text.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

DB = Path(__file__).resolve().parents[2] / "supportorg.db"

SCHEMA = """
CREATE TABLE accounts (account_id TEXT PRIMARY KEY, name TEXT, plan TEXT, region TEXT,
                       tam TEXT, production_critical INTEGER, notes TEXT);
CREATE TABLE entitlements (account_id TEXT PRIMARY KEY, technical_support INTEGER, intake TEXT,
                           workloads TEXT, named_tam INTEGER);
CREATE TABLE incidents (incident_id TEXT PRIMARY KEY, service TEXT, region TEXT, summary TEXT,
                        status TEXT, started TEXT);
CREATE TABLE service_status (service TEXT, region TEXT, status TEXT, PRIMARY KEY (service, region));
CREATE TABLE cases (case_id TEXT PRIMARY KEY, account_id TEXT, subject TEXT, body TEXT,
                    declared_severity TEXT, created TEXT, opened_by TEXT);
CREATE TABLE case_truth (case_id TEXT PRIMARY KEY, severity TEXT, in_scope INTEGER,
                         sla_response_hours REAL, next_action TEXT, citations TEXT,
                         note TEXT);
CREATE TABLE approvals (proposal_id INTEGER PRIMARY KEY AUTOINCREMENT, case_id TEXT, action TEXT,
                        payload TEXT, rationale TEXT, approved_by TEXT, executed_at TEXT);
"""


def connect(path: Path | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(path or DB)
    conn.row_factory = sqlite3.Row
    return conn


def reset(path: Path | None = None) -> sqlite3.Connection:
    conn = connect(path)
    conn.executescript("DROP TABLE IF EXISTS accounts; DROP TABLE IF EXISTS entitlements;"
                       "DROP TABLE IF EXISTS incidents; DROP TABLE IF EXISTS service_status;"
                       "DROP TABLE IF EXISTS cases; DROP TABLE IF EXISTS case_truth;"
                       "DROP TABLE IF EXISTS approvals;")
    conn.executescript(SCHEMA)
    return conn


# ── reads ────────────────────────────────────────────────────────────────────────────────────────

def account(conn, account_id: str) -> dict | None:
    row = conn.execute("SELECT * FROM accounts WHERE account_id = ?", (account_id,)).fetchone()
    return dict(row) if row else None


def entitlement(conn, account_id: str) -> dict | None:
    row = conn.execute("SELECT * FROM entitlements WHERE account_id = ?", (account_id,)).fetchone()
    return dict(row) if row else None


def get_case(conn, case_id: str) -> dict | None:
    row = conn.execute("SELECT * FROM cases WHERE case_id = ?", (case_id,)).fetchone()
    return dict(row) if row else None


def all_cases(conn) -> list[dict]:
    return [dict(r) for r in conn.execute("SELECT * FROM cases ORDER BY case_id")]


def truth(conn, case_id: str) -> dict | None:
    row = conn.execute("SELECT * FROM case_truth WHERE case_id = ?", (case_id,)).fetchone()
    return dict(row) if row else None


def open_incidents(conn, region: str | None = None) -> list[dict]:
    sql = "SELECT * FROM incidents WHERE status != 'resolved'"
    args: tuple = ()
    if region:
        sql += " AND region = ?"
        args = (region,)
    return [dict(r) for r in conn.execute(sql + " ORDER BY incident_id", args)]


def service_status(conn, region: str | None = None) -> list[dict]:
    sql = "SELECT * FROM service_status"
    args: tuple = ()
    if region:
        sql += " WHERE region = ?"
        args = (region,)
    return [dict(r) for r in conn.execute(sql + " ORDER BY service, region", args)]


def prior_cases(conn, account_id: str, exclude: str) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT case_id, subject, declared_severity, created FROM cases"
        " WHERE account_id = ? AND case_id != ? ORDER BY created DESC LIMIT 5", (account_id, exclude))]


# ── the only writes, and they are gated (see tools.py) ───────────────────────────────────────────

def record_approval(conn, case_id: str, action: str, payload: str, rationale: str, approved_by: str,
                    executed_at: str) -> int:
    cur = conn.execute(
        "INSERT INTO approvals (case_id, action, payload, rationale, approved_by, executed_at)"
        " VALUES (?, ?, ?, ?, ?, ?)", (case_id, action, payload, rationale, approved_by, executed_at))
    conn.commit()
    return int(cur.lastrowid)


def approvals(conn, case_id: str | None = None) -> list[dict]:
    sql = "SELECT * FROM approvals"
    args: tuple = ()
    if case_id:
        sql += " WHERE case_id = ?"
        args = (case_id,)
    return [dict(r) for r in conn.execute(sql + " ORDER BY proposal_id", args)]
