"""Reward issuance. One reward per customer per survey, enforced by a UNIQUE
constraint rather than application logic, so a double-submit or a retry can
never mint two codes."""

from __future__ import annotations

import hashlib
import secrets
import sqlite3


def email_hash(email: str) -> str:
    return hashlib.sha256(email.strip().lower().encode()).hexdigest()


def new_code() -> str:
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O/1/I confusion
    return "PULSE-" + "".join(secrets.choice(alphabet) for _ in range(8))


def issue(conn, survey_id: int, ehash: str, points: int) -> tuple[str, bool]:
    """Return (code, newly_issued). Idempotent per (survey, customer)."""
    existing = conn.execute(
        "SELECT code FROM rewards WHERE survey_id = ? AND email_hash = ?", (survey_id, ehash)
    ).fetchone()
    if existing:
        return existing["code"], False
    code = new_code()
    try:
        conn.execute(
            "INSERT INTO rewards (survey_id, email_hash, code, points) VALUES (?, ?, ?, ?)",
            (survey_id, ehash, code, points),
        )
    except sqlite3.IntegrityError:
        # Lost a race with a concurrent submit: return the winner's code.
        row = conn.execute(
            "SELECT code FROM rewards WHERE survey_id = ? AND email_hash = ?", (survey_id, ehash)
        ).fetchone()
        return row["code"], False
    return code, True
