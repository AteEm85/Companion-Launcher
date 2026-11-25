"""XP, levels and prestige, built on the games already saved in ranked.db (see ranked.py).

XP = per kill by the victim's difficulty (easiest 4, easy 6, normal 10, hard 15, nightmare 24, hell 35; a player kill = 30)
+ 1 per minute played (deaths cost nothing). Levels 1..MAX_LEVEL need a growing amount of XP.
Every one of the six characters has its own XP, level and prestige per player: kills only count for the character used in that game.
At MAX_LEVEL the player may prestige (up to 3 times): XP counted from that moment starts again at level 1 and the
prestige count goes up (per character). Prestige 1/2/3 = bronze / silver / gold variant of that character (see variant_model)."""
import sqlite3
import time

import ranked

KILL_XP = 10                                   # kills saved before per-kill XP existed count as a Normal bot
LEVEL_XP = {"easiest": 4, "easy": 6, "normal": 10, "hard": 15, "nightmare": 24, "hell": 35}   # XP for killing a bot of that difficulty
PLAYER_KILL_XP = 30                            # killing another player
MINUTE_XP = 1
MAX_LEVEL = 50
MAX_PRESTIGE = 3
TIERS = ["", "BRZ", "SLV", "GLD"]          # file-name suffix per prestige count
CHARACTERS = {"sanchez": "SANCHEZ", "james": "JAMES", "twofeathers": "TWOFEATH", "drdeath": "DRDEATH", "bob": "Bobgrahm", "mary": "MARY"}   # key -> model file stem


def char_key(name):
    """'James Anderson' / 'james' / 'Dr Death' / 'Two Feathers' -> 'james' / 'drdeath' / 'twofeathers'; '' when it is not one of the six."""
    k = "".join(ch for ch in (name or "").lower() if ch.isalnum())
    for key in CHARACTERS:
        if k.startswith(key) or (key == "twofeathers" and k.startswith("twofeath")) or (key == "bob" and k.startswith("bobgrahm")):
            return key
    return ""


def xp_for_level(level):
    """Total XP needed to reach `level` (level 1 = 0)."""
    n = max(level, 1) - 1
    return int(100 * n + 20 * n * n)


def level_of(xp):
    lvl = 1
    while lvl < MAX_LEVEL and xp >= xp_for_level(lvl + 1):
        lvl += 1
    return lvl


def _conn():
    c = ranked.connect()
    _events_table(c)
    c.execute("CREATE TABLE IF NOT EXISTS prestige2 (player TEXT NOT NULL COLLATE NOCASE, character TEXT NOT NULL, count INTEGER NOT NULL DEFAULT 0, "
              "since REAL NOT NULL DEFAULT 0, PRIMARY KEY (player, character))")
    return c


def _events_table(c):
    c.execute("CREATE TABLE IF NOT EXISTS kill_events (id INTEGER PRIMARY KEY AUTOINCREMENT, player TEXT NOT NULL COLLATE NOCASE, "
              "victim TEXT, level TEXT, xp INTEGER NOT NULL, t REAL NOT NULL)")
    if "character" not in {r[1] for r in c.execute("PRAGMA table_info(kill_events)")}:
        c.execute("ALTER TABLE kill_events ADD COLUMN character TEXT NOT NULL DEFAULT ''")
    c.execute("CREATE TABLE IF NOT EXISTS xp_meta (k TEXT PRIMARY KEY, v REAL)")
    c.execute("CREATE TABLE IF NOT EXISTS xp_archive (id INTEGER PRIMARY KEY AUTOINCREMENT, player TEXT NOT NULL COLLATE NOCASE, character TEXT, "
              "kills INTEGER NOT NULL, started REAL NOT NULL, ended REAL NOT NULL)")      # games removed by Reset Stats: their XP stays
    c.execute("INSERT OR IGNORE INTO xp_meta (k, v) VALUES ('events_from', ?)", (time.time(),))   # games before this moment have no per-kill records
    if not c.execute("SELECT 1 FROM xp_meta WHERE k='repaired_v1'").fetchone():
        _repair(c)
        c.execute("INSERT INTO xp_meta (k, v) VALUES ('repaired_v1', ?)", (time.time(),))


