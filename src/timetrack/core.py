"""timetrack core – State, Log und Projekt-Erkennung.

Alle Frontends (CLI, TUI, MCP-Server) laufen über dieses Modul.
State liegt in state.json (flock + atomarer Write), das Log in log.csv.

© 2026 >devcircus_ · www.dev-circus.com · by Patrick B. Stang
"""

from __future__ import annotations

import csv
import fcntl
import json
import os
import subprocess
import tempfile
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterator, Optional

CSV_HEADER = ["date", "project", "repo", "branch", "start", "end", "duration_min", "note", "paused_min"]
DEDUPE_KEYS = ["date", "project", "repo", "branch", "start", "end", "duration_min", "note"]
STATE_VERSION = 1


class TimetrackError(Exception):
    """Erwarteter Fehler mit lesbarer Meldung."""


# ── Pfade ─────────────────────────────────────────────────────────────────────

def home() -> Path:
    p = Path(os.environ.get("TIMETRACK_HOME") or Path.home() / ".timetrack")
    p.mkdir(parents=True, exist_ok=True)
    return p


def log_file() -> Path:
    return home() / "log.csv"


def state_file() -> Path:
    return home() / "state.json"


# ── Projekt-Erkennung ─────────────────────────────────────────────────────────

@dataclass
class ProjectInfo:
    name: str
    repo: Optional[str] = None
    branch: Optional[str] = None
    path: Optional[str] = None


def _git(args: list[str], cwd: Path) -> Optional[str]:
    try:
        return subprocess.check_output(
            ["git", *args], cwd=cwd, stderr=subprocess.DEVNULL, text=True, timeout=5
        ).strip()
    except (OSError, subprocess.SubprocessError):
        return None


def detect_project(path: Optional[str] = None) -> ProjectInfo:
    """Projekt aus Git-Repo ableiten, sonst aus dem Ordnernamen."""
    cwd = Path(path or os.getcwd()).expanduser()
    root = _git(["rev-parse", "--show-toplevel"], cwd)
    if root:
        repo = Path(root).name
        branch = _git(["symbolic-ref", "--short", "-q", "HEAD"], cwd) or _git(["rev-parse", "--abbrev-ref", "HEAD"], cwd)
        return ProjectInfo(repo, repo, branch, root)
    return ProjectInfo(cwd.name or str(cwd), None, None, str(cwd))


def is_generic_dir(info: ProjectInfo) -> bool:
    """Home und / ergeben keinen brauchbaren Projektnamen."""
    return info.repo is None and Path(info.path or "/") in (Path.home(), Path("/"))


def resolve_project(project: Optional[str] = None, cwd: Optional[str] = None) -> ProjectInfo:
    """Projekt für CLI/MCP: explizit benannt, sonst Git-Repo, sonst Ordnername."""
    info = detect_project(cwd)
    if project:
        info.name = project
    elif is_generic_dir(info):
        raise TimetrackError(
            "Kein Projekt erkannt: Das Arbeitsverzeichnis ist Home oder /. Frag den User nach einem "
            "Projektnamen und übergib ihn als `project` (CLI: -p NAME). Das Projekt entsteht beim ersten start."
        )
    return info


# ── Session-Modell ────────────────────────────────────────────────────────────

@dataclass
class Session:
    project: str
    started: float
    repo: Optional[str] = None
    branch: Optional[str] = None
    note: str = ""
    pauses: list = field(default_factory=list)  # [[von, bis|None], ...] als Epoch

    @property
    def paused(self) -> bool:
        return bool(self.pauses) and self.pauses[-1][1] is None

    @property
    def state(self) -> str:
        return "paused" if self.paused else "running"

    def wall(self, t: float) -> float:
        return max(0.0, t - self.started)

    def paused_seconds(self, t: float) -> float:
        total = 0.0
        for von, bis in self.pauses:
            lo = min(max(von, self.started), t)
            hi = min(bis if bis is not None else t, t)
            total += max(0.0, hi - lo)
        return total

    def active(self, t: float) -> float:
        return max(0.0, self.wall(t) - self.paused_seconds(t))


