"""Ranked Survival Horde: the player's results saved in a small database (SQLite), ranked by kill/death ratio, one
ranking per map. The database is ranked.db next to the launcher; set RANKED_DB to a path on a shared drive to share it."""
import json
import os
import sqlite3
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
DB = Path(os.environ.get("RANKED_DB") or HERE / "ranked.db")
RANKED_MAPS = {"hargrove": "Repair", "drygulch": "Dry Gulch", "lilbatl": "Bunkers"}


def connect():
    c = sqlite3.connect(str(DB), timeout=10)
    c.execute("""CREATE TABLE IF NOT EXISTS sessions (
        id TEXT PRIMARY KEY, player TEXT NOT NULL, character TEXT, map TEXT NOT NULL,
        kills INTEGER NOT NULL DEFAULT 0, deaths INTEGER NOT NULL DEFAULT 0,
        started REAL NOT NULL, ended REAL NOT NULL, mode TEXT NOT NULL DEFAULT 'horde')""")
    cols = {row[1] for row in c.execute("PRAGMA table_info(sessions)")}
    for col in ("suicides", "ranked", "causes"):                       # added later: older databases get them here
        if col not in cols:
            c.execute("ALTER TABLE sessions ADD COLUMN causes TEXT" if col == "causes" else
                      f"ALTER TABLE sessions ADD COLUMN {col} INTEGER NOT NULL DEFAULT {1 if col == 'ranked' else 0}")
    return c


def save_session(sid, player, character, map_key, kills, deaths, started, suicides=0, ranked=True, causes=None):
    """Insert or update this session's row (called every few seconds, so a killed game still leaves its stats)."""
    if not player or (ranked and map_key not in RANKED_MAPS):
        return
    with connect() as c:
        c.execute("INSERT INTO sessions (id, player, character, map, kills, deaths, started, ended, suicides, ranked, causes) "
                  "VALUES (?,?,?,?,?,?,?,?,?,?,?) "
                  "ON CONFLICT(id) DO UPDATE SET kills=excluded.kills, deaths=excluded.deaths, ended=excluded.ended, "
                  "character=excluded.character, suicides=excluded.suicides, ranked=excluded.ranked, causes=COALESCE(excluded.causes, causes)",
                  (sid, player, character, map_key, kills, deaths, started, time.time(), suicides, 1 if ranked else 0, json.dumps(causes) if causes else None))


def leaderboard(map_key, limit=50):
    """[(rank, player, kills, deaths, kd, games, favourite character)] best kill/death ratio first."""
    with connect() as c:
        rows = c.execute("SELECT player, SUM(kills), SUM(deaths), COUNT(*) FROM sessions WHERE map=? AND ranked=1 AND (kills>0 OR deaths>0) "
                         "GROUP BY player", (map_key,)).fetchall()
        out = []
        for player, k, d, n in rows:
            fav = c.execute("SELECT character FROM sessions WHERE map=? AND player=? AND ranked=1 GROUP BY character "
                            "ORDER BY COUNT(*) DESC LIMIT 1", (map_key, player)).fetchone()
            out.append((player, k, d, k / max(d, 1), n, (fav or ("",))[0] or ""))
    out.sort(key=lambda r: (-r[3], -r[1], r[0].lower()))
    return [(i + 1, p, k, d, round(kd, 2), n, f) for i, (p, k, d, kd, n, f) in enumerate(out[:limit])]


LAST_SOURCE = "this PC"


def best_leaderboard(map_key, limit=50):
    """The online ranking when it can be read (see onlinerank.py), else this PC's own. LAST_SOURCE says which one it was."""
    global LAST_SOURCE
    try:                                              # the rankings relay first (relay.py), then the GitHub files, then this PC
        import relay
        rows = relay.leaderboard(map_key, limit)
        if rows is not None:
            LAST_SOURCE = "relay"
            return rows
    except Exception:
        pass
    try:
        import onlinerank
        rows = onlinerank.leaderboard(map_key, limit)
        if rows is not None:
            LAST_SOURCE = "online"
            return rows
    except Exception:
        pass
    LAST_SOURCE = "this PC"
    return leaderboard(map_key, limit)


def reset_player(player):
    """Delete every saved game of this player (their Stats and their place in the rankings). Returns the rows removed."""
    try:
        import xp
        xp.archive_player(player)               # the XP those games gave stays (levels and prestige are not reset by Reset Stats)
    except Exception:
        pass
    with connect() as c:
        return c.execute("DELETE FROM sessions WHERE player=? COLLATE NOCASE", (player,)).rowcount


def player_stats(player):
    """(totals, per_map): totals = dict(games, kills, deaths, suicides, kd, favourite, minutes); per_map = rows
    (map label, games, kills, deaths, suicides, kd). Counts every saved game of that player, ranked or not."""
    with connect() as c:
        rows = c.execute("SELECT map, COUNT(*), SUM(kills), SUM(deaths), SUM(suicides), SUM(ended-started) FROM sessions "
                         "WHERE player=? COLLATE NOCASE GROUP BY map", (player,)).fetchall()
        fav = c.execute("SELECT character FROM sessions WHERE player=? COLLATE NOCASE AND ranked=1 AND character<>'' GROUP BY character "
                        "ORDER BY COUNT(*) DESC LIMIT 1", (player,)).fetchone()
    causes = {}
    with connect() as c:
        for (blob,) in c.execute("SELECT causes FROM sessions WHERE player=? COLLATE NOCASE AND causes IS NOT NULL", (player,)):
            try:
                for k_, v_ in json.loads(blob).items():
                    causes[k_] = causes.get(k_, 0) + v_
            except ValueError:
                pass
    per = [(m, n, k, d, s, round(k / max(d, 1), 2)) for m, n, k, d, s, _t in rows]
    k = sum(r[2] for r in per); d = sum(r[3] for r in per)
    tot = {"games": sum(r[1] for r in per), "kills": k, "deaths": d, "suicides": sum(r[4] for r in per),
           "kd": round(k / max(d, 1), 2), "favourite": (fav or ("",))[0] or "", "minutes": round(sum(r[5] or 0 for r in rows) / 60), "causes": causes}
    return tot, per


if __name__ == "__main__":
    import sys
    for key, label in RANKED_MAPS.items():
        print(label)
        for row in leaderboard(key):
            print("  ", row)
