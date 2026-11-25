"""Online rankings for ranked Survival Horde, kept as small JSON files in the GitHub repo (one per map):
    rankings/<map>.json = {"map": "<map>", "sessions": {"<session id>": {"p": player, "c": character, "k": kills, "d": deaths, "t": time}}}
Everybody reads them (the repo is public: no login needed); the rankings are worked out here, best kill/death ratio first.
Pushing a finished ranked game needs write access to the repo: a GitHub token in online_settings.json (never in the repo itself):
    {"repo": "AteEm85/Bot-Launcher", "token": "github_pat_...", "dir": "rankings"}
A game that can't be pushed (no token, no internet) waits in state\\online_outbox.jsonl and goes out the next time it can.
To use a relay server instead of a token later, only _put() has to change."""
import base64
import json
import os
import re
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
SETTINGS = HERE / "online_settings.json"
OUTBOX = HERE / "state" / "online_outbox.jsonl"
DEFAULTS = {"repo": "AteEm85/Bot-Launcher", "token": "", "dir": "rankings", "branch": "",
            "api": "https://api.github.com", "raw": "https://raw.githubusercontent.com"}
MAX_KILLS, MAX_DEATHS = 60, 500                     # more than a game can hold: rejected (cheap guard against made-up numbers)
_cache = {}


def cfg():
    c = dict(DEFAULTS)
    try:
        c.update({k: v for k, v in json.loads(SETTINGS.read_text()).items() if v not in (None,)})
    except (OSError, ValueError):
        pass
    if os.environ.get("OUTLAWS_RANK_TOKEN"):
        c["token"] = os.environ["OUTLAWS_RANK_TOKEN"]
    return c


def can_push():
    return bool(cfg()["token"])


def _http(method, url, token=None, body=None):
    """-> (status, parsed json or None). Never raises."""
    hdr = {"User-Agent": "outlaws-bot-launcher", "Accept": "application/vnd.github+json"}
    if token:
        hdr["Authorization"] = "Bearer " + token
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        hdr["Content-Type"] = "application/json"
    try:
        with urllib.request.urlopen(urllib.request.Request(url, data=data, headers=hdr, method=method), timeout=15) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        return e.code, None
    except (urllib.error.URLError, OSError, ValueError):
        return 0, None


def _contents_url(c, map_key):
    return f"{c['api']}/repos/{c['repo']}/contents/{c['dir']}/{map_key}.json"


def _get_file(c, map_key):
    """-> (sessions dict, sha or None, ok). ok False = could not tell (network, rate limit, private repo without a token)."""
    url = _contents_url(c, map_key) + (f"?ref={c['branch']}" if c["branch"] else "")
    st, js = _http("GET", url, c["token"] or None)
    if st == 200 and js and "content" in js:
        try:
            doc = json.loads(base64.b64decode(js["content"]))
            return dict(doc.get("sessions") or {}), js.get("sha"), True
        except (ValueError, TypeError):
            return {}, js.get("sha"), False
    if st == 404 and c["token"]:
        return {}, None, True                        # no file for this map yet (with a token a 404 is a true "not there")
    if st in (404, 403, 0) and not c["token"]:      # public read without the API: the raw file
        for br in ([c["branch"]] if c["branch"] else ["main", "master"]):
            st2, _ = 0, None
            try:
                req = urllib.request.Request(f"{c['raw']}/{c['repo']}/{br}/{c['dir']}/{map_key}.json", headers={"User-Agent": "outlaws-bot-launcher"})
                with urllib.request.urlopen(req, timeout=15) as r:
                    return dict(json.loads(r.read()).get("sessions") or {}), None, True
            except urllib.error.HTTPError as e:
                if e.code == 404:
                    continue
            except (urllib.error.URLError, OSError, ValueError):
                pass
        return {}, None, False
    return {}, None, False


def fetch(map_key, max_age=60):
    """The map's sessions from the repo, or None when they can't be read. Cached for max_age seconds."""
    hit = _cache.get(map_key)
    if hit and time.time() - hit[0] < max_age:
        return hit[1]
    sessions, _sha, ok = _get_file(cfg(), map_key)
    if not ok:
        return None
    _cache[map_key] = (time.time(), sessions)
    return sessions