# ── State-Datei ───────────────────────────────────────────────────────────────

def _atomic_write(path: Path, text: str) -> None:
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _load_state() -> dict:
    f = state_file()
    if not f.exists():
        return {"version": STATE_VERSION, "projects": {}, "sessions": {}}
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise TimetrackError(f"state.json unlesbar ({e}). Datei prüfen: {f}") from e
    data.setdefault("projects", {})
    data.setdefault("sessions", {})
    return data


@contextmanager
def _locked() -> Iterator[dict]:
    """Exklusiver Zugriff; der State wird nur bei Erfolg zurückgeschrieben."""
    with open(home() / ".lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        state = _load_state()
        yield state
        _atomic_write(state_file(), json.dumps(state, indent=2, ensure_ascii=False))


def _session(st: dict, project: str) -> Session:
    if project not in st["sessions"]:
        raise TimetrackError(f"Keine aktive Session für '{project}'.")
    return Session(**st["sessions"][project])


def _put(st: dict, s: Session) -> None:
    st["sessions"][s.project] = asdict(s)


def _pause_others(st: dict, now: float, keep: str) -> list[str]:
    paused = []
    for name in list(st["sessions"]):
        if name == keep:
            continue
        s = _session(st, name)
        if not s.paused:
            s.pauses.append([now, None])
            _put(st, s)
            paused.append(name)
    return paused


# ── Aktionen ──────────────────────────────────────────────────────────────────

def iso(ts: float) -> str:
    return datetime.fromtimestamp(ts).isoformat(timespec="seconds")


def add_project(info: ProjectInfo) -> None:
    with _locked() as st:
        st["projects"][info.name] = {"repo": info.repo, "branch": info.branch, "path": info.path}


def remove_project(name: str) -> None:
    with _locked() as st:
        if name in st["sessions"]:
            raise TimetrackError(f"'{name}' hat eine aktive Session.")
        st["projects"].pop(name, None)


def start(project: str, *, info: Optional[ProjectInfo] = None, note: str = "",
          now: Optional[float] = None) -> dict:
    now = now or time.time()
    with _locked() as st:
        if project in st["sessions"]:
            raise TimetrackError(
                f"'{project}' hat schon eine Session ({_session(st, project).state})."
            )
        if info is not None:
            st["projects"][project] = {"repo": info.repo, "branch": info.branch, "path": info.path}
        elif project not in st["projects"]:
            raise TimetrackError(f"Projekt '{project}' ist nicht angelegt.")
        meta = st["projects"][project]
        auto_paused = _pause_others(st, now, keep=project)
        _put(st, Session(project, now, meta.get("repo"), meta.get("branch"), note))
    return {"project": project, "state": "running", "started": iso(now), "auto_paused": auto_paused}


def pause(project: str, *, now: Optional[float] = None) -> dict:
    now = now or time.time()
    with _locked() as st:
        s = _session(st, project)
        if s.paused:
            raise TimetrackError(f"'{project}' ist schon pausiert.")
        s.pauses.append([now, None])
        _put(st, s)
    return {"project": project, "state": "paused", "active_sec": round(s.active(now))}


def resume(project: str, *, now: Optional[float] = None) -> dict:
    now = now or time.time()
    with _locked() as st:
        s = _session(st, project)
        if not s.paused:
            raise TimetrackError(f"'{project}' läuft bereits.")
        s.pauses[-1][1] = now
        _put(st, s)
        auto_paused = _pause_others(st, now, keep=project)
    return {"project": project, "state": "running", "auto_paused": auto_paused}


def parse_at(text: str, now: float) -> float:
    """'HH:MM[:SS]' (heute, sonst gestern) oder 'YYYY-MM-DD HH:MM[:SS]' als Epoch."""
    text = text.strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(text, fmt).timestamp()
        except ValueError:
            pass
    for fmt in ("%H:%M:%S", "%H:%M"):
        try:
            t = datetime.strptime(text, fmt).time()
        except ValueError:
            continue
        dt = datetime.combine(datetime.fromtimestamp(now).date(), t)
        if dt.timestamp() > now:
            dt -= timedelta(days=1)
        return dt.timestamp()
    raise TimetrackError(f"Zeitangabe '{text}' nicht lesbar (HH:MM oder YYYY-MM-DD HH:MM).")


def stop(project: str, *, note: Optional[str] = None, at: Optional[str] = None,
         now: Optional[float] = None) -> dict:
    """Session beenden und ins Log schreiben. `at` setzt das Ende nachträglich."""
    now = now or time.time()
    with _locked() as st:
        s = _session(st, project)
        end = parse_at(at, now) if at else now
        if end < s.started:
            raise TimetrackError(f"Ende {iso(end)} liegt vor dem Start {iso(s.started)}.")
        if end > now + 1:
            raise TimetrackError("Ende liegt in der Zukunft.")
        wall, paused, active = s.wall(end), s.paused_seconds(end), s.active(end)
        start_dt, end_dt = datetime.fromtimestamp(s.started), datetime.fromtimestamp(end)
        entry = {
            "date": start_dt.strftime("%Y-%m-%d"),
            "project": project,
            "repo": s.repo or "",
            "branch": s.branch or "",
            "start": start_dt.strftime("%H:%M:%S"),
            "end": end_dt.strftime("%H:%M:%S"),
            "duration_min": round(active / 60, 2),
            "note": s.note if note is None else note,
            "paused_min": round(paused / 60, 2),
        }
        _append_log(entry)
        del st["sessions"][project]
    return {**entry, "wall_min": round(wall / 60, 2)}


def snapshot(now: Optional[float] = None) -> dict:
    """Lesender Blick auf den State, ohne Lock."""
    now = now or time.time()
    st = _load_state()
    sessions = []
    for name in st["sessions"]:
        s = Session(**st["sessions"][name])
        sessions.append({
            "project": name, "repo": s.repo, "branch": s.branch, "note": s.note,
            "state": s.state, "started": iso(s.started),
            "active_sec": round(s.active(now)), "paused_sec": round(s.paused_seconds(now)),
            "wall_sec": round(s.wall(now)),
        })
    projects = [{"name": n, **meta} for n, meta in st["projects"].items()]
    return {"sessions": sessions, "projects": projects}


# ── Log ───────────────────────────────────────────────────────────────────────

def _read_raw(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with open(path, newline="", encoding="utf-8") as f:
        return [{k: (r.get(k) or "") for k in CSV_HEADER} for r in csv.DictReader(f)]


def _write_rows(path: Path, rows: list[dict]) -> None:
    from io import StringIO
    buf = StringIO(newline="")
    w = csv.DictWriter(buf, fieldnames=CSV_HEADER, extrasaction="ignore", lineterminator="\r\n")
    w.writeheader()
    w.writerows(rows)
    _atomic_write(path, buf.getvalue())


def _append_log(entry: dict) -> None:
    f = log_file()
    if f.exists() and f.stat().st_size > 0:
        with open(f, newline="", encoding="utf-8") as fh:
            header = next(csv.reader(fh), [])
        if header != CSV_HEADER:
            _write_rows(f, _read_raw(f))
    else:
        _write_rows(f, [])
    with open(f, "a", newline="", encoding="utf-8") as fh:
        csv.DictWriter(fh, fieldnames=CSV_HEADER, extrasaction="ignore", lineterminator="\r\n").writerow(entry)


def read_log(project: Optional[str] = None, limit: Optional[int] = None) -> list[dict]:
    """Einträge chronologisch. `index` verweist auf die Zeile in der Datei (für delete_log_entry)."""
    rows = []
    for i, r in enumerate(_read_raw(log_file())):
        try:
            datetime.strptime(r["date"], "%Y-%m-%d")
        except ValueError:
            continue
        if not r["project"]:
            continue
        try:
            dur = float(r["duration_min"] or 0)
        except ValueError:
            dur = 0.0
        try:
            paused = float(r["paused_min"]) if r["paused_min"] else None
        except ValueError:
            paused = None
        rows.append({**r, "index": i, "duration_min": dur, "paused_min": paused})
    if project:
        rows = [r for r in rows if r["project"] == project]
    return rows[-limit:] if limit else rows


def delete_log_entry(index: int, expect: dict) -> None:
    """Zeile löschen; `expect` (date/project/start) schützt vor verschobenen Indizes."""
    with _locked():
        rows = _read_raw(log_file())
        if not 0 <= index < len(rows) or any(rows[index][k] != str(expect[k]) for k in ("date", "project", "start")):
            raise TimetrackError("Log hat sich geändert. Neu laden.")
        del rows[index]
        _write_rows(log_file(), rows)


def summary(period: str = "week", project: Optional[str] = None, today: Optional[datetime] = None) -> dict:
    today = today or datetime.now()
    d = today.date()
    if period == "today":
        lo = d
    elif period == "week":
        lo = d - timedelta(days=d.weekday())
    elif period == "month":
        lo = d.replace(day=1)
    elif period == "all":
        lo = None
    else:
        raise TimetrackError("period muss today, week, month oder all sein.")
    by: dict[str, dict] = {}
    for r in read_log(project):
        if lo and datetime.strptime(r["date"], "%Y-%m-%d").date() < lo:
            continue
        b = by.setdefault(r["project"], {"project": r["project"], "minutes": 0.0, "sessions": 0})
        b["minutes"] = round(b["minutes"] + r["duration_min"], 2)
        b["sessions"] += 1
    items = sorted(by.values(), key=lambda b: -b["minutes"])
    return {
        "period": period,
        "from": lo.isoformat() if lo else None,
        "total_min": round(sum(b["minutes"] for b in items), 2),
        "by_project": items,
    }


def migrate(sources: list[Path], *, dry_run: bool = False) -> dict:
    """Alte Logs ins aktuelle Log mergen (dedupliziert, chronologisch). Quellen bleiben unberührt."""
    target = log_file()
    rows = _read_raw(target)
    seen = {tuple(r[k] for k in DEDUPE_KEYS) for r in rows}
    added = 0
    for src in sources:
        if not src.exists() or src.resolve() == target.resolve():
            continue
        for r in _read_raw(src):
            key = tuple(r[k] for k in DEDUPE_KEYS)
            if key not in seen:
                seen.add(key)
                rows.append(r)
                added += 1
    rows.sort(key=lambda r: (r["date"], r["start"]))
    backup = None
    if not dry_run and (added or (target.exists() and _header_of(target) != CSV_HEADER)):
        if target.exists():
            backup = target.with_name(f"log.csv.bak-{datetime.now():%Y%m%d-%H%M%S}")
            backup.write_bytes(target.read_bytes())
        with _locked():
            _write_rows(target, rows)
    return {"added": added, "total": len(rows), "backup": str(backup) if backup else None}


def _header_of(path: Path) -> list[str]:
    with open(path, newline="", encoding="utf-8") as f:
        return next(csv.reader(f), [])


# ── Format ────────────────────────────────────────────────────────────────────

def fmt_hms(seconds: float) -> str:
    h, r = divmod(int(max(0, seconds)), 3600)
    m, s = divmod(r, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def fmt_min(minutes: float) -> str:
    h, m = divmod(int(round(minutes)), 60)
    return f"{h}h {m:02d}m" if h else f"{m}m"
