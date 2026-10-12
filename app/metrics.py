"""NPS and theme metrics. Pure functions over rows so they are trivial to test."""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from typing import Iterable, Mapping


def bucket(score: int) -> str:
    if score >= 9:
        return "promoter"
    if score >= 7:
        return "passive"
    return "detractor"


def nps(scores: Iterable[int]) -> dict:
    """Net Promoter Score with a 95% CI (normal approximation on the
    promoter/detractor difference). Returns None for nps when n == 0."""
    scores = list(scores)
    n = len(scores)
    if n == 0:
        return {"n": 0, "nps": None, "ci_low": None, "ci_high": None}
    p = sum(s >= 9 for s in scores) / n
    d = sum(s <= 6 for s in scores) / n
    value = (p - d) * 100
    # Var(P - D) for a multinomial = [p + d - (p - d)^2] / n
    se = math.sqrt(max(p + d - (p - d) ** 2, 0.0) / n) * 100
    return {
        "n": n,
        "nps": round(value, 1),
        "ci_low": round(max(value - 1.96 * se, -100), 1),
        "ci_high": round(min(value + 1.96 * se, 100), 1),
    }


def weekly_nps(rows: Iterable[Mapping]) -> list[dict]:
    """rows need 'created_at' (ISO string) and 'score'. Groups by ISO week."""
    from datetime import datetime

    weeks: dict[str, list[int]] = defaultdict(list)
    for r in rows:
        dt = datetime.fromisoformat(str(r["created_at"]).replace(" ", "T"))
        year, week, _ = dt.isocalendar()
        weeks[f"{year}-W{week:02d}"].append(int(r["score"]))
    return [{"week": w, **nps(s)} for w, s in sorted(weeks.items())]


def theme_breakdown(rows: Iterable[Mapping]) -> list[dict]:
    """Per theme: volume, share of detractors, and NPS. Sorted by how many
    detractors the theme accounts for, because that is what a team should fix first."""
    by_theme: dict[str, list[int]] = defaultdict(list)
    for r in rows:
        if r["theme"] and r["theme"] != "no_comment":
            by_theme[r["theme"]].append(int(r["score"]))
    total_detractors = sum(1 for s in (x for v in by_theme.values() for x in v) if s <= 6) or 1
    out = []
    for theme, scores in by_theme.items():
        det = sum(s <= 6 for s in scores)
        out.append(
            {
                "theme": theme,
                "n": len(scores),
                "detractors": det,
                "detractor_share": round(det / total_detractors * 100, 1),
                "nps": nps(scores)["nps"],
            }
        )
    return sorted(out, key=lambda t: (-t["detractors"], -t["n"]))


def sentiment_counts(rows: Iterable[Mapping]) -> dict:
    c = Counter(r["sentiment"] for r in rows if r["sentiment"])
    return {k: c.get(k, 0) for k in ("positive", "neutral", "negative")}
