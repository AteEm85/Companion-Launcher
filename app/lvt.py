"""Read Outlaws LVT level files (text sector geometry) from the game's .lab archives.

An LVT lists SECTORs. Each has FLOOR Y / CEILING Y (plus optional SLOPEDFLOOR / SLOPEDCEILING), a VERTICES
list (X, Z) and WALLs between vertex pairs. A wall's ADJOIN is the sector on the other side (-1 = solid);
DADJOIN is a second adjoin for stacked sectors. Y is up, and coordinates match the in-game actor positions.
"""
import re
import struct
from dataclasses import dataclass, field
from pathlib import Path

GAME_DIR = Path(__file__).resolve().parent.parent
# Later archives override earlier ones. Loose files from mods are passed in separately.
LABS = ["olgeo.lab", "olpatch1.lab", "olpatch2.lab", "olpatch3.lab", "olpatch4.lab", "olpatch5.lab", "Olpatch6.lab",
        "TheDip1s.lab",       # custom map: The Dip! by Twobitt
        "Stone.lab"]          # custom map: Stone by Templar


def _custom_labs():
    """Custom maps: every X.rcm in the game folder with an X.lab beside it (and not already in LABS)."""
    have = {n.lower() for n in LABS}
    out = []
    try:
        for rcm in sorted(GAME_DIR.glob("*.rcm")):
            lab = rcm.with_suffix(".lab").name
            if (GAME_DIR / lab).exists() and lab.lower() not in have:
                out.append(lab)
                have.add(lab.lower())
    except OSError:
        pass
    return out


CUSTOM_LABS = _custom_labs()


@dataclass
class Wall:
    v1: int
    v2: int
    adjoin: int
    mirror: int
    dadjoin: int
    flags: int


@dataclass
class Sector:
    index: int
    name: str
    floor: float
    ceiling: float
    flags: int
    layer: int
    vertices: list = field(default_factory=list)     # [(x, z)]
    walls: list = field(default_factory=list)
    floor_slope: tuple = None                         # (sector, wall, angle) as written in the file
    ceiling_slope: tuple = None
    vadjoin: int = -1

    def contains(self, x, z):
        """Point-in-polygon over all walls (handles sectors with holes, since walls form closed loops)."""
        inside = False
        for w in self.walls:
            x1, z1 = self.vertices[w.v1]
            x2, z2 = self.vertices[w.v2]
            if (z1 > z) != (z2 > z):
                xc = x1 + (z - z1) * (x2 - x1) / (z2 - z1)
                if xc > x:
                    inside = not inside
        return inside


def lab_directory(path):
    """{lowercase name: (offset, size)} without reading the whole archive."""
    with open(path, "rb") as f:
        magic, _ver, n, names_size = struct.unpack("<4sIII", f.read(16))
        if magic != b"LABN":
            raise ValueError(f"{path}: not a LABN archive")
        entries = [struct.unpack("<III4s", f.read(16)) for _ in range(n)]
        names = f.read(names_size)
    out = {}
    for name_off, off, size, _typ in entries:
        end = names.find(b"\0", name_off)
        out[names[name_off:end].decode("latin-1").lower()] = (off, size)
    return out


def read_from_labs(name, labs=LABS):
    """The newest copy of `name` across the archives, or None. Custom maps' own archives (CUSTOM_LABS) are looked
    in too: a file named after the archive (Stone.lvt in Stone.lab) overrides, any other only fills a gap."""
    data = read_from_standard(name, labs)
    stem = name.lower().rsplit(".", 1)[0]
    for lab in CUSTOM_LABS if labs is LABS else ():
        if data is not None and Path(lab).stem.lower() != stem:
            continue
        path = GAME_DIR / lab
        try:
            entry = lab_directory(path).get(name.lower())
        except (OSError, ValueError):
            continue
        if entry:
            with open(path, "rb") as f:
                f.seek(entry[0])
                data = f.read(entry[1])
            if Path(lab).stem.lower() == stem:
                break
    if data is None:
        # A loose file in the game folder (Duel.lvt, Duel.inf ...).
        try:
            for p in GAME_DIR.iterdir():
                if p.name.lower() == name.lower() and p.is_file():
                    return p.read_bytes()
        except OSError:
            pass
    return data


def read_from_standard(name, labs=LABS):
    data = None
    for lab in labs:
        path = GAME_DIR / lab
        if not path.exists():
            continue
        entry = lab_directory(path).get(name.lower())
        if entry:
            with open(path, "rb") as f:
                f.seek(entry[0])
                data = f.read(entry[1])
    return data


def list_levels(labs=LABS):
    names = set()
    for lab in list(labs) + (CUSTOM_LABS if labs is LABS else []):
        path = GAME_DIR / lab
        if path.exists():
            names |= {n[:-4] for n in lab_directory(path) if n.endswith((".lvt", ".lvb"))}
    if labs is LABS:
        try:
            names |= {p.stem for p in GAME_DIR.iterdir() if p.suffix.lower() in (".lvt", ".lvb")}   # loose levels
        except OSError:
            pass
    return sorted(names)


