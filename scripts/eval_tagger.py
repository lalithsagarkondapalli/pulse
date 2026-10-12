"""Measure a tagger against hand-labelled comments before trusting it.

    python -m scripts.eval_tagger                 # rule baseline
    ANTHROPIC_API_KEY=... ANTHROPIC_MODEL=... python -m scripts.eval_tagger   # Claude

Prints theme accuracy, sentiment accuracy, a per-theme breakdown and every
miss, so a regression is visible as specific examples, not just a number."""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

from app.themes import default_tagger

LABELS = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "labeled_feedback.jsonl"


def main() -> None:
    tagger = default_tagger()
    rows = [json.loads(line) for line in LABELS.read_text().splitlines() if line.strip()]
    theme_ok = sent_ok = 0
    per_theme: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    misses = []
    for r in rows:
        t = tagger.tag(r["comment"], r["score"])
        hit = t.theme == r["theme"]
        theme_ok += hit
        sent_ok += t.sentiment == r["sentiment"]
        per_theme[r["theme"]][0] += hit
        per_theme[r["theme"]][1] += 1
        if not hit:
            misses.append((r["comment"], r["theme"], t.theme))
    n = len(rows)
    print(f"tagger={tagger.name}  n={n}")
    print(f"theme accuracy     {theme_ok / n:.0%}")
    print(f"sentiment accuracy {sent_ok / n:.0%}\n")
    for theme, (ok, total) in sorted(per_theme.items()):
        print(f"  {theme:<18} {ok}/{total}")
    if misses:
        print("\nmisses (comment | expected -> got):")
        for c, exp, got in misses:
            print(f"  {c[:70]!r} | {exp} -> {got}")


if __name__ == "__main__":
    main()
