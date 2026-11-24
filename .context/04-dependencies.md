# 04 - Dependencies, Setup and `.env.example` spec

## Python packages (`requirements.txt`, pin exact versions at build time)

| Package | Why (gate test: current need) |
|---|---|
| fastapi | REST API + static serving |
| uvicorn[standard] | ASGI server |
| httpx | LinkedIn HTTP calls, explicit timeouts, easy to mock |
| cryptography | Fernet encryption for tokens |
| itsdangerous | required by Starlette `SessionMiddleware` (signed cookie) |
| python-dotenv | load `.env` |
| pytest (dev) | tests |

Not used (decided): SQLAlchemy/Alembic (raw sqlite3 + schema.sql enough), Celery/Redis/APScheduler (thread worker enough), Jinja (static JS UI), authlib (OAuth flow is 3 HTTP calls).

Runtime: Python 3.11+, SQLite (bundled), no other services.

## External dependency: LinkedIn Developer App (setup, step by step)

1. Sign in at https://www.linkedin.com/developers/apps -> **Create app**.
2. Fill app name, upload a logo, and associate a **LinkedIn Company Page** (required; create a throwaway page if you have none). Accept terms, create.
3. **Products** tab -> request both (self-serve, usually instant):
   - **Sign In with LinkedIn using OpenID Connect** (gives `openid profile email`)
   - **Share on LinkedIn** (gives `w_member_social`, needed to post)
4. **Auth** tab:
   - Copy **Client ID** and **Primary Client Secret** into `.env`.
   - Under **OAuth 2.0 settings -> Authorized redirect URLs**, add exactly `http://localhost:8000/auth/callback` (must match `LINKEDIN_REDIRECT_URI` character for character; later add your public URL if you deploy).
5. Verify the **OAuth 2.0 scopes** list on the Auth tab shows `openid`, `profile`, `email`, `w_member_social`.
6. Access tokens last ~60 days. Self-serve apps get no refresh token, so when a token expires the user just logs in again.
7. Only the app's own developers/test members can authorize while the app is in development; the reviewer's account must be added under **Settings -> Test users** if they need to log in themselves (or demo with your own account).

## `.env.example` (final content to be written during M1)

```dotenv
# ===== App =====
APP_BASE_URL=http://localhost:8000
HOST=127.0.0.1
PORT=8000
LOG_LEVEL=INFO
DB_PATH=data/app.db

# ===== LinkedIn app (see .context/04-dependencies.md "LinkedIn Developer App") =====
# Where: linkedin.com/developers/apps -> your app -> Auth tab
LINKEDIN_CLIENT_ID=
LINKEDIN_CLIENT_SECRET=
# Must be registered in the app's "Authorized redirect URLs", exact match
LINKEDIN_REDIRECT_URI=http://localhost:8000/auth/callback
LINKEDIN_SCOPES=openid profile email w_member_social
# Versioned posts API header, format YYYYMM. Use a currently supported version; change if LinkedIn replies "version not active"
LINKEDIN_API_VERSION=202511

# ===== Security (generate once, never commit the real values) =====
# python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
TOKEN_ENCRYPTION_KEY=
# python -c "import secrets; print(secrets.token_urlsafe(48))"
SESSION_SECRET=
# true when served over https
COOKIE_SECURE=false

# ===== Worker / retry =====
WORKER_POLL_SECONDS=5
MAX_ATTEMPTS=5
RETRY_BASE_SECONDS=30
RETRY_MAX_SECONDS=7200

# ===== Publishing mode =====
# live    = really post to LinkedIn
# dry_run = fake publisher, nothing leaves your machine (demo + tests)
PUBLISH_MODE=live
# dry_run only: fail the first N attempts of each post with a fake 503 to demo retries
FAULT_INJECT=0
```

Process notes shown in the file as comments: how to obtain each value, which are secrets, which are safe defaults. Missing required value (`LINKEDIN_CLIENT_ID`, `LINKEDIN_CLIENT_SECRET`, `TOKEN_ENCRYPTION_KEY`, `SESSION_SECRET` when `PUBLISH_MODE=live`) = app refuses to start with a message naming the variable.

## Dependency order between milestones

```
M0 LinkedIn app  ─┐
M1 skeleton ──────┼─> M2 OAuth login ─> M3 posts API+UI ─> M4 worker(dry_run) ─> M5 retry/dup ─> M6 live publish ─> M7 tests ─> M8 docs
                  └─(M1 only needs .env keys that exist; M4/M5/M7 work with no LinkedIn access via dry_run)
```

- M2 and M6 are the only steps needing real LinkedIn access (M0).
- M4, M5, M7 can proceed fully offline.

## Open questions for approval

1. **SQLite vs PostgreSQL:** plan uses SQLite (matches your other projects, zero setup). JD asks Postgres; schema is portable. Switch to Postgres only if you want it for the interview story (+45 min, needs Docker/local PG).
2. **LinkedIn app:** do you already have a developer app + Company Page, or do I include creating them in your prerequisite checklist (steps above)?
3. **Live demo:** need a public URL (stretch 9c), or is a screen recording + local run enough?
4. **Stretch picks:** Dockerfile + GitHub Actions worth including for JD fit, or skip entirely?
