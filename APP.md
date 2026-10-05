# Gramly — website / Mini App

This bundle is the **website only**: the Flask app, the Mini App UI and the
shared core (Redis storage, pact state machine, reputation, discovery). The
Telegram bot code is not here — it runs as a separate process against the same
Redis.

## Files

| Path | What it is |
|---|---|
| `api/index.py` | Vercel entrypoint, exposes the Flask instance as `app` |
| `gramly/webapp/` | Flask app: routes, `initData` auth, templates, CSS, JS |
| `gramly/core/` | Redis layer, pacts, rating, users, guarantors |
| `requirements.txt` | pip / Vercel dependencies (flask, redis) |
| `vercel.json` | function settings |
| `.python-version` | CPython 3.12 |
| `run_web.py` | local server (`python run_web.py`) |
| `tools/check_redis.py` | checks that Redis is reachable and writable |
| `tools/smoke.py` | end-to-end test on an in-memory Redis |

## 1. Redis (Upstash)

In the Upstash console open your database and copy **Endpoint** (host:port),
**Username** (`default`) and **Password**. Then build the URL:

```
rediss://default:<password>@<host>:6379
```

`rediss://` = with TLS, `redis://` = without. Check it before deploying:

```bash
python tools/check_redis.py     # reads REDIS_URL from .env, never prints the password
```

### If you used the Vercel Upstash integration

Vercel's marketplace integration prefixes every variable with the resource
name, so a resource called `REDIS_URL` produces `REDIS_URL_REDIS_URL` instead
of `REDIS_URL`. The app already understands that: any variable ending in
`_REDIS_URL` is picked up automatically. To keep it tidy, either rename the
integration resource (for example to `UPSTASH`, giving `UPSTASH_REDIS_URL`) or
add your own plain `REDIS_URL` variable — an explicitly set `REDIS_URL` always
wins.

Ignore `KV_URL` / `KV_REST_API_URL` / `*_TOKEN` — those are the REST API
credentials, `redis-py` needs the TCP endpoint.

## 2. Environment variables

Set these in Vercel under **Project → Settings → Environment Variables**
(Production + Preview):

```
GRAMLY_BOT_TOKEN=123456:AA...        # from @BotFather
GRAMLY_BOT_USERNAME=gramly_bot
GRAMLY_PUBLIC_URL=https://your-app.vercel.app
REDIS_URL=rediss://default:password@host:6379
GRAMLY_SECRET_KEY=<random string>
GRAMLY_ADMIN_IDS=11111111            # your Telegram id
```

Optional: `GRAMLY_PREFIX` (key prefix, default `g`), `GRAMLY_WEB_SESSION_TTL`
(default 86400), `GRAMLY_HOST`, `GRAMLY_PORT`, `GRAMLY_DEBUG`.

## 3. Deploy

```bash
npm i -g vercel
vercel link
vercel deploy --prod
```

Or import the repository in the dashboard — Vercel detects Flask from
`requirements.txt` and loads `api/index.py`.

After deploying, open `https://<your-domain>/healthz`: `{"ok": true, ...}`
means the database connection works, `{"ok": false}` means `REDIS_URL` is
wrong or unreachable.

## 4. Tell Telegram about the domain

* `@BotFather` → `/setmenubutton` → your Vercel domain
* `@BotFather` → Mini App → Web App URL: `https://your-app.vercel.app`

## Security

* `initData` is verified with HMAC-SHA256 keyed by `WebAppData`, compared in
  constant time; `auth_date` is required and checked for staleness.
* Sessions are opaque Redis ids; the cookie is `HttpOnly`, `SameSite=Lax` and
  `Secure` over HTTPS (ProxyFix is enabled for the Vercel proxy).
* Every mutating route requires the per-session CSRF token (`X-CSRF-Token`
  header or `_csrf` field); `/auth` needs a double-submit token from `/gate`
  and follows only relative `next` paths.
* If Redis is unreachable the site answers `503` with a readable page instead
  of a stack trace.

## Local run

```bash
pip install -r requirements.txt
python run_web.py            # http://127.0.0.1:8080
python tools/smoke.py        # end-to-end test, no Redis needed
```

`tools/smoke.py` compiles every template and renders every page for a signed-in
user, so a broken template fails the test instead of a live request.

## Notes

* Vercel has no Redis — `REDIS_URL` must point to a hosted instance (Upstash,
  Redis Cloud, Aiven). Upstash free tier: 256 MB and 500K commands/month,
  eviction disabled by default, and the database is archived after 30 days
  without use.
* The bot needs a permanent process (long polling) and cannot run on Vercel.