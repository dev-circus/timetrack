"""timetrack MCP-Server – projektbezogene Zeiterfassung für Claude.

Das Projekt wird aus dem Git-Repo des Arbeitsverzeichnisses erkannt (cwd des Servers,
überschreibbar per `cwd`/`project`). State und Log teilt sich der Server mit CLI und TUI.

© 2026 dev_circus · www.dev-circus.com · by Patrick B. Stang
"""

from __future__ import annotations

from functools import wraps
from typing import Optional

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from . import core
from .core import ProjectInfo, TimetrackError

INSTRUCTIONS = (
    "Projektbezogene Zeiterfassung. Das Projekt ergibt sich aus dem Git-Repo des "
    "Arbeitsverzeichnisses: übergib bei jedem Tool-Aufruf das Arbeitsverzeichnis der aktuellen "
    "Session als `cwd`. Zeit nur auf ausdrücklichen Wunsch des Users starten, pausieren, "
    "fortsetzen oder stoppen, niemals eigenmächtig. Bei Unsicherheit zuerst `status` aufrufen."
)

mcp = MCPServer("timetrack", instructions=INSTRUCTIONS)

READ = ToolAnnotations(readOnlyHint=True)
WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=False)


def tool(annotations: ToolAnnotations):
    """Tool registrieren. Erwartete Fehler gehen als ToolError raus, sonst verschluckt das SDK den Text."""
    def register(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            try:
                return fn(*args, **kwargs)
            except TimetrackError as e:
                raise ToolError(str(e)) from e
        return mcp.tool(annotations=annotations)(wrapper)
    return register


def _info(project: Optional[str], cwd: Optional[str]) -> ProjectInfo:
    return core.resolve_project(project, cwd)


def _name(project: Optional[str], cwd: Optional[str]) -> str:
    return _info(project, cwd).name


def _view(s: dict) -> dict:
    return {
        "project": s["project"], "state": s["state"], "branch": s["branch"], "note": s["note"],
        "started": s["started"], "active": core.fmt_hms(s["active_sec"]),
        "paused": core.fmt_hms(s["paused_sec"]), "total": core.fmt_hms(s["wall_sec"]),
    }


@tool(READ)
def status(project: Optional[str] = None, cwd: Optional[str] = None) -> dict:
    """Zeigt die Session des aktuellen Projekts (aus dem Git-Repo) und alle anderen offenen Sessions."""
    name = _name(project, cwd)
    sessions = core.snapshot()["sessions"]
    return {
        "project": name,
        "session": next((_view(s) for s in sessions if s["project"] == name), None),
        "others": [_view(s) for s in sessions if s["project"] != name],
    }


@tool(WRITE)
def start(note: str = "", project: Optional[str] = None, cwd: Optional[str] = None) -> dict:
    """Startet die Zeiterfassung für das aktuelle Projekt. Ein anderes laufendes Projekt wird automatisch pausiert."""
    info = _info(project, cwd)
    r = core.start(info.name, info=info, note=note)
    msg = f"Zeiterfassung für '{info.name}' gestartet."
    if r["auto_paused"]:
        msg += f" Pausiert: {', '.join(r['auto_paused'])}."
    return {**r, "message": msg}


@tool(WRITE)
def pause(project: Optional[str] = None, cwd: Optional[str] = None) -> dict:
    """Pausiert die laufende Session des aktuellen Projekts."""
    name = _name(project, cwd)
    r = core.pause(name)
    return {**r, "message": f"'{name}' pausiert bei {core.fmt_hms(r['active_sec'])} aktiv."}


@tool(WRITE)
def resume(project: Optional[str] = None, cwd: Optional[str] = None) -> dict:
    """Setzt die pausierte Session fort. Ein anderes laufendes Projekt wird automatisch pausiert."""
    name = _name(project, cwd)
    r = core.resume(name)
    msg = f"'{name}' läuft weiter."
    if r["auto_paused"]:
        msg += f" Pausiert: {', '.join(r['auto_paused'])}."
    return {**r, "message": msg}


@tool(WRITE)
def stop(note: Optional[str] = None, at: Optional[str] = None,
         project: Optional[str] = None, cwd: Optional[str] = None) -> dict:
    """Beendet die Session und schreibt sie ins Log (Aktivzeit ohne Pausen).
    `note`: was gemacht wurde. `at`: nachträgliches Ende als 'HH:MM' (heute) oder 'YYYY-MM-DD HH:MM'."""
    name = _name(project, cwd)
    e = core.stop(name, note=note, at=at)
    msg = (f"'{name}' gestoppt: {core.fmt_min(e['duration_min'])} aktiv, "
           f"{core.fmt_min(e['paused_min'])} Pause ({e['start']}–{e['end']}).")
    return {**e, "message": msg}


@tool(READ)
def log(limit: int = 10, project: Optional[str] = None, cwd: Optional[str] = None,
        all_projects: bool = False) -> dict:
    """Letzte Log-Einträge, standardmäßig nur vom aktuellen Projekt."""
    name = None if all_projects else _name(project, cwd)
    rows = core.read_log(name, limit=limit)
    entries = [{k: r[k] for k in ("date", "project", "start", "end", "duration_min", "paused_min", "note")}
               for r in rows]
    return {"project": name, "entries": entries}


@tool(READ)
def summary(period: str = "week", project: Optional[str] = None, cwd: Optional[str] = None,
            all_projects: bool = True) -> dict:
    """Summe der erfassten Aktivzeit je Projekt. period: today, week (ab Montag), month oder all."""
    name = None if all_projects else _name(project, cwd)
    s = core.summary(period, name)
    return {**s, "total": core.fmt_min(s["total_min"]),
            "by_project": [{**b, "time": core.fmt_min(b["minutes"])} for b in s["by_project"]]}


@tool(READ)
def projects() -> dict:
    """Alle angelegten Projekte und offene Sessions."""
    snap = core.snapshot()
    return {"projects": [p["name"] for p in snap["projects"]],
            "sessions": [_view(s) for s in snap["sessions"]]}


def main() -> None:
    mcp.run("stdio")


if __name__ == "__main__":
    main()
