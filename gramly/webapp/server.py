"""Flask application: the Gramly website and Mini App.

Read paths are public (the pact board, profiles, the rating ledger), write
paths need a signed Telegram Mini App session. Sessions live in Redis, so the
site works behind several workers without sticky sessions.
"""

from __future__ import annotations

import hmac
import secrets
from urllib.parse import urlencode

import redis
from flask import (
    Flask, abort, g, jsonify, make_response, redirect, render_template, request,
)
from werkzeug.middleware.proxy_fix import ProxyFix

from ..core import discovery, format as fmt, guarantors, pacts, rating, terms, users
from ..core.config import config
from ..core.store import now, store
from . import auth

COOKIE = "gramly_sid"
GATE = "gramly_gate"

NAV = [
    {"key": "home", "label": "Главная", "href": "/"},
    {"key": "board", "label": "Пакты", "href": "/pacts"},
    {"key": "rating", "label": "Порядочность", "href": "/rating"},
    {"key": "guarantors", "label": "Гаранты", "href": "/guarantors"},
]

AVATAR_COLORS = [
    "#e17076", "#7bc862", "#e5ca77", "#65aadd", "#a695e7",
    "#ee7aae", "#6ec9cb", "#faa774", "#84b1eb", "#d09aeb",
]


# --------------------------------------------------------------------- helpers

def avatar_color(rec: dict) -> str:
    if rec.get("avatar_color"):
        return rec["avatar_color"]
    seed = int(rec.get("id") or rec.get("uid") or 0)
    return AVATAR_COLORS[seed % len(AVATAR_COLORS)] if seed else "#8d8d8d"


def initials(rec: dict) -> str:
    rec = rec or {}
    if rec.get("first_name") or rec.get("last_name"):
        return fmt.initials(rec.get("first_name"), rec.get("last_name"))
    name = (rec.get("name") or rec.get("title") or "").strip()
    if name:
        parts = [p for p in name.replace("  ", " ").split(" ") if p]
        if len(parts) >= 2:
            return (parts[0][:1] + parts[1][:1]).upper()
        return name[:2].upper()
    return "?"


def status_label(status: str) -> str:
    return terms.STATUSES.get(status, status or "")


def kind_spec(kind: str) -> dict:
    return terms.PACT_KINDS.get(kind, terms.PACT_KINDS[terms.DEFAULT_KIND])


def ts_short(ts) -> str:
    return fmt.fmt_relative(int(ts or 0))


def ts_full(ts) -> str:
    return fmt.fmt_datetime(int(ts or 0))


def ts_date(ts) -> str:
    return fmt.fmt_date(int(ts or 0))


def view_pact(pact: dict) -> dict:
    """Shape a pact for the templates."""
    return {
        "id": pact.get("id"),
        "num": pact.get("num"),
        "title": pact.get("title", ""),
        "emoji": kind_spec(pact.get("kind"))["emoji"],
        "kind": pact.get("kind"),
        "kind_label": kind_spec(pact.get("kind"))["label"],
        "status": pact.get("status", "draft"),
        "status_label": status_label(pact.get("status", "")),
        "a": pact.get("side_a_name", ""),
        "b": pact.get("side_b_name", "") or "контрагент",
        "side_a": int(pact.get("side_a") or 0),
        "side_b": int(pact.get("side_b") or 0),
        "created_at": pact.get("created_at", 0),
        "created_short": ts_short(pact.get("created_at")),
        "signed_at": pact.get("signed_at", 0),
        "term_start": pact.get("term_start", 0),
        "term_end": pact.get("term_end", 0),
        "hash": pact.get("hash", ""),
        "price_stars": int(pact.get("price_stars") or 0),
    }


def view_side(meta: dict) -> dict:
    meta = meta or {}
    uid = int(meta.get("uid") or 0)
    return {
        "uid": uid,
        "name": meta.get("name") or ("контрагент" if not uid else str(uid)),
        "username": meta.get("username", ""),
        "conditions": meta.get("conditions") or [],
        "channels": meta.get("channels") or [],
        "guarantor": int(meta.get("guarantor") or 0),
        "offers_stars": bool(meta.get("offers_stars")),
    }


