# 03 - Roadmap (ship today, 2026-11-24)

Each milestone = one vertical slice with an exit check. Tags: MUST = needed for assignment. STRETCH = only if all MUST done and verified. Nothing else enters.

| # | Milestone | Time box | Deliverable | Exit check | Tag |
|---|---|---|---|---|---|
| 0 | Prereqs (user, in parallel with M1) | 20 min | LinkedIn developer app created, products added, redirect URI set, `.env` filled | client id/secret in `.env`; both products show "Added" | MUST |
| 1 | Skeleton | 30 min | repo layout, `config.py`, `db.py`+`schema.sql`, FastAPI app, `/healthz`, `.env.example`, `run.bat`, `.gitignore` | `run.bat` starts server; `/healthz` ok; DB file created | MUST |
| 2 | OAuth login + secure tokens | 60 min | `/auth/*`, `linkedin.py` (authorize, token, userinfo), `crypto.py`, session, `/api/me`, login screen | log in with real LinkedIn; DB has ciphertext only; `/api/me` returns name | MUST |
| 3 | Posts API + dashboard | 60 min | `routes_posts.py`, `static/*` form + list, 409 on duplicate | schedule post in UI; row `scheduled` in DB; list shows it | MUST |
| 4 | Worker + dry_run publisher | 60 min | `worker.py`, `publisher.py`, events, claim/reaper | dry_run post flips scheduled -> processing -> published at due time, events logged | MUST |
| 5 | Retry, backoff, errors, dup guards | 60 min | classifier, backoff, `FAULT_INJECT`, retry-now, cancel | `FAULT_INJECT=2`: 2 retries with growing delay then published, one row/one URN | MUST |
| 6 | Real LinkedIn publish | 30 min | live `create_post()` | a real post appears on profile exactly once | MUST |
| 7 | Tests | 45 min | pytest suite from spec section 13 | `pytest` green | MUST |
| 8 | Docs + demo | 45 min | `README.md`, `docs/architecture.md`, schema in README, limitations, demo script, rehearsal | fresh clone follows README to a running app | MUST |
| 9 | Stretch (pick in order, stop when time is out) | rest | (a) Dockerfile + compose, (b) GitHub Actions running pytest, (c) deploy to free host for live demo | each independently verifiable | STRETCH |

Total MUST ≈ 6.5 h. Buffer ≈ 1.5 h for LinkedIn app/review friction.

## Risk register

| Risk | Impact | Mitigation |
|---|---|---|
| LinkedIn app requires a Company Page to create | blocks M0 | create a throwaway page first (5 min) |
| "Share on LinkedIn" access delayed | blocks M6 | self-serve, normally instant; dry_run keeps M1-M5, M7 unblocked |
| API version header rejected (`Linkedin-Version`) | M6 fails 426/400 | version in `.env`; try latest active `YYYYMM`; fall back to `/v2/ugcPosts` if needed |
| Localhost redirect URI rejected | login fails | LinkedIn allows `http://localhost`; must match exactly; fallback to `127.0.0.1` registered variant |
| Duplicate risk on ambiguous timeout | reviewer questions | documented honestly, layers in spec section 8 |
| Time zone mistakes | post fires at wrong time | UTC storage, explicit local-time label in UI, test in IST |
| Live demo needs public URL | no live demo | stretch 9c; otherwise screen-recorded run + dry_run demo |

## Definition of done (from workspace `definition-of-done.md` spirit)

- All 3 success criteria in `BRIEF.md` verified and reported with what was NOT checked.
- `pytest` green; real LinkedIn post confirmed by eye.
- README lets a stranger run it. Schema + diagram included.
- No secrets in git (`git log -p` grep for client secret / token).
- `PARKING-LOT.md` updated with anything noticed but not built.

## Demo script (for the interview)

1. Open dashboard, "Sign in with LinkedIn".
2. Write post, schedule 2 minutes ahead.
3. Show DB row `scheduled`, token column is ciphertext.
4. Show worker log + UI flip to `processing` -> `published`, open the live LinkedIn post.
5. Restart with `FAULT_INJECT=2` + `PUBLISH_MODE=dry_run`: schedule a post, show retries with increasing delay and a single published row.
6. Try scheduling the same content/time twice -> 409.
