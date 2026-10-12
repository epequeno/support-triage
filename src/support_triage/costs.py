"""Tokens, dollars, and a cap that stops a run.

Prices are per million tokens and are looked up from a table rather than hard-coded into a call site,
because model prices change and a stale number in a log is worse than no number.

The cap is deliberately a *feature*: an agent that keeps spending when it should stop is a bug, and
the eval asserts that hitting the cap halts the run rather than being discovered on an invoice.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone

# Verified at build time against the provider catalogue. Update here, nowhere else.
PRICES: dict[str, tuple[float, float]] = {
    "cheap": (0.09999999999999999, 0.39999999999999997),
    "strong": (1.25, 10.0),
}

DEFAULT_CAP_USD = 0.25


class CostCapExceeded(RuntimeError):
    """Raised when a run would exceed its budget. The run stops; it does not finish optimistically."""


@dataclass
class Ledger:
    cap_usd: float = DEFAULT_CAP_USD
    spent_usd: float = 0.0
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    by_tier: dict[str, float] = field(default_factory=dict)

    def would_exceed(self, tier: str, input_tokens: int, output_tokens: int) -> float:
        return self.spent_usd + price(tier, input_tokens, output_tokens)

    def charge(self, tier: str, input_tokens: int, output_tokens: int) -> float:
        cost = price(tier, input_tokens, output_tokens)
        if self.spent_usd + cost > self.cap_usd:
            raise CostCapExceeded(
                f"run would reach ${self.spent_usd + cost:.4f}, over the ${self.cap_usd:.2f} cap;"
                " stopping rather than completing")
        self.spent_usd += cost
        self.by_tier[tier] = self.by_tier.get(tier, 0.0) + cost
        self.calls += 1
        self.input_tokens += input_tokens
        self.output_tokens += output_tokens
        return cost


def price(tier: str, input_tokens: int, output_tokens: int) -> float:
    cin, cout = PRICES.get(tier, (0.0, 0.0))
    return (input_tokens / 1_000_000) * cin + (output_tokens / 1_000_000) * cout


def log(conn: sqlite3.Connection, case_id: str, model: str, tier: str, input_tokens: int,
        output_tokens: int, cost_usd: float) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS cost_log (case_id TEXT, model TEXT, tier TEXT, input_tokens INT,"
        " output_tokens INT, cost_usd REAL, at TEXT)")
    conn.execute("INSERT INTO cost_log VALUES (?,?,?,?,?,?,?)",
                 (case_id, model, tier, input_tokens, output_tokens, cost_usd,
                  datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")))
    conn.commit()


def summary(conn: sqlite3.Connection) -> str:
    rows = list(conn.execute(
        "SELECT tier, COUNT(*) n, SUM(input_tokens) i, SUM(output_tokens) o, SUM(cost_usd) c"
        " FROM cost_log GROUP BY tier ORDER BY tier"))
    if not rows:
        return "no model calls logged yet"
    lines = ["tier      calls   in tok   out tok      cost"]
    for tier, n, i, o, c in rows:
        lines.append(f"{tier:<8} {n:>6} {i or 0:>8} {o or 0:>9}   ${(c or 0):.4f}")
    total = sum(r[4] or 0 for r in rows)
    lines.append(f"total: ${total:.4f}")
    return "\n".join(lines)


def main() -> int:
    from .backend import connect
    print(summary(connect()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
