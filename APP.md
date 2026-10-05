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

## Local run

```bash
pip install -r requirements.txt
python run_web.py            # http://127.0.0.1:8080
python tools/smoke.py        # 91 checks, no Redis needed
```

## Notes

* Vercel has no Redis — `REDIS_URL` must point to a hosted instance (Upstash,
  Redis Cloud, Aiven). Upstash free tier: 256 MB and 500K commands/month,
  eviction disabled by default, and the database is archived after 30 days
  without use.
* The bot needs a permanent process (long polling) and cannot run on Vercel.