_num = r"(-?[\d.]+)"
WALL_RE = re.compile(r"WALL:.*?V1:\s*(\d+)\s+V2:\s*(\d+).*?ADJOIN:\s*(-?\d+)\s+MIRROR:\s*(-?\d+)\s+DADJOIN:\s*(-?\d+).*?FLAGS:\s*(-?\d+)")


def parse(text):
    sectors = []
    cur = None
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("SECTOR "):
            cur = Sector(index=len(sectors), name="", floor=0.0, ceiling=0.0, flags=0, layer=0)
            sectors.append(cur)
        elif cur is None:
            continue
        elif line.startswith("NAME"):
            cur.name = line[4:].strip()
        elif line.startswith("FLOOR Y"):
            cur.floor = float(line.split()[2])
        elif line.startswith("CEILING Y"):
            cur.ceiling = float(line.split()[2])
        elif line.startswith("FLAGS") and not line.startswith("FLAGS:"):
            cur.flags = int(line.split()[1])
        elif line.startswith("LAYER"):
            cur.layer = int(line.split()[1])
        elif line.startswith("VADJOIN"):
            cur.vadjoin = int(line.split()[1])
        elif line.startswith("SLOPEDFLOOR"):
            p = line.split()
            cur.floor_slope = (int(p[1]), int(p[2]), float(p[3]))
        elif line.startswith("SLOPEDCEILING"):
            p = line.split()
            cur.ceiling_slope = (int(p[1]), int(p[2]), float(p[3]))
        elif line.startswith("X:"):
            p = line.split()
            cur.vertices.append((float(p[1]), float(p[3])))
        elif line.startswith("WALL:"):
            m = WALL_RE.search(line)
            if m:
                cur.walls.append(Wall(*(int(g) for g in m.groups())))
    return sectors


# -- compiled levels (.LVB) --------------------------------------------------------------------------------
# Bunkers (lilbatl) and the winter train (htrain) ship only as a compiled .lvb. It holds the same lines as the
# text .lvt as a stream of records: a tag byte, a big-endian field count, then the fields (4-byte little-endian
# ints or floats, or NUL-terminated strings). Worked out against the 34 levels that ship both forms.
_LVB_STR = {1, 4, 6, 7, 0x0D, 0x10, 0x19}           # level name, palettes, cmaps, music, textures, sector NAME, floor sound
_T_SECTOR, _T_NAME, _T_VADJOIN, _T_FLOORY, _T_CEILY, _T_FLAGS = 0x0F, 0x10, 0x18, 0x1A, 0x1B, 0x20
_T_SLOPEF, _T_SLOPEC, _T_LAYER, _T_VERT, _T_WALL = 0x21, 0x22, 0x23, 0x25, 0x27


def parse_binary(d):
    """Sectors of a compiled .lvb, the same objects `parse` gives for the text form."""
    if d[4:8] != b".LVT":
        raise ValueError("not a compiled LVT")
    pos = 21
    sectors, cur = [], None
    i32, f32 = struct.Struct("<i"), struct.Struct("<f")
    while pos < len(d):
        tag = d[pos]
        n = struct.unpack(">I", d[pos + 1:pos + 5])[0]
        pos += 5
        if tag in _LVB_STR or tag == 0x0B:
            vals = []
            for k in range(n):
                if tag == 0x0B and k < 5:
                    pos += 4
                    continue
                e = d.index(b"\0", pos)
                vals.append(d[pos:e].decode("latin-1"))
                pos = e + 1
            if tag == _T_NAME and cur is not None:
                cur.name = vals[0] if vals else ""
            continue
        raw = d[pos:pos + 4 * n]
        pos += 4 * n
        if tag == _T_SECTOR:
            cur = Sector(index=len(sectors), name="", floor=0.0, ceiling=0.0, flags=0, layer=0)
            sectors.append(cur)
        elif cur is None:
            continue
        elif tag == _T_VADJOIN:
            cur.vadjoin = i32.unpack_from(raw, 0)[0]
        elif tag == _T_FLOORY:
            cur.floor = f32.unpack_from(raw, 0)[0]
        elif tag == _T_CEILY:
            cur.ceiling = f32.unpack_from(raw, 0)[0]
        elif tag == _T_FLAGS:
            cur.flags = i32.unpack_from(raw, 0)[0]
        elif tag == _T_LAYER:
            cur.layer = int(round(f32.unpack_from(raw, 0)[0]))        # stored as a float
        elif tag in (_T_SLOPEF, _T_SLOPEC):
            a, b, ang = struct.unpack_from("<IIf", raw, 0)            # (the text form prints -1 as 4294967295 too)
            if tag == _T_SLOPEF:
                cur.floor_slope = (a, b, ang)
            else:
                cur.ceiling_slope = (a, b, ang)
        elif tag == _T_VERT:
            cur.vertices.append(struct.unpack_from("<ff", raw, 0))
        elif tag == _T_WALL:
            v = struct.unpack("<" + "i" * n, raw)
            cur.walls.append(Wall(v[1], v[2], v[15], v[16], v[17], v[19]))
    return sectors


