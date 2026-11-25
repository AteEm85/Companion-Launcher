"""Bot reports: after each game, keep a copy of every bot's console log (botlogs\\) in botreports\\<date>_<time>_<map>\\,
with a summary.txt that counts what went wrong (give-ups, doors, stuck spots, deaths, errors). For testing: send the
newest folder, or just summary.txt, to Claude. Delete the botreports folder (and the one call in launcher.py) any time.
"""
import datetime
import re
import shutil
import time
from pathlib import Path

KEEP = 25                                   # games kept; older ones are deleted
COUNTS = [
    ("kills", r"^killed 0x"), ("deaths", r"^died$"),
    ("gave up on an item", r"^giving up on "), ("door presses", r"^door in the way"),
    ("door give-ups", r"the door won't open|can't get through"), ("stuck-turnarounds", r"stuck here again"),
    ("double-door second half", r"other half of the double door"), ("saloon back-offs", r"saloon door: backing away"),
    ("window problems", r"the window won't break"), ("map not recognised", r"map not recognised"),
    ("fled for water/health", r"running for the water"), ("lingered in water", r"in the water for \d+s"),
    ("well: nothing left", r"nothing to get"), ("cellar escapes", r"tombstone|cellar"),
    ("errors", r"Traceback|Error|Exception|failed"),
]
SHOW = re.compile(r"giving up|can't get through|stuck here|map not recognised|other half|running for the water|"
                  r"in the water for|nothing to get|tombstone|cellar|Traceback|Error|Exception|failed|map:", re.I)


def _signature(folder):
    return tuple(sorted((f.name, f.stat().st_mtime_ns, f.stat().st_size) for f in folder.glob("*.log")))


def archive(botlogs, reports, launcher_lines=None, last_sig=None):
    """Copy the logs into a new report folder. Returns (folder or None, signature): None when there is nothing new
    since `last_sig` (so repeated cleanup calls don't make duplicates)."""
    botlogs, reports = Path(botlogs), Path(reports)
    if not botlogs.is_dir():
        return None, last_sig
    try:
        sig = _signature(botlogs)
    except OSError:
        return None, last_sig
    if not sig or sig == last_sig:
        return None, last_sig
    texts = {}
    for f in sorted(botlogs.glob("*.log")):
        try:
            texts[f.stem] = [ln for ln in f.read_text(encoding="utf-8", errors="replace").splitlines()
                             if not ln.startswith("persona:")]
        except OSError:
            continue
    if not any(len(t) > 5 for t in texts.values()):
        return None, sig                    # nothing played (a game that never started)
    level = next((m.group(1) for t in texts.values() for ln in t
                  for m in [re.match(r"map: (\S+)", ln)] if m and m.group(1) != "sancop"), "map")
    stamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    out = reports / f"{stamp}_{level}"
    out.mkdir(parents=True, exist_ok=True)
    summary = [f"Bot report {stamp}  ({level})", "=" * 40, ""]
    for name, lines in texts.items():
        (out / f"{name}.log").write_text("\n".join(lines) + "\n", encoding="utf-8")
        summary.append(f"--- {name} ({len(lines)} lines) ---")
        for label, pat in COUNTS:
            n = sum(1 for ln in lines if re.search(pat, ln))
            if n:
                summary.append(f"  {label}: {n}")
        shown = [ln for ln in lines if SHOW.search(ln)][:40]
        if shown:
            summary.append("  notable lines:")
            summary += ["    " + ln for ln in shown]
        summary.append("")
    if launcher_lines:
        (out / "launcher.txt").write_text("\n".join(launcher_lines) + "\n", encoding="utf-8")
        summary.append("(launcher.txt has the launcher's log for this session)")
    (out / "summary.txt").write_text("\n".join(summary) + "\n", encoding="utf-8")
    old = sorted(p for p in reports.iterdir() if p.is_dir())
    for p in old[:-KEEP]:
        shutil.rmtree(p, ignore_errors=True)
    return out, sig
