# Outlaws Companion

Join (or host) Outlaws Remastered LAN games over ZeroTier. Press **Join**: the app finds the host, downloads only the mods
that game needs (zombie skins, Dark Mode map items, custom maps), starts the game and puts the game folder back when it closes.

**Download:** [companion/OutlawsCompanion.exe](companion/OutlawsCompanion.exe) — that one file is all a friend needs.

## Updates
Push a change to `companion.py` on `main`. GitHub Actions (`.github/workflows/build.yml`) rebuilds
`companion/OutlawsCompanion.exe` on Windows, raises `VERSION` if you didn't, and writes `companion/version.txt`.
Everyone's **Update Launcher** button then downloads the new build, checks its SHA-256 and restarts into it
(the button reads *Update available* at start when there is one). Running from source instead, the button pulls the new
`companion.py` and, if `build_companion.cmd` sits next to it, rebuilds the exe locally.

## Building by hand
`build_companion.cmd` (Windows, Python 3 with tkinter).

## Hosting toolkit
`toolkit/toolkit.zip` is the bot toolkit a friend's companion installs when they press **Set up hosting** or
**Refresh hosting toolkit** (the bots, map data, zombie skins, zombie death sounds and the Horde's sounds and music).
`toolkit/version.txt` is `<timestamp> <sha256> <size>`; companions with an older timestamp are told to refresh.