@dataclass
class InfElevator:
    """One CLASS: ELEVATOR block of a sector item in a level's .INF (doors, lifts, moving walls)."""
    sector: str                       # sector NAME it drives (several sectors can share a name)
    kind: str                         # MORPH_SPIN1, MOVE_CEILING, DOOR, ...
    stops: list = field(default_factory=list)     # numeric STOP values (relative "@" stops skipped)
    event_mask: int = None            # None when the block doesn't say
    key: bool = False                 # needs a key
    nudge_line: bool = False          # walls of the same name carry a trigger that moves it when nudged (ranch's
                                      # basement doors: the door's own EVENT_MASK is 0, its LINE triggers send
                                      # NEXT_STOP on a nudge, from one side; the other side says it's locked)


TRIGGER_NUDGE = 16 | 32               # trigger EVENT_MASK bits: nudged from the front / back
DOOR_MESSAGES = {None, "NEXT_STOP", "PREV_STOP", "GOTO_STOP"}


def parse_inf(text):
    """The ELEVATOR blocks of an INF file. Only what navigation needs is kept."""
    out, item, cur = [], None, None
    trig = None                       # [item name, clients, event mask, messages] of the trigger being read
    triggers = []
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip() if not raw.strip().startswith("ITEM:") else raw.strip()
        up = line.upper()
        if up.startswith("ITEM:"):
            m = re.search(r"ITEM:\s*(\w+)\s+NAME:\s*(\S+)", line, re.I)
            item = (m.group(1).upper(), m.group(2).upper()) if m else None
            cur = trig = None
        elif up.startswith("CLASS:"):
            p = up.split()
            cur = trig = None
            if item and item[0] == "SECTOR" and len(p) >= 3 and p[1] == "ELEVATOR":
                cur = InfElevator(item[1], p[2])
                out.append(cur)
            elif item and len(p) >= 2 and p[1] == "TRIGGER":
                trig = [item[1], set(), None, set()]
                triggers.append(trig)
        elif up.startswith("SEQEND"):
            cur = trig = None
        elif trig is not None:
            parts = up.split()
            if up.startswith("CLIENT:") and len(parts) > 1:
                trig[1].add(parts[1])
            elif up.startswith("EVENT_MASK:") and len(parts) > 1 and parts[1].lstrip("-").isdigit():
                trig[2] = int(parts[1])
            elif up.startswith("MESSAGE:") and len(parts) > 1:
                trig[3].add(parts[1])
        elif cur is None:
            continue
        elif up.startswith("STOP:"):
            v = line.split()[1]
            try:
                cur.stops.append(float(v))
            except ValueError:
                pass
        elif up.startswith("EVENT_MASK:"):
            try:
                cur.event_mask = int(line.split()[1])
            except (IndexError, ValueError):
                pass
        elif up.startswith("KEY:"):
            cur.key = True
    # Triggers on the door's own walls (same name) that move it when nudged. A switch elsewhere doesn't count:
    # pressing E at the door wouldn't work.
    nudged = {name for name, clients, mask, msgs in triggers
              if name in clients and mask is not None and mask & TRIGGER_NUDGE
              and (not msgs or msgs & (DOOR_MESSAGES - {None}))}
    for e in out:
        e.nudge_line = e.sector in nudged
    return out


def load_inf(level):
    data = read_from_labs(level + ".inf")
    return parse_inf(data.decode("latin-1")) if data else []


def load(level, extra_files=()):
    """Parse a level by name. `extra_files` are loose .lvt paths (mods) that override the archives."""
    for path in extra_files:
        if Path(path).stem.lower() == level.lower():
            return parse(Path(path).read_text(encoding="latin-1"))
    for ext in (".lvt", ".lvb"):                         # a loose copy in the game folder wins, as in the game itself
        try:                                             # (the Christmas Horde writes the frozen-pool map there: ghosts
            p = next((q for q in GAME_DIR.iterdir()      #  reading the archive's copy thought the ice was water)
                      if q.is_file() and q.name.lower() == (level + ext).lower()), None)
        except OSError:
            p = None
        if p is not None:
            raw = p.read_bytes()
            return parse(raw.decode("latin-1")) if ext == ".lvt" else parse_binary(raw)
    data = read_from_labs(level + ".lvt")
    if data is None:
        data = read_from_labs(level + ".lvb")
        if data is None:
            raise FileNotFoundError(f"{level}.lvt not in any archive")
        return parse_binary(data)
    return parse(data.decode("latin-1"))
