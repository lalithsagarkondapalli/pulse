"""Pulse: collect NPS feedback, reward the customer, tag the comment, and show
the team what to fix and who to follow up with."""

from __future__ import annotations

import os
import re
import secrets
import sqlite3
from pathlib import Path

from fastapi import BackgroundTasks, Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import db, metrics, rewards
from .themes import RuleTagger, default_tagger

HERE = Path(__file__).parent
templates = Jinja2Templates(directory=HERE / "templates")


def slugify(text: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40] or "survey"
    return f"{base}-{secrets.token_hex(3)}"


def create_app(conn: db.SafeConnection | None = None, tagger=None) -> FastAPI:
    conn = conn or db.connect()
    db.init(conn)
    tagger = tagger or default_tagger()
    admin_token = os.environ.get("PULSE_ADMIN_TOKEN")

    app = FastAPI(title="Pulse")
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
    app.state.conn = conn
    app.state.tagger = tagger

    # ------------------------------------------------------------------ auth
    def require_admin(request: Request) -> None:
        """Brand-side pages are behind a shared token when PULSE_ADMIN_TOKEN is
        set. Deliberately minimal - see DECISIONS.md for what production needs."""
        if not admin_token:
            return
        supplied = request.cookies.get("pulse_admin") or request.headers.get("x-pulse-admin")
        if not supplied or not secrets.compare_digest(supplied, admin_token):
            raise HTTPException(status_code=401, detail="admin token required")

    @app.get("/login")
    def login(token: str):
        if admin_token and not secrets.compare_digest(token, admin_token):
            raise HTTPException(status_code=401)
        resp = RedirectResponse("/", status_code=303)
        resp.set_cookie("pulse_admin", token, httponly=True, samesite="lax")
        return resp

    # --------------------------------------------------------------- helpers
    def get_survey(slug: str) -> sqlite3.Row:
        row = conn.execute("SELECT * FROM surveys WHERE slug = ?", (slug,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="survey not found")
        return row

    def tag_response(response_id: int) -> None:
        row = conn.execute("SELECT comment, score FROM responses WHERE id = ?", (response_id,)).fetchone()
        if not row:
            return
        try:
            t = app.state.tagger.tag(row["comment"], row["score"])
        except Exception:
            t = RuleTagger().tag(row["comment"], row["score"])
        with db.tx(conn):
            conn.execute(
                "UPDATE responses SET theme = ?, sentiment = ?, tagged_by = ? WHERE id = ?",
                (t.theme, t.sentiment, t.source, response_id),
            )

    def summary(survey: sqlite3.Row) -> dict:
        rows = conn.execute(
            "SELECT * FROM responses WHERE survey_id = ? ORDER BY created_at", (survey["id"],)
        ).fetchall()
        detractors = [
            r for r in rows if r["score"] <= 6 and not r["followed_up"] and r["contact_email"]
        ]
        return {
            "overall": metrics.nps(r["score"] for r in rows),
            "weekly": metrics.weekly_nps(rows),
            "themes": metrics.theme_breakdown(rows),
            "sentiment": metrics.sentiment_counts(rows),
            "pending_tags": sum(1 for r in rows if r["theme"] is None),
            "follow_up": [dict(r) for r in reversed(detractors)][:25],
            "rewards_issued": conn.execute(
                "SELECT COUNT(*) FROM rewards WHERE survey_id = ?", (survey["id"],)
            ).fetchone()[0],
            "recent": [dict(r) for r in reversed(rows[-8:])],
        }

    # ----------------------------------------------------------- brand pages
    @app.get("/", response_class=HTMLResponse, dependencies=[Depends(require_admin)])
    def index(request: Request):
        surveys = conn.execute(
            """SELECT s.*, COUNT(r.id) AS n FROM surveys s
               LEFT JOIN responses r ON r.survey_id = s.id
               GROUP BY s.id ORDER BY s.created_at DESC"""
        ).fetchall()
        return templates.TemplateResponse(request, "index.html", {"surveys": surveys})

    @app.post("/surveys", dependencies=[Depends(require_admin)])
    def create_survey(
        brand: str = Form(..., max_length=80),
        question: str = Form(..., max_length=200),
        reward_points: int = Form(50, ge=0, le=10000),
    ):
        slug = slugify(brand)
        with db.tx(conn):
            conn.execute(
                "INSERT INTO surveys (slug, brand, question, reward_points) VALUES (?, ?, ?, ?)",
                (slug, brand.strip(), question.strip(), reward_points),
            )
        return RedirectResponse(f"/dashboard/{slug}", status_code=303)

    @app.get("/dashboard/{slug}", response_class=HTMLResponse, dependencies=[Depends(require_admin)])
    def dashboard(request: Request, slug: str):
        survey = get_survey(slug)
        return templates.TemplateResponse(
            request, "dashboard.html", {"survey": survey, "s": summary(survey)}
        )

    @app.post("/responses/{response_id}/follow-up", dependencies=[Depends(require_admin)])
    def follow_up(response_id: int):
        row = conn.execute(
            "SELECT s.slug FROM responses r JOIN surveys s ON s.id = r.survey_id WHERE r.id = ?",
            (response_id,),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404)
        with db.tx(conn):
            conn.execute("UPDATE responses SET followed_up = 1 WHERE id = ?", (response_id,))
        return RedirectResponse(f"/dashboard/{row['slug']}#follow-up", status_code=303)

    @app.post("/dashboard/{slug}/retag", dependencies=[Depends(require_admin)])
    def retag(slug: str, background: BackgroundTasks):
        survey = get_survey(slug)
        for (rid,) in conn.execute(
            "SELECT id FROM responses WHERE survey_id = ? AND theme IS NULL", (survey["id"],)
        ).fetchall():
            background.add_task(tag_response, rid)
        return RedirectResponse(f"/dashboard/{slug}", status_code=303)

    @app.get("/api/surveys/{slug}/summary", dependencies=[Depends(require_admin)])
    def api_summary(slug: str):
        s = summary(get_survey(slug))
        s.pop("follow_up")  # contact details stay out of the API
        s.pop("recent")
        return JSONResponse(s)

    # -------------------------------------------------------- customer pages
    @app.get("/s/{slug}", response_class=HTMLResponse)
    def survey_form(request: Request, slug: str):
        return templates.TemplateResponse(request, "survey.html", {"survey": get_survey(slug)})

    @app.post("/s/{slug}", response_class=HTMLResponse)
    def submit(
        request: Request,
        slug: str,
        background: BackgroundTasks,
        score: int = Form(..., ge=0, le=10),
        comment: str = Form("", max_length=2000),
        email: str = Form("", max_length=254),
        contact_ok: bool = Form(False),
        website: str = Form(""),  # honeypot: humans never see this field
    ):
        survey = get_survey(slug)
        if website:
            # Pretend success so bots get no signal; store nothing.
            return templates.TemplateResponse(request, "thanks.html", {"survey": survey, "code": None})
        email = email.strip()
        if email and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
            raise HTTPException(status_code=422, detail="invalid email")
        ehash = rewards.email_hash(email) if email else None
        code = None
        with db.tx(conn):
            cur = conn.execute(
                "INSERT INTO responses (survey_id, score, comment, email_hash, contact_email)"
                " VALUES (?, ?, ?, ?, ?)",
                (survey["id"], score, comment.strip(), ehash, email if (email and contact_ok) else None),
            )
            if ehash and survey["reward_points"] > 0:
                code, _ = rewards.issue(conn, survey["id"], ehash, survey["reward_points"])
        # Tag after the response is returned: the customer never waits on the LLM.
        background.add_task(tag_response, cur.lastrowid)
        return templates.TemplateResponse(
            request, "thanks.html", {"survey": survey, "code": code, "score": score}
        )

    @app.get("/healthz")
    def healthz():
        conn.execute("SELECT 1")
        return {"ok": True, "tagger": getattr(app.state.tagger, "name", "unknown")}

    return app


app = create_app() if os.environ.get("PULSE_AUTOCREATE", "1") == "1" else None
