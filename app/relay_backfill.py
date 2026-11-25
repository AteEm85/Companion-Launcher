"""One-off: send this PC's saved games (ranked.db) to the rankings relay so earlier play counts. Run:  .venv\\Scripts\\python.exe relay_backfill.py YOURNAME
The server counts each game id once, so running it again does nothing new. Games with no known character are skipped."""
import sqlite3
import sys

import ranked
import relay
import xp


def main(player):
    if not relay.configured():
        print("relay_settings.json has no url")
        return
    ok, msg = relay.claim(player)
    print("name:", msg)
    if not ok:
        return
    with ranked.connect() as c:
        rows = c.execute("SELECT id, character, map, kills, deaths, suicides, ranked, started, ended FROM sessions WHERE player=? COLLATE NOCASE ORDER BY started", (player,)).fetchall()
    sent = skipped = 0
    for sid, ch, mp, k, d, su, rk, t0, t1 in rows:
        ck = xp.char_key(ch)
        if not ck or not t0 or not t1:
            skipped += 1
            continue
        kl = xp.kills_between(player, ck, t0 - 2, t1 + 2)[:k]
        if relay.submit_game(sid, player, ck, mp, t0, t1, k, d, su, bool(rk), kl, max(0, k - len(kl))):
            sent += 1
        else:
            skipped += 1
    print(f"sent {sent} games, {skipped} skipped (no character, or the server refused them); details in state\\relay_log.txt")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "")
