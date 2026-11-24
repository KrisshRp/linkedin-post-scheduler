# LinkedIn Post Scheduler

Small working app that schedules and publishes a LinkedIn post. Log in with LinkedIn (OAuth 2.0), write a post, pick a date/time; a background worker publishes it at that time with automatic retries, increasing delays, duplicate protection, logging and encrypted token storage.

**Stack:** Python 3.11+, FastAPI, SQLite (WAL), httpx, cryptography (Fernet), vanilla-JS dashboard. One process, no external services.

## Features vs. the assignment

| Requirement | Implementation |
|---|---|
| User enters OAuth connection, content, schedule | "Sign in with LinkedIn" button, form with content + date/time |
| Stored in a database | SQLite `posts` table ([schema.sql](schema.sql)) |
| Background worker publishes at scheduled time | daemon thread polling the DB (`app/worker.py`) |
| LinkedIn OAuth | authorization-code flow, `state` CSRF check (`app/routes_auth.py`, `app/linkedin.py`) |
| Secure token storage | Fernet-encrypted at rest; key only in `.env` |
| Retry + increasing delays on temporary errors | 30 s, ~2.5 min, ~12.5 min, ~1 h (cap 2 h), +jitter, honours `Retry-After`, max 5 attempts |
| Error handling | errors classified: temporary (retry) / permanent (fail) / auth (fail, reconnect) |
| Duplicate-post protection | atomic claim, unique key, URN saved with `published` (details below) |
| Logging | console + `logs/app.log` (secrets redacted) + per-post event log in the UI |
| Statuses | `scheduled`, `processing`, `published`, `failed` |

## Setup

Needs Python 3.11+.

```powershell
git clone <this repo>; cd Linkedin-automation
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
copy .env.example .env
```

Generate the two secrets and paste them into `.env`:

```powershell
.venv\Scripts\python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"   # TOKEN_ENCRYPTION_KEY
.venv\Scripts\python -c "import secrets; print(secrets.token_urlsafe(48))"                                 # SESSION_SECRET
```

### LinkedIn developer app (needed for live posting)

1. https://www.linkedin.com/developers/apps -> **Create app**. Associate a LinkedIn Company Page (a throwaway page works).
2. **Products** tab: add **Sign In with LinkedIn using OpenID Connect** and **Share on LinkedIn** (self-serve).
3. **Auth** tab: copy **Client ID** and **Client Secret** into `.env` (`LINKEDIN_CLIENT_ID`, `LINKEDIN_CLIENT_SECRET`).
4. **Auth** tab -> Authorized redirect URLs: add `http://localhost:8000/auth/callback` (must equal `LINKEDIN_REDIRECT_URI`).
5. Only the app's developers/test users can sign in while the app is in development; use your own account.

Every variable is documented in [.env.example](.env.example).

### Run

```powershell
.venv\Scripts\python -m uvicorn app.main:app --port 8000
```
or double-click `run.bat`. Open http://localhost:8000.

### Tests

```powershell
.venv\Scripts\python -m pytest -q
```

## Demo script

**Live:** sign in with LinkedIn -> write a post -> schedule 2 minutes ahead -> watch `scheduled -> processing -> published` -> click "View on LinkedIn".

**Retry demo without a real outage (no LinkedIn account needed):** in `.env` set `PUBLISH_MODE=dry_run`, `FAULT_INJECT=2`, `RETRY_BASE_SECONDS=3`, restart, click **Dev login**, schedule a post 5 s ahead, open its **Log**: attempt 1 fails (simulated 503), retry in ~4 s, attempt 2 fails, retry in ~15 s, attempt 3 publishes. One row, one URN. Scheduling the same content at the same time again returns 409.

`dry_run` never contacts LinkedIn, and the dev login exists only in this mode.

## How duplicate posts are prevented

1. **DB unique key** `(user_id, content_hash, scheduled_at)`: double-submit cannot create two rows (HTTP 409).
2. **Atomic claim:** the worker flips `scheduled -> processing` with one `UPDATE ... WHERE status='scheduled' ... RETURNING`; only one caller can win a row (tested with 8 racing threads).
3. **URN saved with `published`** in the same commit; the claim query excludes rows that have a URN, and the crash-recovery reaper never touches them.
4. **Unknown outcomes are handled conservatively:** unexpected internal errors and "201 without a post id" are NOT retried.

## Known limitations (honest notes)

- **Ambiguous timeouts:** LinkedIn offers no idempotency key. If LinkedIn creates the post but the response is lost (read timeout), a retry could create a duplicate. Verifying would need the restricted `r_member_social` scope. Connection errors that happen before the request is sent are safe to retry.
- **No refresh tokens:** self-serve LinkedIn apps get ~60-day access tokens and no refresh token. On expiry the post fails with "Log in again to reconnect".
- **Single process:** the worker runs inside the web process (SQLite). Running several app processes is safe for claiming, but is not the target setup.
- `LINKEDIN_API_VERSION` (header `Linkedin-Version`) must be a currently supported `YYYYMM`; change it in `.env` if LinkedIn reports it inactive.
- Text posts only (no images/links preview/articles).

## Layout

```
app/        main, config, db, crypto, logs, linkedin (OAuth + API), publisher, worker, routes_auth, routes_posts
static/     index.html, app.js, app.css
tests/      pytest suite (offline)
docs/       architecture.md (diagrams)
schema.sql  database schema
.context/   planning documents (plan, spec, roadmap, dependencies)
```

Architecture diagram and state machine: [docs/architecture.md](docs/architecture.md).