def view_profile(rec: dict) -> dict:
    uid = int(rec.get("id") or 0)
    st = store()
    snap = rating.snapshot(st, uid)
    tier = int(rec.get("guarantor_tier") or 0)
    return {
        "id": uid,
        "uid": uid,
        "first_name": rec.get("first_name", ""),
        "last_name": rec.get("last_name", ""),
        "name": users.display(rec),
        "username": rec.get("username", ""),
        "bio": rec.get("bio", ""),
        "premium": bool(rec.get("premium")),
        "score": snap["score"],
        "tier": snap["tier"],
        "pact_count": len(users.pact_ids(st, uid, 500)),
        "opened": int(rec.get("pacts_opened") or 0),
        "signed": int(rec.get("pacts_signed") or 0),
        "closed": int(rec.get("pacts_closed") or 0),
        "broken": int(rec.get("pacts_broken") or 0),
        "reports_against": int(rec.get("reports_against") or 0),
        "reports_upheld": int(rec.get("reports_upheld") or 0),
        "guarantor_tier": tier,
        "guarantor_name": guarantors.TIER_NAMES.get(tier, ""),
        "stars": int(rec.get("star_balance") or 0),
        "last_seen": rec.get("last_seen", 0),
    }


def ledger_rows(limit: int = 20) -> list:
    out = []
    for e in rating.global_events(store(), limit):
        uid = int(e.get("uid") or 0)
        rec = users.get(store(), uid) if uid else {}
        out.append({
            "uid": uid,
            "name": users.display(rec) if rec else f"id {uid}",
            "username": rec.get("username", ""),
            "delta": float(e.get("delta") or 0),
            "note": e.get("note") or e.get("kind", ""),
            "ts": e.get("ts", 0),
            "ts_short": ts_short(e.get("ts")),
            "hash": (e.get("hash") or "")[:16],
        })
    return out


def _payload() -> dict:
    if request.is_json:
        return request.get_json(silent=True) or {}
    return {k: v for k, v in request.form.items()}


def _back(url: str, ok: str = "", err: str = "") -> str:
    params = {}
    if ok:
        params["ok"] = ok
    if err:
        params["err"] = err
    return url + ("?" + urlencode(params) if params else "")


def _lines(raw: str) -> list:
    return [" ".join(x.split()) for x in (raw or "").splitlines() if x.strip()]


def _safe_next(target: str) -> str:
    """Only same-site relative paths survive, so /auth cannot be used to bounce
    a freshly authenticated user to an attacker's page."""
    target = (target or "").strip()
    if not target.startswith("/") or target.startswith("//"):
        return "/"
    if "\\" in target or "\n" in target or "\r" in target:
        return "/"
    return target


# ------------------------------------------------------------------- app setup