def _repair(c):
    """One-off clean-up of the first records: the same kill seen by several observers (same victim within 3 s) is kept once, and
    kills with no character get the character of the saved game they happened in."""
    rows = c.execute("SELECT id, player, victim, t FROM kill_events ORDER BY player, victim, t").fetchall()
    drop, last = [], {}
    for i, p, v, t in rows:
        k = (p.lower(), v)
        if k in last and t - last[k] <= 3.0:
            drop.append(i)
        else:
            last[k] = t
    c.executemany("DELETE FROM kill_events WHERE id=?", [(i,) for i in drop])
    for i, p, t in c.execute("SELECT id, player, t FROM kill_events WHERE character=''").fetchall():
        row = c.execute("SELECT character FROM sessions WHERE player=? COLLATE NOCASE AND started<=? AND ended>=? ORDER BY started DESC LIMIT 1", (p, t, t)).fetchone()
        ch = char_key(row[0]) if row else ""
        if ch:
            c.execute("UPDATE kill_events SET character=? WHERE id=?", (ch, i))


def kill_xp(level):
    return LEVEL_XP.get(level or "normal", LEVEL_XP["normal"])


def record_kill(player, victim, level=None, human=False, character=""):
    """One kill by `player`. level = the victim bot's difficulty (a key of LEVEL_XP); human=True: the victim was a player.
    Returns the XP given."""
    lvl = "player" if human else (level if level in LEVEL_XP else "normal")
    xp = PLAYER_KILL_XP if human else kill_xp(lvl)
    with _conn() as c:
        now = time.time()
        if c.execute("SELECT 1 FROM kill_events WHERE player=? AND victim=? AND t>?", (player, victim, now - 3.0)).fetchone():
            return 0                                   # several in-game observers see the same kill: count it once
        c.execute("INSERT INTO kill_events (player, victim, level, xp, t, character) VALUES (?,?,?,?,?,?)", (player, victim, lvl, xp, now, char_key(character)))
    return xp


def kills_between(player, character, t0, t1):
    """The per-kill records of one game, in the form relay.submit_game wants: [{victim, level, human, t}]."""
    with _conn() as c:
        rows = c.execute("SELECT victim, level, t FROM kill_events WHERE player=? AND character=? AND t>=? AND t<=? ORDER BY t",
                         (player, char_key(character), t0, t1)).fetchall()
    return [{"victim": v, "level": ("normal" if lv == "player" else lv), "human": lv == "player", "t": int(t)} for v, lv, t in rows]


def archive_player(player):
    """Reset Stats deletes the saved games; call this first so the XP they gave (minutes played, kills from before per-kill records) stays."""
    with _conn() as c:
        c.execute("INSERT INTO xp_archive (player, character, kills, started, ended) "
                  "SELECT player, character, kills, started, ended FROM sessions WHERE player=? COLLATE NOCASE", (player,))


def _raw_xp(c, player, character, since):
    """XP of this player's character: per-kill records, plus 10 per kill for older saved games (no records), plus minutes played."""
    (frm,) = c.execute("SELECT v FROM xp_meta WHERE k='events_from'").fetchone()
    rows = c.execute("SELECT character, kills, started, ended FROM sessions WHERE player=? COLLATE NOCASE AND started>=?", (player, since)).fetchall()
    rows += c.execute("SELECT character, kills, started, ended FROM xp_archive WHERE player=? COLLATE NOCASE AND started>=?", (player, since)).fetchall()
    old_k = sum(r[1] for r in rows if char_key(r[0]) == character and r[2] < frm)
    secs = sum((r[3] or 0) - r[2] for r in rows if char_key(r[0]) == character)
    (ev,) = c.execute("SELECT COALESCE(SUM(xp),0) FROM kill_events WHERE player=? AND character=? AND t>=?", (player, character, since)).fetchone()
    return int(old_k * KILL_XP + ev + secs / 60 * MINUTE_XP)


