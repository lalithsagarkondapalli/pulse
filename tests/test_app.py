import os

os.environ["PULSE_AUTOCREATE"] = "0"

import pytest
from fastapi.testclient import TestClient

from app import db
from app.main import create_app
from app.themes import RuleTagger, Tag


@pytest.fixture
def client_and_conn(monkeypatch):
    monkeypatch.delenv("PULSE_ADMIN_TOKEN", raising=False)
    conn = db.connect(":memory:")
    app = create_app(conn, RuleTagger())
    return TestClient(app), conn


def make_survey(client, points=50):
    r = client.post("/surveys", data={"brand": "Acme", "question": "Recommend us?", "reward_points": points},
                    follow_redirects=False)
    assert r.status_code == 303
    return r.headers["location"].rsplit("/", 1)[-1]


def test_end_to_end_submit_tag_and_dashboard(client_and_conn):
    client, conn = client_and_conn
    slug = make_survey(client)
    assert client.get(f"/s/{slug}").status_code == 200

    r = client.post(f"/s/{slug}", data={"score": 3, "comment": "Package arrived two weeks late",
                                         "email": "a@b.com", "contact_ok": "true"})
    assert r.status_code == 200
    assert "PULSE-" in r.text

    row = conn.execute("SELECT * FROM responses").fetchone()
    assert row["theme"] == "delivery"          # background task ran
    assert row["tagged_by"] == "rules"
    assert row["contact_email"] == "a@b.com"   # consented
    assert row["email_hash"] != "a@b.com"

    dash = client.get(f"/dashboard/{slug}")
    assert dash.status_code == 200
    assert "Package arrived two weeks late" in dash.text
    assert "a@b.com" in dash.text              # shows in close-the-loop list


def test_email_not_stored_without_consent(client_and_conn):
    client, conn = client_and_conn
    slug = make_survey(client)
    client.post(f"/s/{slug}", data={"score": 2, "comment": "bad", "email": "x@y.com"})
    row = conn.execute("SELECT contact_email, email_hash FROM responses").fetchone()
    assert row["contact_email"] is None
    assert row["email_hash"]


def test_one_reward_per_customer_per_survey(client_and_conn):
    client, conn = client_and_conn
    slug = make_survey(client)
    a = client.post(f"/s/{slug}", data={"score": 9, "email": "Same@Example.com"})
    b = client.post(f"/s/{slug}", data={"score": 10, "email": " same@example.com "})
    code_a = a.text.split("PULSE-")[1][:8]
    code_b = b.text.split("PULSE-")[1][:8]
    assert code_a == code_b
    assert conn.execute("SELECT COUNT(*) FROM rewards").fetchone()[0] == 1


def test_no_reward_when_points_zero(client_and_conn):
    client, conn = client_and_conn
    slug = make_survey(client, points=0)
    r = client.post(f"/s/{slug}", data={"score": 9, "email": "a@b.com"})
    assert "PULSE-" not in r.text


def test_validation_rejects_bad_input(client_and_conn):
    client, _ = client_and_conn
    slug = make_survey(client)
    assert client.post(f"/s/{slug}", data={"score": 11}).status_code == 422
    assert client.post(f"/s/{slug}", data={"score": 5, "email": "not-an-email"}).status_code == 422
    assert client.get("/s/does-not-exist").status_code == 404


def test_honeypot_stores_nothing(client_and_conn):
    client, conn = client_and_conn
    slug = make_survey(client)
    r = client.post(f"/s/{slug}", data={"score": 10, "website": "http://spam"})
    assert r.status_code == 200
    assert conn.execute("SELECT COUNT(*) FROM responses").fetchone()[0] == 0


def test_follow_up_removes_from_queue(client_and_conn):
    client, conn = client_and_conn
    slug = make_survey(client)
    client.post(f"/s/{slug}", data={"score": 1, "comment": "rude agent", "email": "c@d.com", "contact_ok": "true"})
    rid = conn.execute("SELECT id FROM responses").fetchone()[0]
    client.post(f"/responses/{rid}/follow-up")
    assert "c@d.com" not in client.get(f"/dashboard/{slug}").text


def test_api_summary_excludes_contact_details(client_and_conn):
    client, _ = client_and_conn
    slug = make_survey(client)
    client.post(f"/s/{slug}", data={"score": 1, "email": "c@d.com", "contact_ok": "true"})
    body = client.get(f"/api/surveys/{slug}/summary").json()
    assert body["overall"]["nps"] == -100.0
    assert "c@d.com" not in str(body)


def test_admin_token_required_when_configured(monkeypatch):
    monkeypatch.setenv("PULSE_ADMIN_TOKEN", "s3cret")
    client = TestClient(create_app(db.connect(":memory:"), RuleTagger()))
    assert client.get("/").status_code == 401
    assert client.get("/", headers={"x-pulse-admin": "wrong"}).status_code == 401
    assert client.get("/", headers={"x-pulse-admin": "s3cret"}).status_code == 200
    # customer-facing survey pages stay public
    assert client.get("/healthz").status_code == 200


def test_comment_is_redacted_before_reaching_model():
    from app.themes import ClaudeTagger, redact

    assert redact("mail me at jo@ex.com or 555-201-8890, order 12345678") == \
        "mail me at [email] or [number], order [number]"
    # ClaudeTagger formats the prompt with redact(); verify via a fake client.
    tagger = ClaudeTagger.__new__(ClaudeTagger)
    sent = {}

    class FakeMessages:
        def create(self, **kw):
            sent["prompt"] = kw["messages"][0]["content"]

            class R:
                content = [type("B", (), {"text": '{"theme": "delivery", "sentiment": "negative"}'})()]
            return R()

    tagger.client = type("C", (), {"messages": FakeMessages()})()
    tagger.model = "test-model"
    tagger.fallback = RuleTagger()
    t = tagger.tag("call 555-201-8890, jo@ex.com, package lost", 1)
    assert t == Tag("delivery", "negative", "claude")
    assert "jo@ex.com" not in sent["prompt"] and "555-201-8890" not in sent["prompt"]


def test_concurrent_submissions_are_safe(tmp_path):
    """300 submissions from 16 threads, 50 distinct customers: every response
    stored and tagged, exactly one reward per customer. (This test exists
    because the first version shared a raw sqlite3 connection across threads
    and failed under exactly this load.)"""
    import concurrent.futures as cf

    conn = db.connect(str(tmp_path / "load.db"))
    client = TestClient(create_app(conn, RuleTagger()))
    slug = make_survey(client)

    def submit(i):
        return client.post(f"/s/{slug}", data={"score": i % 11, "comment": "late delivery",
                                                "email": f"u{i % 50}@x.com"}).status_code

    with cf.ThreadPoolExecutor(16) as pool:
        assert set(pool.map(submit, range(300))) == {200}
    assert conn.execute("SELECT COUNT(*) FROM responses").fetchone()[0] == 300
    assert conn.execute("SELECT COUNT(*) FROM rewards").fetchone()[0] == 50
    assert conn.execute("SELECT COUNT(*) FROM responses WHERE theme IS NULL").fetchone()[0] == 0
