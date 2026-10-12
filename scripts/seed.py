"""Seed a demo survey with ~10 weeks of synthetic responses.

    python -m scripts.seed            # writes to $PULSE_DB (default pulse.db)

All data is synthetic. Delivery complaints are made to spike in weeks 6-7 so
the dashboard has a real story to show (a theme that moves the score)."""

from __future__ import annotations

import random
from datetime import datetime, timedelta

from app import db, rewards
from app.themes import default_tagger

# (positive comments, negative comments) per theme
COMMENTS = {
    "delivery": (
        ["Shipping was fast, arrived in two days", "Delivery was quicker than promised"],
        ["Package arrived a week late and tracking never updated", "Courier left it at the wrong door",
         "Delivery took forever, had to chase it"],
    ),
    "product_quality": (
        ["Love the quality, the jacket feels durable", "Material is great, exactly as pictured"],
        ["Zipper broke after two weeks", "Size runs small, the fit was wrong"],
    ),
    "price_value": (
        ["Great value for the price"],
        ["A bit expensive for what you get", "Overpriced compared to other stores"],
    ),
    "customer_support": (
        ["Support agent was super helpful on chat"],
        ["Waited 40 minutes for a response from customer service", "Rude agent on the phone"],
    ),
    "website_app": (
        ["Website is easy to use"],
        ["Checkout kept crashing on the app", "Login page is slow"],
    ),
    "rewards_loyalty": (
        ["Love collecting points, the member perks are great"],
        ["Couldn't figure out how to use my reward points"],
    ),
    "returns_refunds": (
        ["Return was easy, free label in the box"],
        ["Refund took three weeks to show up"],
    ),
}


def main(n: int = 420, seed: int = 7) -> None:
    rng = random.Random(seed)
    conn = db.connect()
    db.init(conn)
    tagger = default_tagger()
    with db.tx(conn):
        conn.execute(
            "INSERT OR IGNORE INTO surveys (slug, brand, question, reward_points) VALUES (?,?,?,?)",
            ("acme-outdoor-demo", "Acme Outdoor", "How likely are you to recommend Acme to a friend?", 50),
        )
    survey_id = conn.execute("SELECT id FROM surveys WHERE slug='acme-outdoor-demo'").fetchone()[0]
    start = datetime.now() - timedelta(weeks=10)

    for i in range(n):
        day = rng.randint(0, 69)
        week = day // 7
        incident = week in (5, 6)  # the delivery incident
        weights = {t: 1.0 for t in COMMENTS}
        if incident:
            weights["delivery"] = 4.0
        theme = rng.choices(list(weights), weights=list(weights.values()))[0]
        p_negative = 0.85 if (incident and theme == "delivery") else 0.25
        negative = rng.random() < p_negative
        comment = rng.choice(COMMENTS[theme][1 if negative else 0])
        score = rng.randint(0, 6) if negative else rng.choice([7, 8, 9, 9, 10, 10])
        if rng.random() < 0.15:
            comment = ""  # some people only leave a score
        email = f"customer{i}@example.com" if rng.random() < 0.6 else ""
        contact = email if (email and rng.random() < 0.5) else None
        created = (start + timedelta(days=day, minutes=rng.randint(0, 1439))).strftime("%Y-%m-%d %H:%M:%S")
        t = tagger.tag(comment, score)
        with db.tx(conn):
            conn.execute(
                """INSERT INTO responses (survey_id, score, comment, email_hash, contact_email,
                   created_at, theme, sentiment, tagged_by, followed_up)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (survey_id, score, comment, rewards.email_hash(email) if email else None, contact,
                 created, t.theme, t.sentiment, t.source, int(day < 50)),
            )
            if email:
                rewards.issue(conn, survey_id, rewards.email_hash(email), 50)
    print(f"Seeded {n} responses into survey 'acme-outdoor-demo' using the {tagger.name} tagger.")
    print("Open http://127.0.0.1:8000/dashboard/acme-outdoor-demo")


if __name__ == "__main__":
    main()
