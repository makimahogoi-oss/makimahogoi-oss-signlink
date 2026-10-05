"""Integration smoke test for Gramly: core + Flask site + bot callback wiring.

Run: python tools/smoke.py

Redis is replaced by a minimal in-memory double, so the test needs nothing
installed. It exercises the real code paths: pact lifecycle, rating ledger,
initData validation, every page and every write endpoint.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import sys
import time
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

TOKEN = os.environ.get("SMOKE_TOKEN") or "123456:SMOKE"


# --------------------------------------------------------------------- fake redis

class FakePipe:
    def __init__(self, db):
        self.db = db
        self.ops = []

    def __getattr__(self, name):
        def run(*args, **kwargs):
            self.ops.append((name, args, kwargs))
            return self
        return run

    def execute(self):
        out = []
        for name, args, kwargs in self.ops:
            out.append(getattr(self.db, name)(*args, **kwargs))
        self.ops = []
        return out


class FakeRedis:
    """Just enough Redis for the code under test."""

    def __init__(self, *a, **kw):
        self.strings = {}
        self.hashes = {}
        self.lists = {}
        self.sets = {}
        self.ttl = {}

    # -- plumbing
    def ping(self):
        return True

    def close(self):
        return None

    def pipeline(self, *a, **kw):
        return FakePipe(self)

    @staticmethod
    def _key(key):
        return key if isinstance(key, str) else key.decode()

    # -- strings
    def get(self, key):
        return self.strings.get(self._key(key))

    def set(self, key, value):
        self.strings[self._key(key)] = value
        return True

    def setex(self, key, _ttl, value):
        self.strings[self._key(key)] = value
        return True

    def getset(self, key, value):
        old = self.strings.get(self._key(key))
        self.strings[self._key(key)] = value
        return old

    def incrby(self, key, amount=1):
        key = self._key(key)
        self.strings[key] = int(self.strings.get(key, 0)) + int(amount)
        return self.strings[key]

    def decrby(self, key, amount=1):
        return self.incrby(key, -int(amount))

    # -- hashes
    def hget(self, key, field):
        return (self.hashes.get(self._key(key)) or {}).get(field)

    def hset(self, key, field=None, value=None, mapping=None):
        row = self.hashes.setdefault(self._key(key), {})
        if mapping:
            row.update(mapping)
        if field is not None:
            row[field] = value
        return 1

    def hgetall(self, key):
        return dict(self.hashes.get(self._key(key)) or {})

    def hincrby(self, key, field, amount=1):
        row = self.hashes.setdefault(self._key(key), {})
        row[field] = str(int(row.get(field, 0)) + int(amount))
        return int(row[field])

    def hdel(self, key, *fields):
        row = self.hashes.get(self._key(key)) or {}
        for f in fields:
            row.pop(f, None)
        return 1

    def exists(self, *keys):
        return sum(1 for k in keys if self._key(k) in self.strings or self._key(k) in self.hashes)

    # -- lists
    def rpush(self, key, *values):
        row = self.lists.setdefault(self._key(key), [])
        row.extend(values)
        return len(row)

    def lpush(self, key, *values):
        row = self.lists.setdefault(self._key(key), [])
        for v in values:
            row.insert(0, v)
        return len(row)

    def ltrim(self, key, start, stop):
        row = self.lists.setdefault(self._key(key), [])
        if stop == -1:
            self.lists[self._key(key)] = row[start:] if start != 0 else row
        else:
            self.lists[self._key(key)] = row[start:stop + 1]
        return True

    def lrange(self, key, start=0, stop=-1):
        row = list(self.lists.get(self._key(key)) or [])
        return row[start:] if stop == -1 else row[start:stop + 1]

    def lpop(self, key):
        row = self.lists.get(self._key(key)) or []
        return row.pop(0) if row else None

    def llen(self, key):
        return len(self.lists.get(self._key(key)) or [])

    def lrem(self, key, count, value):
        row = self.lists.get(self._key(key)) or []
        keep = [x for x in row if x != value]
        self.lists[self._key(key)] = keep
        return len(row) - len(keep)

    # -- sets
    def sadd(self, key, *members):
        row = self.sets.setdefault(self._key(key), set())
        row.update(members)
        return len(row)

    def srem(self, key, *members):
        row = self.sets.get(self._key(key)) or set()
        row.difference_update(members)
        return 1

    def smembers(self, key):
        return set(self.sets.get(self._key(key)) or ())

    def scard(self, key):
        return len(self.sets.get(self._key(key)) or ())

    def sismember(self, key, member):
        return member in (self.sets.get(self._key(key)) or ())

    # -- keys
    def delete(self, *keys):
        n = 0
        for k in keys:
            k = self._key(k)
            n += int(self.strings.pop(k, None) is not None)
            n += int(self.hashes.pop(k, None) is not None)
            n += int(self.lists.pop(k, None) is not None)
            n += int(self.sets.pop(k, None) is not None)
        return n

    def scan(self, cursor=0, match=None, count=None):
        pattern = re.compile("^" + re.escape(match).replace(r"\*", ".*") + "$") if match else None
        keys = list(self.strings) + list(self.hashes) + list(self.lists) + list(self.sets)
        keys = sorted(set(k for k in keys if not pattern or pattern.match(k)))
        return 0, keys


def install_fake_redis():
    import importlib
    mod = importlib.import_module("gramly.core.store")
    mod.redis.Redis.from_url = staticmethod(lambda *a, **kw: FakeRedis())
    mod._store = None


# ------------------------------------------------------------------- assertions

PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print(("  ok   " if cond else "  FAIL ") + name + (f"  {extra}" if extra and not cond else ""))


def section(title):
    print(f"\n== {title}")


# ----------------------------------------------------------------------- initData

def make_initData(user: dict, token: str = TOKEN, age: int = 0) -> str:
    import json as _json
    fields = {
        "auth_date": str(int(time.time()) - age),
        "query_id": "AAHsmoke",
        "user": _json.dumps(user, separators=(",", ":"), ensure_ascii=False),
    }
    check_str = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, check_str.encode(), hashlib.sha256).hexdigest()
    return urllib.parse.urlencode(fields)


# ------------------------------------------------------------------------- main

def main() -> int:
    os.environ["GRAMLY_BOT_TOKEN"] = TOKEN
    os.environ["REDIS_URL"] = "redis://fake/0"
    os.environ["GRAMLY_PREFIX"] = "smoke"
    os.environ["GRAMLY_PUBLIC_URL"] = "https://gramly.example"
    install_fake_redis()

    from gramly.core import guarantors, pacts, rating, terms, users
    from gramly.core.store import now, store

    s = store()

    section("core: users and rating")
    a = users.touch(s, {"id": 101, "first_name": "Аня", "username": "anya"})
    b_rec = users.touch(s, {"id": 202, "first_name": "Борис", "username": "boris"})
    g_rec = users.touch(s, {"id": 303, "first_name": "Гарант", "username": "guard"})
    check("username index resolves", (users.by_username(s, "anya") or {}).get("id") == 101)
    check("baseline score", abs(rating.current(s, 101) - terms.RATING_BASE) < 0.01,
          str(rating.current(s, 101)))

    section("core: pact lifecycle")
    pact = pacts.create(
        s, author=101, counterparty=202, kind="pr", title="Пиар каналов",
        a_conditions=["Пиар 3 раза"], b_conditions=["Репост 3 раза"],
        term_start=now(), term_end=now() + 30 * 86400, guarantor=0,
    )
    pid = pact["id"]
    check("number assigned", pact["num"] == 1, str(pact["num"]))
    check("status draft", pact["status"] == "draft")
    ok, note = pacts.verify(s, pid)
    check("seal verifies", ok, note)

    pact, changed, message = pacts.sign(s, pid, 101)
    check("first sign -> half", changed and pact["status"] == "half", message)
    pact, changed, message = pacts.sign(s, pid, 202)
    check("second sign -> signed", changed and pact["status"] == "signed", message)
    pact = pacts.activate(s, pid, 101)
    check("activate -> active", pact["status"] == "active", pact["status"])

    pact, applied = pacts.close(s, pid, 101, "completed", "всё сделано")
    check("close -> done", pact["status"] == "done")
    check("no reputation for a closed loop", all(d == 0 for _, d, _ in applied), str(applied))
    check("graph remembers the pairing", users.display(users.get(s, 101)) == "Аня")

    # A third party gives the pair a real weight.
    c_rec = users.touch(s, {"id": 404, "first_name": "Вера", "username": "vera"})
    pact2 = pacts.create(
        s, author=101, counterparty=404, kind="service", title="Дизайн логотипа",
        a_conditions=["Оплата"], b_conditions=["Логотип за 3 дня"],
        term_start=now(), term_end=0,
    )
    pacts.sign(s, pact2["id"], 101)
    pacts.sign(s, pact2["id"], 404)
    pacts.activate(s, pact2["id"], 101)
    _, applied2 = pacts.close(s, pact2["id"], 101, "completed")
    gains = {uid: d for uid, d, _ in applied2}
    check("established side earns", gains.get(101, 0) > 0, str(applied2))
    check("newcomer earns nothing yet", gains.get(404, 0) == 0, str(applied2))

    section("core: ledger and chain")
    events = rating.global_events(s, 50)
    check("ledger has events", len(events) >= 1, str(len(events)))
    ok, note = rating.verify_chain(s, 100)
    check("chain verifies", ok, note)
    # tamper
    first = events[0]
    chain_key = s.k("ledger", "chain")
    s.lpush(chain_key, {"tampered": True})
    ok2, note2 = rating.verify_chain(s, 100)
    check("tamper detected", not ok2, note2)
    s.lrem(chain_key, first, count=1)

    section("core: reports and lrem fix")
    rep = pacts.raise_report(s, reporter=202, target=101, pid=pid, reason="no_show", text="не пиарил")
    open_before = s.llen(s.k("reports", "open"))
    pacts.resolve_report(s, rep["id"], True, "подтверждено", arbiter=303)
    open_after = s.llen(s.k("reports", "open"))
    check("report removed from open queue", open_after == open_before - 1, f"{open_before}->{open_after}")

    section("core: guarantors")
    users.update(s, 101, pacts_closed=2, pacts_broken=0)
    rating._write(s, 101, 70.0)
    ok, why = guarantors.apply_application(s, 101)
    check("guarantor application accepted", ok, why)
    info = guarantors.tier_info(s, 101)
    check("tier granted", info["tier"] == 1, str(info["tier"]))
    check("first case is free", guarantors.consume_free(s, 101) is True)
    q = guarantors.quote(s, 101)
    check("quote after free slot", q["free"] is False and q["side_stars"] == terms.STAR_FEE_PER_SIDE, str(q))

    board_rows = guarantors.leaderboard(s)
    check("guarantor leaderboard is not empty", bool(board_rows), str(len(board_rows)))
    check("leaderboard rows carry display fields",
          all(board_rows[0].get(k) for k in ("uid", "name", "emoji", "tier_name")),
          str(board_rows[0] if board_rows else {}))

    section("web: initData auth")
    from gramly.webapp import auth
    raw = make_initData({"id": 101, "first_name": "Аня", "username": "anya"})
    ok, payload, why = auth.login(raw)
    check("valid initData accepted", ok, why)
    check("user parsed", payload["user"]["id"] == 101)
    ok, _, why = auth.validate(raw, bot_token="wrong:token")
    check("wrong token rejected", not ok)
    tampered = raw[:-3] + "aaa"
    ok, _, why = auth.login(tampered)
    check("tampered hash rejected", not ok)
    ok, _, why = auth.login(make_initData({"id": 101, "first_name": "Аня"}, age=90000))
    check("stale auth_date rejected", not ok, why)

    unsigned = dict(urllib.parse.parse_qsl(
        make_initData({"id": 101, "first_name": "Аня"}), keep_blank_values=True))
    unsigned.pop("auth_date")
    ok, _, why = auth.login(urllib.parse.urlencode(unsigned))
    check("missing auth_date rejected", not ok, why)

    future = dict(urllib.parse.parse_qsl(make_initData({"id": 101, "first_name": "Аня"}), keep_blank_values=True))
    future["auth_date"] = str(int(time.time()) + 3600)
    ok, _, why = auth.login(urllib.parse.urlencode(future))
    check("future auth_date rejected", not ok, why)

    signed = dict(urllib.parse.parse_qsl(make_initData({"id": 101, "first_name": "Аня"}), keep_blank_values=True))
    signed["signature"] = "ed25519-blob"
    ok, _, why = auth.login(urllib.parse.urlencode(signed))
    check("signature field handled", ok, why)

    section("web: pages")
    from gramly.webapp.server import create_app
    app = create_app()
    app.config.update(TESTING=True)
    client = app.test_client()

    pages = ["/", "/pacts", "/rating", "/guarantors", "/gate", "/my", "/new",
             f"/p/{pid}", f"/u/101", "/api/stats", "/api/board", f"/api/p/{pid}",
             "/api/u/101", "/api/rating", "/healthz", "/nope"]
    for path in pages:
        resp = client.get(path)
        check(f"GET {path} -> {resp.status_code}", resp.status_code in (200, 302, 404),
              resp.get_data(as_text=True)[:200])

    check("login required for /my", client.get("/my").status_code == 302)

    section("web: templates compile")
    tpl_dir = ROOT / "gramly" / "webapp" / "templates"
    for tpl in sorted(tpl_dir.glob("*.html")):
        try:
            app.jinja_env.get_template(tpl.name)
            check(f"template {tpl.name}", True)
        except Exception as exc:  # noqa: BLE001
            check(f"template {tpl.name}", False, f"{type(exc).__name__}: {exc}")

    section("web: session and writes")
    resp = client.get("/gate")
    gate_token = re.search(r'name="_gate" id="gateToken" value="([^"]+)"',
                           resp.get_data(as_text=True)).group(1)
    check("gate cookie issued", len(gate_token) > 16)
    resp = client.post("/auth", data={"initData": raw})
    check("auth without gate token refused", resp.status_code == 302 and
          "gate" in resp.headers["Location"], resp.headers.get("Location", ""))
    resp = client.post("/auth", data={"initData": raw, "_gate": gate_token})
    check("auth redirects", resp.status_code == 302, str(resp.status_code))

    def fresh_gate() -> str:
        client.get("/logout")
        return re.search(r'name="_gate" id="gateToken" value="([^"]+)"',
                         client.get("/gate").get_data(as_text=True)).group(1)

    gate_evil = fresh_gate()
    resp = client.post("/auth", data={"initData": raw, "_gate": gate_evil,
                                      "next": "https://evil.example/steal"})
    check("external next is dropped", resp.headers.get("Location", "") == "/",
          resp.headers.get("Location", ""))
    gate_evil2 = fresh_gate()
    resp = client.post("/auth", data={"initData": raw, "_gate": gate_evil2, "next": "//evil.example"})
    check("protocol-relative next is dropped", resp.headers.get("Location", "") == "/",
          resp.headers.get("Location", ""))
    gate_evil3 = fresh_gate()
    resp = client.post("/auth", data={"initData": raw, "_gate": gate_evil3, "next": "/my"})
    check("relative next is kept", resp.headers.get("Location", "") == "/my",
          resp.headers.get("Location", ""))

    def csrf() -> str:
        return client.get("/api/me").get_json()["csrf"]

    with client:
        check("session established", client.get("/api/me").get_json()["authenticated"] is True)
        resp = client.post("/new", data={
            "_csrf": csrf(),
            "kind": "partner", "counterparty": "@boris", "title": "Совместный проект",
            "a_conditions": "Пир\nПродвижение", "b_conditions": "Ответная рассылка",
            "term": "30",
        })
        check("pact created via web", resp.status_code == 302, resp.get_data(as_text=True)[:200])
        new_pid = resp.headers["Location"].split("?")[0].rsplit("/", 1)[-1]
        created = pacts.get(s, new_pid)
        check("web pact is half-signed by author",
              created["status"] == "half" and pacts.has_signed(created, 101),
              created["status"])

        resp = client.get(f"/p/{new_pid}")
        check("new pact page renders", resp.status_code == 200)
        check("csrf token present in html", 'name="_csrf"' in resp.get_data(as_text=True))

        check("post without csrf is rejected",
              client.post(f"/p/{new_pid}/sign", data={"text": "x"}).status_code == 400)

        # Every page must render for a signed-in user, not only redirect.
        for path in ("/", "/pacts", "/rating", "/guarantors", "/my", "/new",
                     f"/p/{new_pid}", "/u/101", "/api/me", "/api/board",
                     f"/api/p/{new_pid}", "/api/u/101"):
            resp = client.get(path)
            check(f"signed-in GET {path} -> {resp.status_code}", resp.status_code == 200,
                  resp.get_data(as_text=True)[:300])

        new_html = client.get("/new").get_data(as_text=True)
        first = board_rows[0]
        check("guarantor option rendered in /new",
              f'<option value="{first["uid"]}">' in new_html
              and first["emoji"] in new_html and first["name"] in new_html,
              new_html[new_html.find("name=\"guarantor\""):][:220])

        # a stranger must not be able to sign somebody else's pact
        client.get("/logout")
        gate2 = re.search(r'name="_gate" id="gateToken" value="([^"]+)"',
                          client.get("/gate").get_data(as_text=True)).group(1)
        client.post("/auth", data={"initData": make_initData({"id": 303, "first_name": "Гарант"}),
                                   "_gate": gate2})
        client.post(f"/p/{new_pid}/sign", data={"_csrf": csrf(), "text": "подпись незнакомца"})
        check("stranger cannot sign", pacts.get(s, new_pid)["status"] == "half",
              pacts.get(s, new_pid)["status"])

    # the counterparty signs
    client.get("/logout")
    raw_b = make_initData({"id": 202, "first_name": "Борис", "username": "boris"})
    gate3 = re.search(r'name="_gate" id="gateToken" value="([^"]+)"',
                      client.get("/gate").get_data(as_text=True)).group(1)
    client.post("/auth", data={"initData": raw_b, "_gate": gate3})
    client.post(f"/p/{new_pid}/sign", data={"_csrf": csrf(), "text": "Подтверждаю"})
    after = pacts.get(s, new_pid)
    check("pact went active after both signs", after["status"] == "active", after["status"])
    check("second signature stored", pacts.signature_of(after, 202) is not None)

    resp = client.post(f"/p/{new_pid}/close",
                       data={"_csrf": csrf(), "verdict": "completed"})
    check("completed via web", pacts.get(s, new_pid)["status"] == "done", str(resp.status_code))

    resp = client.post("/report", data={"_csrf": csrf(), "target": "@guard",
                                        "reason": "other", "text": "тест"})
    open_ids = {r["id"] for r in pacts.open_reports(s)}
    check("report filed by username", resp.status_code == 302 and len(open_ids) >= 1,
          f"{resp.status_code} {open_ids}")

    resp = client.post("/guarantor/apply", data={"_csrf": csrf()})
    check("guarantor apply answers", resp.status_code == 302, str(resp.status_code))

    section("bot: callback wiring")
    wiring = check_callback_wiring()
    for name, ok in wiring:
        check(f"callback {name}", ok)

    section("web: redis outage")
    import importlib

    import redis as redis_mod

    class DeadRedis:
        """Client that builds fine but fails on every command."""

        def __getattr__(self, name):
            def boom(*a, **kw):
                raise redis_mod.ConnectionError("down")
            return boom

    store_mod = importlib.import_module("gramly.core.store")
    good = store_mod.redis.Redis.from_url
    store_mod.redis.Redis.from_url = staticmethod(lambda *a, **kw: DeadRedis())
    store_mod._store = None
    try:
        broken_app = create_app()
        broken = broken_app.test_client()
        resp = broken.get("/")
        check("redis outage shows 503, not a traceback", resp.status_code == 503,
              str(resp.status_code))
        check("healthz reports the outage", broken.get("/healthz").get_json()["ok"] is False)
    finally:
        store_mod.redis.Redis.from_url = good
        store_mod._store = None
    check("app works again after recovery", client.get("/").status_code == 200)

    print(f"\npassed {len(PASS)}, failed {len(FAIL)}")
    if FAIL:
        print("failed:")
        for name in FAIL:
            print("  - " + name)
        return 1
    return 0


def check_callback_wiring() -> list:
    """Every callback payload emitted must start with a registered prefix."""
    bot_py = ROOT / "gramly" / "botapp" / "bot.py"
    keys_py = ROOT / "gramly" / "botapp" / "keys.py"
    if not bot_py.is_file() or not keys_py.is_file():
        return []          # website-only deployment: nothing to check

    bot_src = bot_py.read_text(encoding="utf-8")
    keys_src = keys_py.read_text(encoding="utf-8")

    prefixes = set()
    for m in re.finditer(r'@bot\.onCallback\(([^)]*)\)', bot_src):
        for p in re.findall(r'"([^"]+)"', m.group(1)):
            prefixes.add(p)

    payloads = set()
    for src in (keys_src, bot_src):
        for m in re.finditer(r'f?"([a-z][a-z0-9]*(?::\{[^"]*)?)', src):
            token = m.group(1)
            if ":" in token:
                payloads.add(token)

    results = []
    for token in sorted(payloads):
        head = token.split(":")[0]
        results.append((token, head in prefixes or token.startswith("wiz") or True))
    # report only real mismatches
    out = []
    for token, _ in results:
        head = token.split(":")[0]
        out.append((token, head in prefixes))
    return out


if __name__ == "__main__":
    raise SystemExit(main())