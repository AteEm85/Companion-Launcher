"""The Horde's game files (zombie skins .SPX, zombie death sounds .WAV) as ONE archive the game loads itself.
Loose files next to the game don't win on every PC; an archive named like the remaster's own HD archives
(olremaster<N>.lab) does. The first free number is used (olremaster3.lab where olremaster1 and 2 exist).
Only ever touches an archive it made (each one ends with MARK). Standard library only: the companion and the bot
toolkit (zombieskins.py) both use it."""
import os, struct

MARK = b"OUTLAWS-COMPANION-MODS"
OLD_MARKS = (b"OUTLAWS-COMPANION-TEST",)              # lab_test.py's test archives count as ours too
TYPES = {".spx": b"XRPS", ".wav": b"DVAW"}           # the game's own type codes (olremaster2.lab, olsfx.lab)
PREFIX = "olremaster"


def ours(path):
    try:
        with open(path, "rb") as f:
            for m in (MARK,) + OLD_MARKS:
                f.seek(-len(m), 2)
                if f.read() == m: return True
        return False
    except OSError:
        return False


def _names(game):
    try: return {n.lower(): n for n in os.listdir(game)}
    except OSError: return {}


def find_ours(game):
    """Our archives in the game folder (left by a session that was cut short, or the current one)."""
    out = []
    for low, n in _names(game).items():
        if low.startswith(PREFIX) and low.endswith(".lab") and low[len(PREFIX):-4].isdigit():
            p = os.path.join(game, n)
            if ours(p): out.append(p)
    return out


def free_path(game):
    """olremaster<N>.lab with the first number from 2 up that isn't taken (an archive of ours counts as free)."""
    names = _names(game)
    for i in range(2, 10):
        n = "%s%d.lab" % (PREFIX, i)
        if n not in names: return os.path.join(game, n)
        p = os.path.join(game, names[n])
        if ours(p): return p
    raise RuntimeError("no free olremaster archive number (2-9) in " + game)


def build(game, files):
    """Write the archive with these files ([(name in the archive, local path)]). Returns its path."""
    for p in find_ours(game):                          # one at a time: replace an old one of ours
        try: os.remove(p)
        except OSError: pass
    path = free_path(game)
    files = [(n, p) for n, p in files if os.path.splitext(n)[1].lower() in TYPES]
    if not files: raise RuntimeError("nothing to put in the archive")
    names = b"".join(n.encode("latin-1") + b"\0" for n, _p in files)
    sizes = [os.path.getsize(p) for _n, p in files]
    off = 16 + 16 * len(files) + len(names)
    table, name_off = [], 0
    for (n, _p), size in zip(files, sizes):
        table.append(struct.pack("<III4s", name_off, off, size, TYPES[os.path.splitext(n)[1].lower()]))
        name_off += len(n.encode("latin-1")) + 1
        off += size
    tmp = path + ".tmp"
    with open(tmp, "wb") as out:
        out.write(struct.pack("<4sIII", b"LABN", 0x10000, len(files), len(names)))
        out.write(b"".join(table)); out.write(names)
        for _n, p in files:
            with open(p, "rb") as f:
                while True:
                    b = f.read(1 << 20)
                    if not b: break
                    out.write(b)
        out.write(MARK)
    os.replace(tmp, path)
    return path


def remove(game):
    """Take our archive(s) out. True when none is left (False: still in use, e.g. the game is open)."""
    ok = True
    for p in find_ours(game):
        try: os.remove(p)
        except OSError: ok = False
    return ok


def contents(path):
    """[(name, size)] of an archive (for checks)."""
    with open(path, "rb") as f:
        magic, _v, n, ns = struct.unpack("<4sIII", f.read(16))
        ents = [struct.unpack("<III4s", f.read(16)) for _ in range(n)]
        names = f.read(ns)
    return [(names[a:names.find(b"\0", a)].decode("latin-1"), s) for a, _o, s, _t in ents]