def board(sessions, limit=50):
    """[(rank, player, kills, deaths, kd, games, favourite character)], best kill/death ratio first (same shape as ranked.leaderboard)."""
    per = {}
    for s in sessions.values():
        try:
            p, k, d = str(s["p"]), int(s["k"]), int(s["d"])
        except (KeyError, TypeError, ValueError):
            continue
        if not (0 <= k <= MAX_KILLS and 0 <= d <= MAX_DEATHS) or (k == 0 and d == 0):
            continue
        e = per.setdefault(p.lower(), {"name": p, "k": 0, "d": 0, "n": 0, "chars": {}})
        e["k"] += k
        e["d"] += d
        e["n"] += 1
        ch = str(s.get("c") or "")
        e["chars"][ch] = e["chars"].get(ch, 0) + 1
    rows = []
    for e in per.values():
        fav = max(e["chars"].items(), key=lambda kv: kv[1])[0] if e["chars"] else ""
        rows.append((e["name"], e["k"], e["d"], e["k"] / max(e["d"], 1), e["n"], fav))
    rows.sort(key=lambda r: (-r[3], -r[1], r[0].lower()))
    return [(i + 1, p, k, d, round(kd, 2), n, f) for i, (p, k, d, kd, n, f) in enumerate(rows[:limit])]


def leaderboard(map_key, limit=50):
    """The online ranking of a map, or None when the repo can't be read."""
    s = fetch(map_key)
    return None if s is None else board(s, limit)


def clean_name(name):
    name = re.sub(r"[^\w .\-']", "", str(name or ""), flags=re.U).strip()
    return name[:24]


def _put(c, map_key, sid, entry):
    """Add one session to the map's file. True when it is in the repo afterwards (or was already)."""
    for attempt in range(5):
        sessions, sha, ok = _get_file(c, map_key)
        if not ok:
            return False
        if sid in sessions:
            return True
        sessions[sid] = entry
        doc = {"map": map_key, "sessions": dict(sorted(sessions.items()))}
        body = {"message": f"ranked: {entry['p']} on {map_key} ({entry['k']} kills, {entry['d']} deaths)",
                "content": base64.b64encode(json.dumps(doc, indent=1).encode()).decode()}
        if sha:
            body["sha"] = sha
        if c["branch"]:
            body["branch"] = c["branch"]
        st, _ = _http("PUT", _contents_url(c, map_key), c["token"], body)
        if st in (200, 201):
            _cache.pop(map_key, None)
            return True
        if st in (409, 422):                          # someone pushed at the same moment: read again and retry
            time.sleep(0.6 + attempt * 0.7)
            continue
        return False
    return False


def _queue(item):
    OUTBOX.parent.mkdir(exist_ok=True)
    with open(OUTBOX, "a") as f:
        f.write(json.dumps(item) + "\n")


def flush_outbox():
    """Push every waiting game. Returns how many went out."""
    c = cfg()
    if not c["token"] or not OUTBOX.exists():
        return 0
    try:
        items = [json.loads(l) for l in OUTBOX.read_text().splitlines() if l.strip()]
    except (OSError, ValueError):
        return 0
    left, sent = [], 0
    for it in items:
        if _put(c, it["map"], it["sid"], it["entry"]):
            sent += 1
        else:
            left.append(it)
    try:
        if left:
            OUTBOX.write_text("".join(json.dumps(i) + "\n" for i in left))
        else:
            OUTBOX.unlink()
    except OSError:
        pass
    return sent


def submit(sid, player, character, map_key, kills, deaths, when=None):
    """A finished ranked game: queue it and try to push it right away. True when it reached the repo."""
    player = clean_name(player)
    try:
        kills, deaths = int(kills), int(deaths)
    except (TypeError, ValueError):
        return False
    if not player or not sid or not (0 <= kills <= MAX_KILLS and 0 <= deaths <= MAX_DEATHS) or (kills == 0 and deaths == 0):
        return False
    _queue({"map": map_key, "sid": str(sid), "entry": {"p": player, "c": str(character or ""), "k": kills, "d": deaths, "t": int(when or time.time())}})
    return flush_outbox() > 0 or not OUTBOX.exists()


if __name__ == "__main__":
    import sys
    for key in ("hargrove", "drygulch", "lilbatl"):
        print(key, leaderboard(key))
