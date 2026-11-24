# 01 - Implementation Plan

## Goal
Schedule and publish ONE LinkedIn post, reliably. Map every assignment bullet to a concrete piece.

## Assignment -> implementation map

| Assignment requirement | Where it is built |
|---|---|
| User enters OAuth credentials/connection | "Login with LinkedIn" button -> OAuth 2.0 code flow. App client id/secret live server-side in `.env` (never in the browser). The "connection" = stored encrypted token. |
| User enters post content + schedule | Dashboard form (`POST /api/posts`), datetime-local picker, converted to UTC |
| Store scheduled post in DB | SQLite `posts` table (schema in 02-technical-spec.md) |
| Background worker publishes at time | Daemon thread in the FastAPI process, polls DB every 5 s |
| LinkedIn OAuth | `app/linkedin.py` + `app/routes_auth.py` (authorize, callback, userinfo) |
| Secure token storage | Fernet (AES-128-CBC + HMAC) encryption of access token, key from `.env` |
| Scheduled job | Worker `due posts` query: `status='scheduled' AND next_attempt_at <= now` |
| Retry mechanism | Per-post `attempts`, `max_attempts=5`, `next_attempt_at` |
| Increasing delays | Exponential backoff 30s, 2m, 10m, 30m, 2h (+ jitter), honors `Retry-After` |
| Error handling | Error classifier: temporary (retry) vs permanent (fail fast) vs auth (fail + prompt reconnect) |
| Duplicate-post protection | Atomic claim (`UPDATE ... WHERE status='scheduled'` rowcount==1), UNIQUE `(user_id, content_hash, scheduled_at)`, store `linkedin_post_urn` the instant it is returned, never re-post a row that has a URN |
| Logging | Python `logging` (console + rotating `logs/app.log`) + `post_events` table shown in the UI |
| Status values | `scheduled`, `processing`, `published`, `failed` (CHECK constraint) |
| Temporary error -> auto retry, backoff, no duplicates | Covered above; proven with fault-injection demo (`FAULT_INJECT`) |
| Git repo, README, setup, schema, diagram, live demo | `README.md`, `schema.sql`, `docs/architecture.md` (Mermaid), demo script in README |

## Key design decisions (frozen once approved)

1. **Stack:** Python 3.11+, FastAPI, httpx, stdlib `sqlite3` (WAL), `cryptography`. No ORM, no broker, no scheduler lib.
2. **Background worker:** same pattern already used in `Job-automation` (`core/fetch.py`, `core/gen.py`): `threading.Thread(daemon=True)` loop with a `stop` Event, SQLite WAL so the UI never blocks the worker, errors caught so the worker never dies. Started in FastAPI `lifespan`.
3. **Auth = LinkedIn login.** "Sign In with LinkedIn using OpenID Connect" gives identity (`sub`, name, email, picture); "Share on LinkedIn" gives `w_member_social` to post. One OAuth round trip covers dashboard login AND posting permission. Session = signed cookie (Starlette `SessionMiddleware`).
4. **Frontend:** static HTML + vanilla JS served by FastAPI (`/static`), fetch -> REST. Same shape as `Job-automation/ui/static`. No build step.
5. **Posting API:** `POST https://api.linkedin.com/rest/posts` (versioned API), headers `Linkedin-Version: <YYYYMM from .env>`, `X-Restli-Protocol-Version: 2.0.0`. Author `urn:li:person:{sub}`. Success = HTTP 201 + `x-restli-id` header (post URN).
6. **Duplicate safety honesty:** LinkedIn gives no idempotency key. We prevent duplicates from OUR side (atomic claim, URN stored on success, only retry errors where we know the post was NOT created or is very likely not created). Residual risk (timeout after LinkedIn actually created the post) is documented in README, not hidden.
7. **Dev/demo mode:** `PUBLISH_MODE=dry_run` uses a fake publisher (no real LinkedIn call) and `FAULT_INJECT=2` fails the first N attempts with a 503. Lets the reviewer see retries without waiting for a real outage, and lets tests run offline.
8. **Time:** all timestamps stored UTC ISO-8601; browser sends UTC; UI renders in local time.
9. **Light multi-user:** every row carries `user_id` (JD mentions multi-tenant). No extra machinery.

## Folder structure

```
Linkedin-automation/
├── BRIEF.md                  one-page brief (sandbox rule)
├── PARKING-LOT.md            deferred ideas
├── README.md                 setup, run, demo script, limitations
├── .env.example              every variable + how to get it (see 04-dependencies.md)
├── .gitignore                .env, .venv, data/, logs/
├── requirements.txt
├── run.bat                   venv + install + uvicorn (matches Job-automation habit)
├── schema.sql                DB schema (also executed at startup)
├── .context/                 planning docs (this folder)
├── app/
│   ├── __init__.py
│   ├── main.py               FastAPI app, lifespan (init DB, start worker), static mount, routers
│   ├── config.py             load .env -> typed settings, fail fast on missing keys
│   ├── db.py                 connect(), init_schema(), small helpers (WAL, row factory)
│   ├── crypto.py             Fernet encrypt/decrypt token helpers
│   ├── linkedin.py           authorize URL, code->token, userinfo, create_post(), error classification
│   ├── publisher.py          real vs dry_run publisher + fault injection (one interface: publish(token, urn, text) -> urn)
│   ├── worker.py             claim_due(), process(), backoff(), reaper for stuck `processing`
│   ├── routes_auth.py        /auth/login, /auth/callback, /auth/logout, /api/me
│   └── routes_posts.py       /api/posts CRUD-lite, /api/posts/{id}/events
├── static/
│   ├── index.html            login screen + dashboard shell
│   ├── app.js                render, fetch, poll status every 5 s
│   └── app.css
├── docs/
│   └── architecture.md      Mermaid diagram + state machine
├── tests/
│   ├── test_backoff.py       delay schedule, Retry-After, jitter bounds
│   ├── test_claim.py         two workers, one row -> exactly one publishes
│   ├── test_worker_flow.py   fake publisher: success, temp error then success, permanent fail, 401
│   └── test_crypto.py        round trip, no plaintext in DB
├── data/                     app.db (git-ignored)
└── logs/                     app.log (git-ignored)
```

Why `publisher.py` is a separate file (gate test): current need = demo retries without real outage + offline tests; simpler option = if/else in worker but it would tangle fault injection into prod path; cost = ~40 lines. Accepted. Nothing else gets its own layer.

## Build order (vertical slices)

1. Skeleton + config + DB + health route.
2. LinkedIn login (OAuth) + session + token encryption + `/api/me`.
3. Create/list posts API + dashboard form + list view.
4. Worker + publisher (dry_run first) + status transitions + events.
5. Retry/backoff + error classification + duplicate guards + fault injection.
6. Real LinkedIn publish, tested live once.
7. Tests, README, architecture diagram, demo rehearsal.

See 03-roadmap.md for time boxes and exit checks.
