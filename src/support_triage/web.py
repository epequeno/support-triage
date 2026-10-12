"""The web UI (M3): deliberately thin, because the point is to make the rest legible in three
minutes — a case in, a grounded record out, and the one screen where a human approves a state
change by name.

Nothing here is persistent across restarts: the last triage record per case lives in memory, and
the only durable state is the approvals table, written by `tools.execute` exactly as the CLI
writes it. The approval endpoint takes an index into the *server's* record, not a client-supplied
payload — the browser never gets to invent a state change.
"""

from __future__ import annotations

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from . import backend, costs, tools
from .models import TriageRecord

app = FastAPI(title="support-triage")

# SQLite connections are thread-bound and FastAPI runs sync endpoints in a threadpool, so each
# request opens its own connection — connecting to a local SQLite file costs microseconds, and a
# shared connection across threads is the kind of cleverness this UI is deliberately too thin for.

# case_id -> the record the agent most recently produced. The approve step reads proposals from
# here, so what gets executed is exactly what the agent proposed — not what the browser posts.
_records: dict[str, TriageRecord] = {}

class ApprovalRequest(BaseModel):
    case_id: str
    index: int
    approved_by: str


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return PAGE


@app.get("/api/cases")
def cases() -> list[dict]:
    conn = backend.connect()
    rows = []
    for case in backend.all_cases(conn):
        acct = backend.account(conn, case["account_id"]) or {}
        rows.append({"case_id": case["case_id"], "subject": case["subject"],
                     "account": acct.get("name", ""), "plan": acct.get("plan", ""),
                     "declared_severity": case.get("declared_severity")})
    return rows


@app.get("/api/ledger")
def ledger() -> dict:
    conn = backend.connect()
    costs.ensure_cost_log(conn)
    rows = list(conn.execute(
        "SELECT tier, COUNT(*), SUM(input_tokens), SUM(output_tokens), SUM(cost_usd)"
        " FROM cost_log GROUP BY tier ORDER BY tier"))
    return {"tiers": [{"tier": r[0], "calls": r[1], "in": r[2], "out": r[3], "cost": r[4]} for r in rows],
            "total_usd": round(sum(r[4] or 0 for r in rows), 4)}


@app.post("/api/triage/{case_id}", response_model=TriageRecord)
def triage(case_id: str) -> TriageRecord:
    from .agent import build_agent  # late, so the app serves without an API key
    try:
        record = build_agent(backend.connect()).triage(case_id)
    except Exception as exc:  # noqa: BLE001 — a failed triage is a result, shown like one
        raise HTTPException(status_code=500, detail=f"{type(exc).__name__}: {exc}") from exc
    _records[case_id] = record
    return record


@app.post("/api/approve")
def approve(body: ApprovalRequest) -> dict:
    record = _records.get(body.case_id)
    if record is None or not 0 <= body.index < len(record.proposals):
        raise HTTPException(status_code=404, detail="no such proposal — triage the case first")
    try:
        conn = backend.connect()
        executed = tools.execute(conn, record.proposals[body.index], body.approved_by)
    except PermissionError as exc:  # the empty-name case: the gate, doing its job
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"executed": executed.model_dump(), "trail": backend.approvals(conn, body.case_id)}


@app.get("/api/approvals/{case_id}")
def approvals(case_id: str) -> list[dict]:
    return backend.approvals(backend.connect(), case_id)


