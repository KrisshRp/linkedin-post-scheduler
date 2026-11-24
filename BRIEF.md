# Linkedin-automation

**Problem (1 sentence):** Interview assignment (Liberating Solution, AI/LLM Engineer): build a small working app that schedules and publishes one LinkedIn post reliably (OAuth, secure tokens, background worker, retries, no duplicates).

**Primary user + the one outcome they need:** Reviewer/user logs in with LinkedIn, writes a post, picks a date/time, and sees it auto-published at that time with a visible status and log trail.

**Smallest end-to-end workflow:**
```
Login with LinkedIn (OAuth) -> create post (content + time) -> row in DB (scheduled)
-> worker claims it at due time (processing) -> POST to LinkedIn -> published | retry w/ backoff | failed
```

**Success criteria (observable):**
1. A post scheduled 1-2 minutes ahead appears on the real LinkedIn profile at that time, exactly once.
2. Forcing a temporary error (fault injection) shows automatic retries with growing delays, then success, with zero duplicate posts, visible in the post's event log.
3. Tokens are encrypted at rest (no plaintext token in DB or logs); a fresh clone runs from README + `.env.example` in under 10 minutes.

**Out of scope (binding for this milestone):** see `PARKING-LOT.md` (Stripe, Postgres, Docker/CI, multi-platform, media posts, refresh-token flow, AI-generated posts, queue brokers).

**Ship target date:** 2026-11-24 (today).

**Simplest architecture that supports the workflow:** one FastAPI process = REST API + static JS dashboard + in-process daemon worker thread; SQLite (WAL); httpx for LinkedIn. Details in `.context/`.