def status(player, character):
    """dict(character, xp, level, prestige, to_next, can_prestige, tier) for this player's character (a name or key)."""
    ch = char_key(character)
    with _conn() as c:
        row = c.execute("SELECT count, since FROM prestige2 WHERE player=? AND character=?", (player, ch)).fetchone()
        count, since = row if row else (0, 0.0)
        xp = _raw_xp(c, player, ch, since)
    lvl = level_of(xp)
    return {"character": ch, "xp": xp, "level": lvl, "prestige": count, "tier": TIERS[count],
            "to_next": None if lvl >= MAX_LEVEL else xp_for_level(lvl + 1) - xp,
            "can_prestige": lvl >= MAX_LEVEL and count < MAX_PRESTIGE}


def all_status(player):
    """status() of the six characters, as {key: status}."""
    return {k: status(player, k) for k in CHARACTERS}


def overview(player):
    """(source, {character key: status}) for the stats screen: the online rankings (relay.py) when set up and reachable, else this PC."""
    try:
        import relay
        if relay.configured():
            p = relay.profile(player)
            if p:
                return "online", p
    except Exception:
        pass
    return "this PC", all_status(player)


def do_prestige(player, character):
    """Prestige from the stats screen. With the relay set up the server decides (and must agree); this PC's own record follows it.
    Returns (ok, message)."""
    ch = char_key(character)
    if not ch:
        return False, "unknown character"
    try:
        import relay
        online = relay.configured() and relay.key_for(player)
    except Exception:
        online, relay = False, None
    if online:
        ok, r = relay.prestige(player, ch)
        if not ok:
            return False, str(r)
        with _conn() as c:                                   # keep this PC in step with the server (its XP may differ a little)
            now = time.time()
            c.execute("INSERT INTO prestige2 (player, character, count, since) VALUES (?,?,1,?) "
                      "ON CONFLICT(player, character) DO UPDATE SET count=count+1, since=?", (player, ch, now, now))
        return True, f"{ch} is now prestige {r['prestige']}"
    n = prestige(player, ch)
    return (True, f"{ch} is now prestige {n}") if n else (False, "needs level 50 (and fewer than 3 prestiges)")


def prestige(player, character):
    """Prestige this character now if allowed (max level, fewer than 3). Returns the new count, or None."""
    ch = char_key(character)
    if not ch or not status(player, ch)["can_prestige"]:
        return None
    with _conn() as c:
        c.execute("INSERT INTO prestige2 (player, character, count, since) VALUES (?,?,1,?) "
                  "ON CONFLICT(player, character) DO UPDATE SET count=count+1, since=?", (player, ch, time.time(), time.time()))
        return c.execute("SELECT count FROM prestige2 WHERE player=? AND character=?", (player, ch)).fetchone()[0]


def variant_model(player, character):
    """The model file this player's character spawns as: prestige 0 = the normal file (JAMES.NWX), then JAMESBRZ.NWX / SLV / GLD
    (stem cut so the name stays 8.3)."""
    ch = char_key(character)
    stem = CHARACTERS.get(ch)
    if not stem:
        return None
    t = status(player, ch)["tier"]
    return f"{stem}.NWX" if not t else f"{stem[:8 - len(t)]}{t}.NWX"


TIER_LETTER = ["", "B", "S", "G"]     # bronze, silver, gold: the game shows letters in a name but not colours or '*' (tested)


def rank_prefix(player, character):
    """'[12]' / '[12B]' / '[12S]' / '[12G]': this character's level (and prestige tier) for in front of the name."""
    st = status(player, character)
    return f"[{st['level']}{TIER_LETTER[st['prestige']]}]"


def display_name(player, character, max_len=15):
    """The prefix and then the name, cut to the 15 characters the game keeps for a name without extra memory
    ('[12]T4H_WaRRioR'); the name loses its end first."""
    prefix = rank_prefix(player, character)
    return prefix + player[:max(1, max_len - len(prefix))]