PAGE = """<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>support-triage</title>
<style>
  :root { color-scheme: light dark; }
  body { font-family: ui-sans-serif, system-ui, sans-serif; margin: 0 auto; max-width: 60rem;
         padding: 1.5rem 1rem 4rem; line-height: 1.45; }
  h1 { font-size: 1.3rem; margin: 0 0 .2rem; }
  .sub { color: #777; margin: 0 0 1.5rem; font-size: .95rem; }
  table { border-collapse: collapse; width: 100%; font-size: .92rem; }
  th, td { text-align: left; padding: .35rem .6rem; border-bottom: 1px solid #8884; }
  button { cursor: pointer; }
  .case-btn { padding: .15rem .7rem; }
  #record { margin-top: 2rem; }
  .kv { display: grid; grid-template-columns: 11rem 1fr; gap: .15rem .5rem; margin: 1rem 0; }
  .kv div:nth-child(odd) { color: #777; }
  .panel { border: 1px solid #8886; border-radius: .5rem; padding: .8rem 1rem; margin: 1rem 0; }
  .panel h3 { margin: 0 0 .5rem; font-size: 1rem; }
  pre { white-space: pre-wrap; margin: .4rem 0; font-size: .85rem; }
  .proposal { border-top: 1px solid #8884; padding-top: .7rem; margin-top: .7rem; }
  .meta { color: #777; font-size: .88rem; }
  .gated { color: #a00; font-weight: 600; }
  input { padding: .2rem .4rem; }
  .err { color: #a00; margin: .5rem 0; }
  .ok { color: #060; }
</style></head><body>
<h1>support-triage</h1>
<p class="sub">A case in plain language in, a grounded triage record out. A commitment may only be
made if the policy and the entitlement support it — and no state change happens without a named
human. <span id="ledger"></span></p>

<table id="cases"><thead><tr>
  <th>case</th><th>subject</th><th>account</th><th>plan</th><th></th>
</tr></thead><tbody></tbody></table>

<div id="error" class="err"></div>
<div id="record"></div>

<script>
const $ = (sel) => document.querySelector(sel);
const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));

async function loadCases() {
  const rows = await (await fetch('/api/cases')).json();
  $('#cases tbody').innerHTML = rows.map((c) => `
    <tr><td><code>${esc(c.case_id)}</code></td><td>${esc(c.subject)}</td>
    <td>${esc(c.account)}</td><td>${esc(c.plan)}</td>
    <td><button class="case-btn" onclick="triage('${esc(c.case_id)}')">triage</button></td></tr>`).join('');
}

async function loadLedger() {
  const l = await (await fetch('/api/ledger')).json();
  $('#ledger').textContent = l.total_usd > 0
    ? `Model spend to date: $${l.total_usd.toFixed(4)}.` : '';
}

async function loadTrail(caseId) {
  const rows = await (await fetch('/api/approvals/' + caseId)).json();
  return rows.length
    ? `<div class="panel"><h3>approved state changes (the audit trail)</h3>` +
      rows.map((r) => `<div><code>${esc(r.action)}</code> ${esc(r.payload)} —
        by <b>${esc(r.approved_by)}</b> at ${esc(r.executed_at)}</div>`).join('') + `</div>`
    : '';
}

async function triage(caseId) {
  $('#error').textContent = ''; $('#record').textContent = 'triaging ' + caseId + ' (real model, real spend)…';
  try {
    const r = await (await fetch('/api/triage/' + caseId, {method: 'POST'})).json();
    if (!r.case_id) throw new Error(r.detail || 'triage failed');
    render(r);
  } catch (e) { $('#record').textContent = ''; $('#error').textContent = e.message; }
}

function render(r) {
  const sla = r.sla_response_hours === null ? 'none (not entitled)' : r.sla_response_hours + 'h';
  const cites = r.citations.map((c) => `<code>${esc(c)}</code>`).join(', ');
  const notes = r.notes.length
    ? `<div class="panel"><h3>notes</h3>${r.notes.map((n) => `<div class="meta">${esc(n)}</div>`).join('')}</div>` : '';
  const proposals = r.proposals.length
    ? `<div class="panel"><h3><span class="gated">proposed state changes — NOT executed, a human approves these</span></h3>` +
      r.proposals.map((p, i) => `
        <div class="proposal">
          <div><code>${esc(p.action)}</code> ${esc(JSON.stringify(p.payload))}</div>
          <div class="meta">${esc(p.rationale)}</div>
          <div><input id="approver-${i}" placeholder="your name" size="12">
            <button onclick="approve('${esc(r.case_id)}', ${i})">approve</button></div>
        </div>`).join('') + `</div>`
    : `<p class="meta">no state changes proposed</p>`;
  $('#record').innerHTML = `
    <div class="kv">
      <div>case</div><div><b>${esc(r.case_id)}</b> · ${esc(r.account_id)} · ${esc(r.plan)} plan</div>
      <div>classification</div><div>${esc(r.severity)} (${esc(r.severity_label)})</div>
      <div>in scope</div><div>${r.in_scope}</div>
      <div>first response</div><div>${esc(sla)}</div>
      <div>next action</div><div>${esc(r.next_action)}</div>
      <div>cites</div><div>${cites}</div>
      <div>routed to</div><div>${esc(r.routing.tier)} model — ${esc(r.routing.reasons.join('; '))}</div>
      <div>cost</div><div>$${r.cost_usd.toFixed(6)} · ${r.model_calls} call(s)
        · ${r.input_tokens} in / ${r.output_tokens} out</div>
    </div>
    ${r.account_facts.length ? `<div class="panel"><h3>grounded in</h3>` +
      r.account_facts.map((f) => `<div class="meta">- ${esc(f)}</div>`).join('') + `</div>` : ''}
    ${notes}
    <div class="panel"><h3>draft reply</h3><pre>${esc(r.draft_reply)}</pre></div>
    ${proposals}
    <div id="trail"></div>`;
  loadTrail(r.case_id).then((html) => $('#trail').innerHTML = html);
}

async function approve(caseId, index) {
  const name = $('#approver-' + index).value;
  try {
    const res = await fetch('/api/approve', {method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({case_id: caseId, index: index, approved_by: name})});
    const out = await res.json();
    if (!res.ok) throw new Error(out.detail || 'approval failed');
    $('#approver-' + index).replaceWith(document.createTextNode(name));
    $('#trail').innerHTML = `<div class="panel"><h3>approved state changes (the audit trail)</h3>` +
      out.trail.map((r) => `<div><code>${esc(r.action)}</code> ${esc(r.payload)} —
        by <b>${esc(r.approved_by)}</b> at ${esc(r.executed_at)}</div>`).join('') + `</div>`;
  } catch (e) { $('#error').textContent = e.message; }
}

loadCases(); loadLedger();
</script>
</body></html>
"""