def create_app() -> Flask:
    app = Flask(__name__)
    app.config["SECRET_KEY"] = config.secret_key
    app.config["JSON_AS_ASCII"] = False
    app.jinja_env.globals.update(
        terms=terms,
        avatar_color=avatar_color,
        initials=initials,
        status_label=status_label,
        kind_spec=kind_spec,
        ts_short=ts_short,
        ts_full=ts_full,
        ts_date=ts_date,
        fmt=fmt,
    )
    app.jinja_env.filters["pct"] = lambda v: max(0, min(100, int(round(float(v or 0)))))
    app.jinja_env.filters["num"] = lambda v: f"{float(v or 0):.1f}"

    s = store()
    # Behind Vercel/nginx the client talks TLS to the proxy, not to Flask:
    # without this, request.is_secure is False and session cookies lose Secure.
    app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1, x_prefix=1)

    @app.before_request
    def _load_user():
        g.user = None
        g.uid = 0
        g.me = None
        g.csrf = ""
        sid = request.cookies.get(COOKIE) or ""
        data = s.get_json(s.k("web", "sess", sid), {}) if sid else {}
        if data.get("uid"):
            g.uid = int(data["uid"])
            rec = users.get(s, g.uid)
            users.sync_username_index(s, rec)
            g.user = rec
            g.me = view_profile(rec)
            token = data.get("csrf") or secrets.token_urlsafe(24)
            g.csrf = token
            s.set_json(s.k("web", "sess", sid), {"uid": g.uid, "csrf": token},
                       ttl=config.web_session_ttl)

    @app.before_request
    def _check_csrf():
        if request.method not in ("POST", "PUT", "PATCH", "DELETE"):
            return None
        if request.endpoint == "do_auth":
            return None
        if not g.uid:
            return None
        sent = request.headers.get("X-CSRF-Token", "")
        if not sent:
            if request.is_json:
                sent = (request.get_json(silent=True) or {}).get("_csrf", "")
            else:
                sent = request.form.get("_csrf", "")
        if not sent or sent != g.csrf:
            abort(400, description="CSRF token missing or invalid")
        return None

    @app.context_processor
    def _ctx():
        return {"nav": NAV, "active": request.endpoint or "", "terms": terms,
                "me": g.get("me"), "uid": g.get("uid", 0), "csrf": g.get("csrf", ""),
                "ok_msg": request.args.get("ok", ""), "err_msg": request.args.get("err", "")}

    def login_redirect(next_url: str = ""):
        if not g.uid:
            return redirect(_back("/gate", err="Войдите через Telegram, чтобы действовать"))
        return None

    def require_login():
        guard = login_redirect()
        return guard

    # ---------------------------------------------------------------- pages

    @app.get("/")
    def index():
        st = discovery.counters(s)
        me = g.me
        my_list = []
        if me:
            my_list = [view_pact(p) for p in pacts.list_for(s, g.uid, 12)]
        chain_ok, chain_note = rating.verify_chain(s, 200)
        return render_template(
            "index.html",
            brand=terms.APP_NAME,
            active="home",
            me=me,
            stats=st,
            my_list=my_list,
            ledger=ledger_rows(14),
            chain_note=("🔐 " + chain_note) if chain_ok else ("⚠️ " + chain_note),
            people=[dict(r, id=r["uid"]) for r in users.rank(s, 8)],
            guarantors=[dict(r, id=r["uid"], name=r["name"])
                        for r in guarantors.leaderboard(s, 6)],
        )

    @app.get("/gate")
    def gate():
        if g.uid:
            return redirect("/")
        token = request.cookies.get(GATE) or ""
        if len(token) < 16:
            token = secrets.token_urlsafe(24)
        resp = make_response(render_template("gate.html", brand="Вход", active="",
                                             bot_username=config.bot_username,
                                             gate_token=token))
        resp.set_cookie(GATE, token, max_age=config.web_session_ttl, httponly=True,
                        samesite="Lax", secure=request.is_secure)
        return resp

    @app.post("/auth")
    def do_auth():
        data = _payload()
        raw = data.get("initData") or request.args.get("initData", "")
        cookie_token = request.cookies.get(GATE) or ""
        sent_token = data.get("_gate") or request.args.get("_gate", "")
        if not raw or not hmac.compare_digest(str(sent_token), str(cookie_token)) \
                or len(cookie_token) < 16:
            return redirect(_back("/gate", err="Сессия входа устарела, попробуйте ещё раз"))
        if raw:
            ok, payload, why = auth.login(raw)
            if not ok:
                return redirect(_back("/gate", err=why))
            user = payload["user"]
            rec = users.touch(s, user)
            users.sync_username_index(s, rec)
            uid = int(user["id"])
        elif config.debug and data.get("uid"):
            uid = int(data["uid"])
            users.touch(s, {"id": uid, "first_name": data.get("first_name", "dev"),
                            "username": data.get("username", "")})
        else:
            return redirect(_back("/gate", err="Нет данных Telegram"))

        sid = secrets.token_urlsafe(24)
        token = secrets.token_urlsafe(24)
        s.set_json(s.k("web", "sess", sid), {"uid": uid, "csrf": token},
                   ttl=config.web_session_ttl)
        resp = make_response(redirect(_safe_next(data.get("next"))))
        resp.set_cookie(COOKIE, sid, max_age=config.web_session_ttl, httponly=True,
                        samesite="Lax", secure=request.is_secure)
        return resp

    @app.get("/logout")
    def logout():
        sid = request.cookies.get(COOKIE)
        if sid:
            s.delete(s.k("web", "sess", sid))
        resp = make_response(redirect("/"))
        resp.delete_cookie(COOKIE)
        return resp

    @app.get("/pacts")
    def board():
        kind = request.args.get("kind", "")
        status = request.args.get("status", "")
        q = request.args.get("q", "")
        rows = []
        for pid in s.lrange(s.k("pact", "all"), -400, -1):
            pub = pacts.public(s, pid)
            if not pub:
                continue
            if kind and pub.get("kind") != kind:
                continue
            if status and pub.get("status") != status:
                continue
            if q:
                hay = f"{pub.get('title', '')} {pub.get('a', '')} {pub.get('b', '')}".lower()
                if q.lower() not in hay:
                    continue
            rows.append({
                "id": pid, "num": pub.get("num"), "title": pub.get("title", ""),
                "emoji": kind_spec(pub.get("kind"))["emoji"],
                "kind_label": kind_spec(pub.get("kind"))["label"],
                "status": pub.get("status", "draft"),
                "status_label": status_label(pub.get("status", "")),
                "a": pub.get("a", ""), "b": pub.get("b", ""),
                "created_short": ts_short(pub.get("created_at")),
                "hash": pub.get("hash", ""),
            })
        rows.sort(key=lambda r: r.get("num") or 0, reverse=True)
        return render_template("board.html", brand="Пакты", active="board",
                               rows=rows[:60], kind=kind, status=status, q=q,
                               kinds=terms.PACT_KINDS, order=terms.KIND_ORDER,
                               statuses=terms.STATUSES, total=len(rows))

    @app.get("/rating")
    def rating_page():
        chain_ok, chain_note = rating.verify_chain(s, 300)
        return render_template(
            "rating.html", brand="Порядочность", active="rating",
            rows=ledger_rows(60),
            top=[dict(r, id=r["uid"]) for r in users.rank(s, 20)],
            chain_ok=chain_ok, chain_note=chain_note,
            st=discovery.counters(s),
        )

    @app.get("/guarantors")
    def guarantors_page():
        return render_template(
            "guarantors.html", brand="Гаранты", active="guarantors",
            rows=[dict(r, id=r["uid"], name=r["name"]) for r in guarantors.leaderboard(s, 40)],
            tiers=[{"t": t, "name": n, "perk": guarantors.TIER_PERKS.get(t, ""),
                    "mult": guarantors.TIER_MULTIPLIER.get(t, 1.0)}
                   for t, n in guarantors.TIER_NAMES.items()],
            mine=guarantors.tier_info(s, g.uid) if g.uid else None,
        )

    @app.get("/p/<pid>")
    @app.get("/pact/<pid>")
    def pact_page(pid):
        pact = pacts.get(s, pid)
        if not pact:
            abort(404)
        sides = [view_side(pacts.side_meta(pact, int(pact.get("side_a") or 0))),
                 view_side(pacts.side_meta(pact, int(pact.get("side_b") or 0)))]
        integrity_ok, integrity_note = pacts.verify(s, pid)
        return render_template(
            "pact.html",
            brand=f"Пакт №{pact.get('num')}",
            p=view_pact(pact),
            sides=sides,
            signatures=pacts.signatures(pact),
            timeline=[dict(e, when=ts_full(e.get("t"))) for e in pacts.timeline(s, pid, 40)],
            health=pacts.health(s, pid),
            integrity_ok=integrity_ok, integrity_note=integrity_note,
            guarantor=users.display(users.get(s, int(pact.get("guarantor") or 0)))
            if pact.get("guarantor") else "",
            quote=guarantors.quote(s, int(pact.get("guarantor") or 0)) if pact.get("guarantor") else None,
            signed=pacts.has_signed(pact, g.uid) if g.uid else False,
            my_sig=pacts.signature_of(pact, g.uid) if g.uid else None,
            is_party=pacts.is_party(pact, g.uid) if g.uid else False,
            my_side="a" if g.uid and int(pact.get("side_a") or 0) == g.uid else ("b" if g.uid and int(pact.get("side_b") or 0) == g.uid else ""),
        )

    @app.get("/u/<int:uid>")
    def profile(uid):
        rec = users.get(s, uid)
        if not rec.get("joined_at") and not rec.get("username"):
            abort(404)
        return render_template(
            "profile.html", brand=users.display(rec), active="",
            p=view_profile(rec),
            pacts_list=[view_pact(x) for x in pacts.list_for(s, uid, 20)],
            events=[dict(e, delta=float(e.get("delta") or 0), when=ts_short(e.get("ts")))
                    for e in rating.events(s, uid, 25)],
            ginfo=guarantors.tier_info(s, uid),
            is_me=uid == g.uid,
        )

    @app.get("/my")
    def my_pacts():
        guard = require_login()
        if guard:
            return guard
        rows = [view_pact(p) for p in pacts.list_for(s, g.uid, 60)]
        return render_template("board.html", brand="Мои пакты", active="my",
                               rows=rows, kind="", status="", q="", mine=True,
                               kinds=terms.PACT_KINDS, order=terms.KIND_ORDER,
                               statuses=terms.STATUSES, total=len(rows))

    @app.get("/new")
    def new_pact():
        guard = require_login()
        if guard:
            return guard
        return render_template("new.html", brand="Новый пакт", active="new",
                               kinds=terms.PACT_KINDS, order=terms.KIND_ORDER,
                               default_kind=terms.DEFAULT_KIND,
                               guarantors=[dict(r, id=r["uid"], name=r["name"])
                                           for r in guarantors.leaderboard(s, 10)])

    # --------------------------------------------------------------- actions

    @app.post("/p/<pid>/sign")
    def pact_sign(pid):
        guard = require_login()
        if guard:
            return guard
        pact, changed, message = pacts.sign(s, pid, g.uid, _payload().get("text", ""))
        if changed and pacts.both_signed(pact):
            pact = pacts.activate(s, pid, g.uid)
            if pact.get("guarantor"):
                guarantors.add_case(s, int(pact["guarantor"]), pid)
            message = "Обе стороны подписали — пакт в силе"
        return redirect(_back(f"/p/{pid}", ok=message, err="" if changed else message))

    @app.post("/p/<pid>/close")
    def pact_close(pid):
        guard = require_login()
        if guard:
            return guard
        data = _payload()
        verdict = data.get("verdict", "completed")
        pact = pacts.get(s, pid)
        if not pact:
            abort(404)
        if verdict == "cancelled":
            _, applied = pacts.cancel(s, pid, g.uid, data.get("note", ""))
            return redirect(_back(f"/p/{pid}", ok="Пакт расторгнут"))
        if verdict == "broken":
            guilty = g.uid
            if data.get("guilty") == "other":
                guilty = pacts.counterparty_of(pact, g.uid)
            pacts.close(s, pid, guilty, "broken", data.get("note", ""))
            return redirect(_back(f"/p/{pid}", ok="Нарушение зафиксировано"))
        pacts.close(s, pid, g.uid, "completed", data.get("note", ""))
        return redirect(_back(f"/p/{pid}", ok="Пакт отмечен как исполненный"))

    @app.post("/new")
    def create_pact():
        guard = require_login()
        if guard:
            return guard
        data = _payload()
        cp = _resolve_person(data.get("counterparty", ""))
        if not cp:
            return redirect(_back("/new", err="Вторая сторона не найдена: укажите @username или id"))
        a_cond = _lines(data.get("a_conditions", ""))
        b_cond = _lines(data.get("b_conditions", ""))
        if not a_cond:
            return redirect(_back("/new", err="Добавьте хотя бы одно своё обязательство"))

        days, end_flag = _parse_days(data.get("term", "30"))
        start = now()
        end = 0 if end_flag else start + days * 86400
        guarant = int(data.get("guarantor") or 0)

        pact = pacts.create(
            s,
            author=g.uid, counterparty=cp,
            kind=data.get("kind", terms.DEFAULT_KIND),
            title=data.get("title", ""),
            a_conditions=a_cond, b_conditions=b_cond,
            term_start=start, term_end=end, guarantor=guarant,
            a_meta_extra={"channels": []}, b_meta_extra={"channels": []},
        )
        pact, changed, message = pacts.sign(s, pact["id"], g.uid, "Подтверждаю условия")
        return redirect(_back(f"/p/{pact['id']}", ok=f"{terms.ENTITY} создан и подписан вами"))

    @app.post("/report")
    def report():
        guard = require_login()
        if guard:
            return guard
        data = _payload()
        target = _resolve_person(data.get("target", ""))
        if not target or target == g.uid:
            return redirect(_back("/", err="Некорректный получатель жалобы"))
        rec = pacts.raise_report(
            s, reporter=g.uid, target=target,
            pid=data.get("pid", ""), reason=data.get("reason", "other"),
            text=data.get("text", ""),
        )
        return redirect(_back(f"/u/{target}", ok=f"Жалоба {rec['id']} отправлена на рассмотрение"))

    @app.post("/guarantor/apply")
    def guarantor_apply():
        guard = require_login()
        if guard:
            return guard
        ok, why = guarantors.apply_application(s, g.uid)
        return redirect(_back("/guarantors", ok=why if ok else "", err="" if ok else why))

    # ------------------------------------------------------------------- api

    @app.get("/healthz")
    def healthz():
        return jsonify(ok=s.ping(), app=terms.APP_NAME, api="1")

    @app.get("/api/stats")
    def api_stats():
        return jsonify(discovery.counters(s))

    @app.get("/api/board")
    def api_board():
        rows = discovery.board(s, request.args.get("kind", ""),
                               request.args.get("status", ""),
                               request.args.get("q", ""),
                               int(request.args.get("limit", 20)))
        return jsonify(rows)

    @app.get("/api/p/<pid>")
    def api_pact(pid):
        pact = pacts.get(s, pid)
        if not pact:
            abort(404)
        out = view_pact(pact)
        out["sides"] = [view_side(pacts.side_meta(pact, int(pact.get("side_a") or 0))),
                        view_side(pacts.side_meta(pact, int(pact.get("side_b") or 0)))]
        out["signatures"] = pacts.signatures(pact)
        out["timeline"] = pacts.timeline(s, pid, 40)
        out["health"] = pacts.health(s, pid)
        return jsonify(out)

    @app.get("/api/u/<int:uid>")
    def api_profile(uid):
        rec = users.get(s, uid)
        if not rec:
            abort(404)
        out = view_profile(rec)
        out["events"] = rating.events(s, uid, 25)
        out["pacts"] = [view_pact(p) for p in pacts.list_for(s, uid, 20)]
        return jsonify(out)

    @app.get("/api/rating")
    def api_rating():
        ok, note = rating.verify_chain(s, 200)
        return jsonify(chain_ok=ok, chain_note=note, events=ledger_rows(60))

    @app.get("/api/me")
    def api_me():
        if not g.uid:
            return jsonify({"authenticated": False})
        return jsonify({"authenticated": True, "uid": g.uid, "profile": g.me,
                        "csrf": g.csrf})

    # -------------------------------------------------------------- errors

    @app.errorhandler(404)
    def nf(_e):
        return render_template("404.html", brand="Не найдено", active=""), 404

    @app.errorhandler(500)
    def err(_e):
        return render_template("404.html", brand="Ошибка", active=""), 500

    @app.errorhandler(redis.RedisError)
    def db_down(_e):
        # Wrong REDIS_URL, an unreachable host or a throttled serverless Redis
        # should not look like a broken site.
        app.logger.warning("redis unavailable", exc_info=_e)
        return render_template("503.html", brand="База недоступна", active=""), 503

    return app


def _resolve_person(raw: str) -> int:
    raw = (raw or "").strip()
    if not raw:
        return 0
    raw = raw.lstrip("@").replace("https://t.me/", "").strip()
    if raw.lstrip("-").isdigit():
        return int(raw)
    rec = users.by_username(store(), raw)
    return int(rec["id"]) if rec and rec.get("id") else 0


def _parse_days(raw: str) -> tuple[int, bool]:
    raw = (raw or "").strip().lower()
    if raw in ("бессрочно", "0", "нет", "-", "∞"):
        return 0, True
    digits = "".join(ch for ch in raw if ch.isdigit())
    return (int(digits) or 30), False


def main() -> None:
    app = create_app()
    app.run(host=config.host, port=config.port, debug=config.debug, threaded=True)


if __name__ == "__main__":
    main()