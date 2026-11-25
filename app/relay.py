"""Client of the rankings relay (relay/worker.js: Cloudflare Worker + D1, or relay/local_server.mjs on a PC).
Settings: relay_settings.json next to this file  {"url": "https://outlaws-rankings.<you>.workers.dev", "keys": {"<player>": "<secret key>"}}
The key is made the first time a player name is claimed (claim()) and stays on that PC. Finished games are sent with submit_game();
one that cannot be sent (offline, server down) waits in state/relay_outbox.jsonl and goes out the next time. Nothing here raises on a network error."""
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
SETTINGS = HERE / "relay_settings.json"
OUTBOX = HERE / "state" / "relay_outbox.jsonl"
TIMEOUT = 8


def cfg():
    try:
        c = json.loads(SETTINGS.read_text())
    except (OSError, ValueError):
        c = {}
    c.setdefault("url", "")
    c.setdefault("keys", {})
    if os.environ.get("OUTLAWS_RELAY_URL"):
        c["url"] = os.environ["OUTLAWS_RELAY_URL"]
    return c


def configured():
    return bool(cfg()["url"])


def _save(c):
    SETTINGS.write_text(json.dumps(c, indent=1))


def _log(msg):
    """state/relay_log.txt: what the relay client did and why something failed (nothing here ever stops a game)."""
    try:
        f = HERE / "state" / "relay_log.txt"
        f.parent.mkdir(parents=True, exist_ok=True)
        with f.open("a", encoding="utf-8") as fh:
            fh.write(time.strftime("%H:%M:%S ") + msg + "\n")
    except OSError:
        pass


def _call(method, path, body=None):
    """(status, json) ; (0, {"error": ...}) when the server cannot be reached."""
    url = cfg()["url"].rstrip("/") + path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={"content-type": "application/json", "user-agent": "outlaws-bots"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        try:
            r = json.loads(e.read().decode() or "{}")
        except ValueError:
            r = {"error": str(e)}
        _log(f"{method} {path} -> {e.code} {r.get('error', '')}")
        return e.code, r
    except (urllib.error.URLError, OSError, ValueError) as e:
        _log(f"{method} {path} failed: {e}")
        return 0, {"error": str(e)}


def claim(player):
    """Claim a player name on the server and keep its secret key here. Returns (ok, message)."""
    c = cfg()
    if player in c["keys"]:
        return True, "already claimed on this PC"
    st, r = _call("POST", "/claim", {"player": player})
    if st == 200:
        c["keys"][player] = r["key"]
        _save(c)
        return True, "claimed"
    return False, r.get("error", f"server answered {st}")


def key_for(player):
    k = cfg()["keys"]
    return k.get(player) or next((v for n, v in k.items() if n.lower() == player.lower()), None)


def submit_game(game_id, player, character, map_key, started, ended, kills, deaths, suicides=0, ranked=False, kill_list=None, legacy_kills=0, mode="horde"):
    """Send one finished game (character = a key of xp.CHARACTERS). Queued in the outbox when it cannot be sent. Returns True when the server has it."""
    body = {"player": player, "id": game_id, "character": character, "map": map_key, "mode": mode, "kills": kills, "deaths": deaths,
            "suicides": suicides, "ranked": bool(ranked), "started": int(started), "ended": int(ended),
            "kill_list": kill_list or [], "legacy_kills": legacy_kills}
    if not configured():
        return False
    if not key_for(player):
        ok_, _msg = claim(player)                       # the first finished game under a name claims it on the server
        _log(f"claim {player}: {ok_} {_msg}")
        if not ok_ or not key_for(player):
            return False
    ok = _send(body)
    _log(f"game {game_id} ({player}, {character}, {map_key}, {len(kill_list or [])} kills): {'sent' if ok else 'not sent, queued'}")
    if not ok:
        try:
            OUTBOX.parent.mkdir(parents=True, exist_ok=True)
            with OUTBOX.open("a") as f:
                f.write(json.dumps(body) + "\n")
        except OSError:
            pass
    else:
        flush_outbox()
    return ok


def _send(body):
    k = key_for(body["player"])
    if not k:
        return False
    st, r = _call("POST", "/submit", dict(body, key=k))
    return st == 200 or (st == 400 and "numbers out of range" in str(r.get("error", "")))      # a game the server will never accept is dropped, not retried forever


def flush_outbox():
    """Send what is waiting in the outbox; keeps what still fails."""
    try:
        lines = [json.loads(x) for x in OUTBOX.read_text().splitlines() if x.strip()]
    except (OSError, ValueError):
        return 0
    left = [b for b in lines if not _send(b)]
    try:
        if left:
            OUTBOX.write_text("".join(json.dumps(b) + "\n" for b in left))
        else:
            OUTBOX.unlink()
    except OSError:
        pass
    return len(lines) - len(left)


def prestige(player, character):
    k = key_for(player)
    if not k:
        return False, "no key for this name on this PC (claim it first)"
    st, r = _call("POST", "/prestige", {"player": player, "key": k, "character": character})
    return st == 200, (r.get("status") if st == 200 else r.get("error", f"server answered {st}"))


def profile(player):
    """{character: {xp, level, prestige, to_next, can_prestige}} or None."""
    st, r = _call("GET", "/profile?player=" + urllib.parse.quote(player))
    return r.get("characters") if st == 200 else None


def leaderboard(map_key, limit=50):
    """Rows like ranked.leaderboard: [(rank, player, kills, deaths, kd, games, "")]; None when the relay is off or unreachable."""
    if not configured():
        return None
    st, r = _call("GET", f"/leaderboard?map={urllib.parse.quote(map_key)}&limit={int(limit)}")
    if st != 200 or not isinstance(r, list):
        return None
    return [(x["rank"], x["player"], x["kills"], x["deaths"], x["kd"], x["games"], "") for x in r]


def xp_board(character, limit=50):
    st, r = _call("GET", f"/xp?character={urllib.parse.quote(character)}&limit={int(limit)}")
    return r if st == 200 and isinstance(r, list) else None
