# Parking lot

idea | why deferred | evidence that would reopen it

- Stripe subscriptions / billing | not in assignment; JD-only | reviewer asks for it
- PostgreSQL + multi-tenant schema | SQLite enough for one-post demo; `user_id` already on every row so migration is mechanical | need >1 process or hosted deploy
- Docker + CI (GitHub Actions) | JD-only; nice-to-have | deploy to a live URL is required
- AI-drafted post text (Claude/OpenAI button, structured JSON output, usage/cost tracking) | JD-relevant but not an assignment requirement | assignment finished early, time left
- Redis/Celery/BullMQ | single-process thread worker proves the idea; DB row-claim already gives single-flight | need multiple workers
- LinkedIn refresh tokens | self-serve apps do not get refresh tokens; handled as "reconnect when expired" | partner-program access
- Image/video/article posts | text post is the assignment | requirement appears
- Settings page to enter client id/secret in UI | secrets live in `.env` (safer) | multi-tenant use
- Other social platforms | out of scope | n/a
