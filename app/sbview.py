"""Draws the live scoreboard and the end-of-match card into a tkinter Canvas (scoreboard.py on the host PC, the companion app
for players who joined). Standard library only.

    sbview.draw(canvas, board, width, me="T4H_WaRRioR")          -> the height it used
    sbview.draw(canvas, board, width, card=True)                 -> the MATCH OVER card (MVP, most kills, streak, best shot) + table

board is livestats.build()'s dict (state/live_<run>.json, /live.json)."""

TIER_COLORS = {"easiest": "#c9c2a8", "easy": "#d6c36a", "normal": "#e0993a", "hard": "#d9602b", "nightmare": "#d02a1f",
               "hell": "#ff2a1a", "santa": "#ff3b3b"}
METAL_COLORS = {0: "#e8dcc0", 1: "#d08a4a", 2: "#cfd6de", 3: "#f2c443"}
BG, PANEL, LINE, TEXT, DIM, ME = "#101010", "#1b1712", "#3a3126", "#efe6d2", "#9a8f7a", "#f2c443"


def rows_for_view(board, max_ghost_rows=4):
    """players first; a crowd of bots (the Horde) is one line"""
    rows = list(board.get("rows") or [])
    people = [r for r in rows if not r.get("ghost")]
    bots = [r for r in rows if r.get("ghost")]
    if len(bots) > max_ghost_rows:
        horde = {"name": "The Horde" if "Christmas" not in (board.get("mode") or "") else "The Elves", "title": f"{len(bots)} on the field",
                 "kills": sum(r["kills"] for r in bots), "deaths": sum(r["deaths"] for r in bots),
                 "streak": max((r["streak"] for r in bots), default=0), "best_streak": max((r["best_streak"] for r in bots), default=0),
                 "shots": sum(r["shots"] for r in bots), "hits": sum(r["hits"] for r in bots),
                 "best_shot": max((r["best_shot"] for r in bots), default=0), "ghost": True}
        horde["acc"] = round(100.0 * horde["hits"] / horde["shots"]) if horde["shots"] else None
        bots = [horde]
    return people + bots


def draw(cv, board, W, me=None, card=False, font="Consolas"):
    cv.delete("all")
    if not board:
        return 0
    s = max(0.75, min(1.6, W / 900.0))
    f = lambda n, b=True: (font, int(n * s), "bold" if b else "normal")
    pad = int(18 * s)
    y = pad
    cv.create_rectangle(0, 0, W, 4000, fill=PANEL, outline=LINE, width=2, tags="bg")
    mode = board.get("mode") or "Match"
    title = "MATCH OVER" if card else "SCOREBOARD"
    cv.create_text(pad, y, text=title, font=f(20), fill=TEXT, anchor="nw")
    right = []
    tier = (board.get("tier") or "").lower()
    if tier:
        right.append((f"{mode.upper()}: {tier.upper()}", TIER_COLORS.get(tier, "#d6c36a")))
    elif mode:
        right.append((mode.upper(), DIM))
    if board.get("target") and board.get("team_kills") is not None and tier != "santa":
        right.append((f"{board['team_kills']}/{board['target']} kills", DIM))
    x = W - pad
    for text, col in reversed(right):
        cv.create_text(x, y + int(4 * s), text=text, font=f(12), fill=col, anchor="ne")
        x -= int((len(text) * 8 + 24) * s)
    y += int(38 * s)
    if board.get("map"):
        cv.create_text(pad, y, text=str(board["map"]), font=f(10, False), fill=DIM, anchor="nw")
        y += int(20 * s)
    if card:
        y = _card(cv, board, W, y, s, f, pad)
    cols = [("", 0.0, "w"), ("RANK", 0.04, "w"), ("NAME", 0.27, "w"), ("K", 0.60, "e"), ("D", 0.67, "e"),
            ("STREAK", 0.79, "e"), ("ACC", 0.88, "e"), ("BEST", 0.98, "e")]
    cv.create_line(pad, y, W - pad, y, fill=LINE)
    y += int(6 * s)
    for name, fx, anc in cols:
        cv.create_text(int(fx * (W - 2 * pad)) + pad, y, text=name, font=f(10), fill=DIM, anchor="n" + anc)
    y += int(22 * s)
    for i, r in enumerate(rows_for_view(board)):
        mine = me and r["name"].lower() == me.lower()
        if mine:
            cv.create_rectangle(pad - 4, y - 3, W - pad + 4, y + int(24 * s), fill="#2c2416", outline="")
        col = DIM if r.get("ghost") else (ME if mine else TEXT)
        streak = f"{r['streak']}" + (f" ({r['best_streak']})" if r["best_streak"] > r["streak"] else "")
        vals = [str(i + 1) if not r.get("ghost") else "", r.get("title") or "", r["name"], str(r["kills"]), str(r["deaths"]),
                streak, f"{r['acc']}%" if r.get("acc") is not None else "-", f"{r['best_shot']} ft" if r.get("best_shot") else "-"]
        for (name, fx, anc), v in zip(cols, vals):
            c = METAL_COLORS.get(r.get("prestige") or 0, TEXT) if name == "RANK" and not r.get("ghost") else col
            cv.create_text(int(fx * (W - 2 * pad)) + pad, y, text=v[:22], font=f(12, name in ("NAME", "K")), fill=c, anchor="n" + anc)
        y += int(26 * s)
    y += pad
    cv.coords("bg", 0, 0, W, y)
    return y


def _card(cv, board, W, y, s, f, pad):
    sm = board.get("summary") or {}
    if sm.get("mvp"):
        cv.create_text(W // 2, y + int(4 * s), text="MVP", font=f(12), fill=DIM, anchor="n")
        cv.create_text(W // 2, y + int(22 * s), text=sm["mvp"], font=f(28), fill=ME, anchor="n")
        cv.create_text(W // 2, y + int(64 * s), text=sm.get("mvp_line", ""), font=f(11, False), fill=TEXT, anchor="n")
        y += int(96 * s)
    awards = []
    if sm.get("most_kills"):
        awards.append(("MOST KILLS", sm["most_kills"][0], f"{sm['most_kills'][1]}"))
    if sm.get("longest_streak"):
        awards.append(("LONGEST STREAK", sm["longest_streak"][0], f"{sm['longest_streak'][1]} in a row"))
    if sm.get("best_shot"):
        awards.append(("BEST SHOT", sm["best_shot"][0], f"{sm['best_shot'][1]} ft"))
    if awards:
        w = (W - 2 * pad) / len(awards)
        for i, (k, who, what) in enumerate(awards):
            cx = int(pad + w * i + w / 2)
            cv.create_text(cx, y, text=k, font=f(10), fill=DIM, anchor="n")
            cv.create_text(cx, y + int(18 * s), text=who[:18], font=f(15), fill=TEXT, anchor="n")
            cv.create_text(cx, y + int(42 * s), text=what, font=f(11, False), fill=DIM, anchor="n")
        y += int(72 * s)
    return y
