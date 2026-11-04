"""timetrack CLI – Subcommands auf dem gemeinsamen Core. Ohne Argument startet die TUI.

© 2026 dev_circus · www.dev-circus.com · by Patrick B. Stang
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional

from rich import box
from rich.console import Console
from rich.markup import escape as esc
from rich.table import Table

from . import __version__, core
from .core import TimetrackError

LEGACY_LOGS = [Path.home() / "timetrack" / "log.csv"]


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="timetrack", description="Projektbezogene Zeiterfassung.")
    p.add_argument("--version", action="version", version=f"timetrack {__version__}")
    sub = p.add_subparsers(dest="cmd", metavar="<befehl>")

    def add(name: str, help: str, project: bool = True) -> argparse.ArgumentParser:
        sp = sub.add_parser(name, help=help)
        if project:
            sp.add_argument("-p", "--project", help="Projektname (Standard: Git-Repo im aktuellen Ordner)")
        return sp

    add("ui", "Norton-Commander-TUI öffnen (Standard)", project=False)
    s = add("start", "Zeiterfassung starten")
    s.add_argument("-n", "--note", default="")
    add("pause", "Pausieren")
    add("resume", "Fortsetzen")
    s = add("stop", "Beenden und ins Log schreiben")
    s.add_argument("-n", "--note")
    s.add_argument("--at", help="nachträgliches Ende: HH:MM oder 'YYYY-MM-DD HH:MM'")
    add("status", "Laufende Sessions anzeigen")
    s = add("log", "Letzte Log-Einträge")
    s.add_argument("-l", "--limit", type=int, default=15)
    s.add_argument("-a", "--all", action="store_true", help="alle Projekte")
    s = add("summary", "Aktivzeit je Projekt")
    s.add_argument("period", nargs="?", default="week", choices=["today", "week", "month", "all"])
    add("projects", "Projekte und offene Sessions", project=False)
    s = add("migrate", "Alte Logs ins aktuelle Log mergen", project=False)
    s.add_argument("--from", dest="sources", action="append", type=Path,
                   help=f"Quell-CSV (mehrfach; Standard: {LEGACY_LOGS[0]})")
    s.add_argument("--dry-run", action="store_true")
    return p


def _out(sessions: list[dict], console: Console) -> None:
    for s in sessions:
        icon = {"running": "[yellow]►[/]", "paused": "[magenta]‖[/]"}[s["state"]]
        console.print(f"{icon} [bold]{esc(s['project'])}[/]  {core.fmt_hms(s['active_sec'])} aktiv"
                      f"  [dim]({s['state']}, seit {s['started'][11:]}, Pause {core.fmt_hms(s['paused_sec'])})[/]"
                      + (f"  [dim]{esc(s['note'])}[/]" if s["note"] else ""))


def _log_table(rows: list[dict]) -> Table:
    t = Table(box=box.SIMPLE_HEAD, header_style="bold cyan")
    for c in ("Datum", "Projekt", "Zeit", "Aktiv", "Pause", "Notiz"):
        t.add_column(c)
    for r in rows:
        t.add_row(r["date"], esc(r["project"]), f"{r['start'][:5]}–{r['end'][:5]}", core.fmt_min(r["duration_min"]),
                  "–" if r["paused_min"] is None else core.fmt_min(r["paused_min"]), esc(r["note"]))
    return t


def run(args: argparse.Namespace, console: Console) -> None:
    cmd = args.cmd or "ui"
    if cmd == "ui":
        from . import tui
        return tui.run()
    if cmd == "start":
        info = core.resolve_project(args.project, None)
        r = core.start(info.name, info=info, note=args.note)
        console.print(f"[yellow]►[/] '{esc(info.name)}' gestartet." + (f" Pausiert: {esc(', '.join(r['auto_paused']))}." if r["auto_paused"] else ""))
    elif cmd in ("pause", "resume"):
        name = core.resolve_project(args.project).name
        r = getattr(core, cmd)(name)
        console.print(f"'{esc(name)}': {r['state']}." + (f" Pausiert: {esc(', '.join(r['auto_paused']))}." if r.get("auto_paused") else ""))
    elif cmd == "stop":
        name = core.resolve_project(args.project).name
        e = core.stop(name, note=args.note, at=args.at)
        console.print(f"[green]■[/] '{esc(name)}': {core.fmt_min(e['duration_min'])} aktiv, "
                      f"{core.fmt_min(e['paused_min'])} Pause ({e['start']}–{e['end']})")
    elif cmd == "status":
        snap = core.snapshot()["sessions"]
        _out(snap, console) if snap else console.print("[dim]Keine offene Session.[/]")
    elif cmd == "log":
        name = None if args.all else core.resolve_project(args.project).name
        rows = core.read_log(name, limit=args.limit)
        console.print(_log_table(rows) if rows else "[dim]Keine Einträge.[/]")
    elif cmd == "summary":
        s = core.summary(args.period)
        t = Table(box=box.SIMPLE_HEAD, header_style="bold cyan", title=f"{args.period} (ab {s['from'] or '–'})")
        for c in ("Projekt", "Zeit", "Sessions"):
            t.add_column(c)
        for b in s["by_project"]:
            t.add_row(esc(b["project"]), core.fmt_min(b["minutes"]), str(b["sessions"]))
        t.add_row("[bold]Gesamt[/]", f"[bold]{core.fmt_min(s['total_min'])}[/]", "")
        console.print(t)
    elif cmd == "projects":
        snap = core.snapshot()
        console.print(esc(", ".join(p["name"] for p in snap["projects"])) or "[dim]Keine Projekte.[/]")
        _out(snap["sessions"], console)
    elif cmd == "migrate":
        sources = args.sources or LEGACY_LOGS
        r = core.migrate(sources, dry_run=args.dry_run)
        verb = "würde hinzufügen" if args.dry_run else "hinzugefügt"
        console.print(f"{r['added']} Einträge {verb}, Log hat {r['total']} Einträge.")
        if r["backup"]:
            console.print(f"[dim]Backup: {r['backup']}[/]")


def main(argv: Optional[list[str]] = None) -> int:
    args = _parser().parse_args(argv)
    try:
        run(args, Console())
    except TimetrackError as e:
        Console(stderr=True).print(f"[red]Fehler:[/] {e}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
