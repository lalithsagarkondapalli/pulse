# Decisions

Short log of the product and technical calls, what I traded off, and when I'd revisit each one.

### 1. One question, not a survey builder
**Chose:** a single 0–10 question plus an optional comment.
**Why:** every extra question lowers completion, and the comment already carries the "why". A survey builder is a large product of its own.
**Revisit when:** brands ask for a follow-up question per score band (e.g. a different prompt for detractors).

### 2. Fixed theme taxonomy instead of open-ended clustering
**Chose:** eight themes the model must pick from.
**Why:** the dashboard's job is "what got worse this week". Free clustering produces different groupings each run, so trends can't be compared. A fixed list also makes the model's output checkable.
**Cost:** new problems land in `other`. **Mitigation:** watch the share of `other`; when it grows, read those comments and add a theme.

### 3. Rank themes by detractors explained, not by volume
**Chose:** sort by count of 0–6 scores per theme.
**Why:** the most-mentioned theme is often the one people love. Detractor share points at what's costing recommendations.

### 4. The LLM is never on the request path
**Chose:** tag in a background task, with a rule-based fallback.
**Why:** a customer submitting feedback should never wait on, or fail because of, a model call. Untagged responses still count toward NPS and can be re-tagged from the dashboard.

### 5. Consent-gated contact details
**Chose:** hash every email; keep the plain address only with explicit opt-in.
**Why:** the reward needs de-duplication, which a hash gives. Following up needs a real address, which needs consent. Two needs, two storage rules.
**Earlier version:** I first stored only the hash — and then realised the follow-up queue had no way to reach anyone. The consent checkbox came out of that.

### 6. Show uncertainty
**Chose:** a 95% CI on NPS, a warning under 30 responses, and faded bars for weeks under 20.
**Why:** a brand will act on the dashboard. A 1-response week reading +100 looks like a recovery and isn't.

### 7. Server-rendered pages, SQLite
**Chose:** FastAPI + Jinja + SQLite, no front-end framework.
**Why:** fastest path to a complete, testable product; nothing here needs client-side state.
**Revisit when:** the dashboard needs live filters, or there is more than one app process (move to Postgres).
