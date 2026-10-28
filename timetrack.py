#!/usr/bin/env python3
"""
timetrack – Multi-project terminal time tracking
Norton Commander style TUI – ↑↓ Navigate  n New  s/p/r/e Actions  q Quit
Created by Patrick B. Stang — dev_circus | www.dev-circus.com
"""

import csv
import os
import select
import signal
import subprocess
import sys
import termios
import time
import tty
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional, List

try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table
    from rich.layout import Layout
    from rich.live import Live
    from rich import box
    from rich.markup import escape
except ImportError:
    print("pip install rich")
    sys.exit(1)

# ── Constants ──────────────────────────────────────────────────────────────────

CONFIG_DIR  = Path.home() / "timetrack"
CONFIG_DIR.mkdir(exist_ok=True)
LOG_FILE    = CONFIG_DIR / "log.csv"
CSV_HEADER  = ["date", "project", "repo", "branch", "start", "end", "duration_min", "note"]

console     = Console()
STATE_ICONS = {"idle": "○", "running": "●", "paused": "⏸"}

# ── Color palette ───────────────────────────────────────────────────────────────
BG  = "#0200ae"   # background
SEL = "#60fffe"   # selected row
RUN = "#e8ea5f"   # running / accent
PAU = "yellow"    # paused

# ── CSV ─────────────────────────────────────────────────────────────────────────

def csv_ensure():
    if not LOG_FILE.exists():
        with open(LOG_FILE, "w", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow(CSV_HEADER)

def csv_write(project, repo, branch, start_dt, end_dt, note="") -> float:
    csv_ensure()
    dur = round((end_dt - start_dt).total_seconds() / 60, 2)
    with open(LOG_FILE, "a", newline="", encoding="utf-8") as f:
        csv.writer(f).writerow([
            start_dt.strftime("%Y-%m-%d"), project, repo or "", branch or "",
            start_dt.strftime("%H:%M:%S"), end_dt.strftime("%H:%M:%S"), dur, note
        ])
    return dur

def csv_read(n: int = 40) -> list:
    csv_ensure()
    try:
        with open(LOG_FILE, "r", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))[-n:]
        return [
            {"date": r["date"], "project_name": r["project"],
             "duration_min": r["duration_min"], "branch": r.get("branch", "")}
            for r in reversed(rows)
        ]
    except Exception:
        return []

# ── Git detection ───────────────────────────────────────────────────────────────

def detect_git(path="."):
    try:
        root = subprocess.check_output(
            ["git", "rev-parse", "--show-toplevel"],
            stderr=subprocess.DEVNULL, cwd=path
        ).decode().strip()
        branch = subprocess.check_output(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            stderr=subprocess.DEVNULL, cwd=path
        ).decode().strip()
        return Path(root).name, branch
    except Exception:
        return None, None

# ── Utils ───────────────────────────────────────────────────────────────────────

def fmt(seconds: float) -> str:
    h, r = divmod(int(max(0, seconds)), 3600)
    m, s = divmod(r, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"

# ── Data model ──────────────────────────────────────────────────────────────────

@dataclass
class ProjectSession:
    name: str
    repo: Optional[str]    = None
    branch: Optional[str]  = None
    note: str              = ""
    state: str             = "idle"
    session_start: Optional[float]    = None
    start_dt: Optional[datetime]      = None
    pause_start: Optional[float]      = None
    paused_total: float    = 0.0

    def elapsed(self) -> float:
        if self.state == "running" and self.session_start is not None:
            return time.time() - self.session_start
        if self.state == "paused" and self.pause_start is not None:
            return self.pause_start - self.session_start
        return 0.0

    def active_time(self) -> float:
        return max(0.0, self.elapsed() - self.paused_total)


@dataclass
class AppState:
    projects: List[ProjectSession] = field(default_factory=list)
    selected_idx: int  = 0
    message: Optional[str] = None
    msg_style: str     = "green"
    quit_confirm: bool = False

    @property
    def selected(self) -> Optional[ProjectSession]:
        if self.projects and 0 <= self.selected_idx < len(self.projects):
            return self.projects[self.selected_idx]
        return None

    @property
    def active(self) -> Optional[ProjectSession]:
        for p in self.projects:
            if p.state == "running":
                return p
        return None

# ── Keyboard input ──────────────────────────────────────────────────────────────

def get_key(timeout=0.5) -> Optional[str]:
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        ready, _, _ = select.select([fd], [], [], timeout)
        if not ready:
            return None
        ch = os.read(fd, 1)
        if ch in (b'\r', b'\n'):
            return 'enter'
        if ch == b'\x1b':
            r2, _, _ = select.select([fd], [], [], 0.05)
            if not r2:
                return 'esc'
            ch2 = os.read(fd, 1)
            if ch2 == b'[':
                seq = b''
                for _ in range(8):
                    r3, _, _ = select.select([fd], [], [], 0.05)
                    if not r3:
                        break
                    c = os.read(fd, 1)
                    seq += c
                    if c.isalpha() or c == b'~':
                        break
                s = seq.decode('utf-8', errors='ignore')
                return {
                    'A': 'up', 'B': 'down', 'C': 'right', 'D': 'left',
                }.get(s, 'esc')
            return 'esc'
        return ch.decode('utf-8', errors='ignore').lower()
    except Exception:
        return None
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)

# ── UI builders ─────────────────────────────────────────────────────────────────

def _field(label: str, value: str, vstyle: str = f"bright_white on {BG}") -> str:
    return f"[bold white on {BG}]{label:<10}[/]  [{vstyle}]{escape(str(value))}[/]"


def build_projects_panel(app: AppState) -> Panel:
    lines = []
    for i, p in enumerate(app.projects):
        icon  = STATE_ICONS.get(p.state, "○")
        timer = fmt(p.elapsed()) if p.state != "idle" else ""
        name  = p.name[:20]

        if i == app.selected_idx:
            line = f"[bold black on {SEL}] {icon} {name:<20} {timer:<9} [/]"
        elif p.state == "running":
            line = f"[bold {RUN} on {BG}] {icon} {name:<20} {timer:<9}[/]"
        elif p.state == "paused":
            line = f"[{PAU} on {BG}] {icon} {name:<20} {timer:<9}[/]"
        else:
            line = f"[dim white on {BG}] {icon} {name:<20} {'':9}[/]"
        lines.append(line)

    content = "\n".join(lines) if lines else f"[dim on {BG}]  No projects. Press n.[/]"
    return Panel(
        content,
        title=f"[bold bright_white on {BG}] PROJECTS [/]",
        subtitle=f"[dim on {BG}] ↑↓ navigate · n new [/]",
        border_style=f"bright_white on {BG}",
        style=f"on {BG}",
        padding=(0, 1),
    )


def build_session_panel(p: Optional[ProjectSession]) -> Panel:
    if p is None:
        return Panel(
            f"[dim on {BG}]  No project selected.[/]",
            title=f"[bold bright_white on {BG}] SESSION [/]",
            border_style=f"white on {BG}",
            style=f"on {BG}",
            padding=(1, 2),
        )

    icon = STATE_ICONS.get(p.state, "○")
    state_cfg = {
        "idle":    (f"white on {BG}",         f"{icon}  IDLE"),
        "running": (f"bold {RUN} on {BG}",    f"{icon}  RUNNING"),
        "paused":  (f"bold {PAU} on {BG}",    f"{icon}  PAUSED"),
    }
    s_style, s_label = state_cfg.get(p.state, (f"white on {BG}", p.state.upper()))
    sep = f"[bright_white on {BG}]" + "─" * 60 + "[/]"

    lines = [sep, f"[{s_style}]{s_label}[/]", sep, "", _field("Project", p.name)]
    if p.repo:
        lines.append(
            f"[bold white on {BG}]{'Repo':<10}[/]  "
            f"[bright_white on {BG}]{escape(p.repo)}[/]"
            + (f"  [white on {BG}]({escape(p.branch)})[/]" if p.branch else "")
        )
    if p.note:
        lines.append(_field("Note", p.note, f"white on {BG}"))

    lines.append("")
    if p.state != "idle":
        lines.append(_field(
            "Active", fmt(p.active_time()),
            f"bold {RUN} on {BG}" if p.state == "running" else f"bright_white on {BG}"
        ))
        if p.paused_total > 0:
            lines.append(_field("Paused", fmt(p.paused_total), f"{PAU} on {BG}"))
        lines.append(_field("Total", fmt(p.elapsed())))
    lines.append("")

    return Panel(
        "\n".join(lines),
        title=f"[bold bright_white on {BG}] SESSION · {escape(p.name)} [/]",
        subtitle=f"[dim on {BG}] {datetime.now().strftime('%H:%M:%S')} [/]",
        border_style=f"bright_white on {BG}",
        style=f"on {BG}",
        padding=(1, 2),
    )


def build_log_panel(entries: list, highlight: Optional[str] = None) -> Panel:
    t = Table(
        box=box.SIMPLE_HEAD, border_style=f"white on {BG}",
        header_style=f"bold white on {BG}", style=f"on {BG}",
        show_edge=False, expand=True,
    )
    t.add_column("Date",    style=f"white on {BG}",        no_wrap=True)
    t.add_column("Project", style=f"bright_white on {BG}", overflow="fold")
    t.add_column("Branch",  style=f"dim white on {BG}",    overflow="fold")
    t.add_column("Min",     style=f"{RUN} on {BG}",        justify="right", no_wrap=True)

    for r in entries:
        pname = r.get("project_name", "")
        t.add_row(
            r.get("date", ""),
            pname,
            r.get("branch", "") or "",
            str(round(float(r.get("duration_min", 0) or 0), 1)),
            style="bold" if (highlight and pname == highlight) else "",
        )

    return Panel(
        t if entries else f"[dim on {BG}]  No sessions yet.[/]",
        title=f"[bold bright_white on {BG}] LOG [/]",
        subtitle=f"[dim on {BG}] {len(entries)} entries [/]" if entries else "",
        border_style=f"white on {BG}",
        style=f"on {BG}",
        padding=(0, 1),
    )


def build_fkey_bar(app: AppState) -> Panel:
    sel = app.selected

    def fk(key: str, label: str, on: bool) -> str:
        if on:
            return f"[bold black on {SEL}] {key} [/][bold white on {BG}] {label} [/]"
        return f"[dim on {BG}] {key}  {label} [/]"

    parts = [
        fk("n", "New",    True),
        fk("s", "Start",  sel is not None and sel.state == "idle"),
        fk("p", "Pause",  sel is not None and sel.state == "running"),
        fk("r", "Resume", sel is not None and sel.state == "paused"),
        fk("e", "End",    sel is not None and sel.state in ("running", "paused")),
        fk("l", "Log",    True),
        fk("q", "Quit",   True),
    ]

    bar = "  ".join(parts)
    if app.message:
        mstyle = RUN if app.msg_style == "green" else PAU
        bar += f"   [{mstyle} on {BG}] {escape(app.message)} [/]"

    return Panel(
        bar,
        subtitle=f"[dim on {BG}] ↑↓ navigate · n s p r e l q [/]",
        style=f"on {BG}", border_style=f"white on {BG}", padding=(0, 1),
    )


def build_layout(app: AppState, entries: list) -> Layout:
    sel = app.selected
    hl  = sel.name if sel else None

    right = Layout()
    right.split_column(
        Layout(build_session_panel(sel), ratio=42),
        Layout(build_log_panel(entries, hl), ratio=58),
    )

    main = Layout()
    main.split_row(
        Layout(build_projects_panel(app), ratio=30),
        Layout(right, ratio=70),
    )

    outer = Layout()
    outer.split_column(
        Layout(main, name="main"),
        Layout(build_fkey_bar(app), name="fkeys", size=3),
    )
    return outer

# ── Actions ─────────────────────────────────────────────────────────────────────

def _pause_internal(p: ProjectSession):
    if p.state != "running":
        return
    p.pause_start = time.time()
    p.state = "paused"


def do_start(app: AppState):
    sel = app.selected
    if not sel or sel.state != "idle":
        return
    active = app.active
    if active and active is not sel:
        _pause_internal(active)
        app.message   = f"Auto-paused: {active.name}"
        app.msg_style = "yellow"
    sel.session_start = time.time()
    sel.start_dt      = datetime.now()
    sel.paused_total  = 0.0
    sel.state         = "running"


def do_pause(app: AppState):
    sel = app.selected
    if not sel or sel.state != "running":
        return
    _pause_internal(sel)


def do_resume(app: AppState):
    sel = app.selected
    if not sel or sel.state != "paused":
        return
    active = app.active
    if active and active is not sel:
        _pause_internal(active)
    sel.paused_total += time.time() - sel.pause_start
    sel.pause_start   = None
    sel.state         = "running"


def do_end(app: AppState) -> bool:
    sel = app.selected
    if not sel or sel.state not in ("running", "paused"):
        return False
    if sel.state == "paused":
        sel.paused_total += time.time() - sel.pause_start
    end_dt   = datetime.now()
    total    = (end_dt - sel.start_dt).total_seconds()
    dur_min  = total / 60
    act_min  = max(0.0, dur_min - sel.paused_total / 60)
    csv_write(sel.name, sel.repo, sel.branch, sel.start_dt, end_dt, sel.note)
    sel.state         = "idle"
    sel.session_start = None
    sel.start_dt      = None
    sel.pause_start   = None
    sel.paused_total  = 0.0
    app.message   = f"Saved · {round(act_min, 1)} min active"
    app.msg_style = "green"
    return True

# ── Dialogs ─────────────────────────────────────────────────────────────────────

def dialog_add_project(live: Live, app: AppState) -> Optional[ProjectSession]:
    live.stop()
    console.clear()
    console.print(Panel(
        "", title="[bold bright_white on blue] ADD PROJECT [/]",
        border_style=f"bright_white on {BG}", style=f"on {BG}", padding=(0, 2),
    ))

    auto_repo, auto_branch = detect_git()
    repo, branch = None, None

    if auto_repo:
        console.print(f"\n  [bright_green]Git:[/] [cyan]{auto_repo}[/] [dim]({auto_branch})[/]")
        ans = console.input("  Use Git repo? [Y/n]: ").strip().lower()
        if ans in ("", "y"):
            repo, branch = auto_repo, auto_branch
        else:
            repo   = console.input("  Repo name (optional): ").strip() or None
            branch = (console.input("  Branch (optional): ").strip() or None) if repo else None
    else:
        console.print("\n  [dim]No Git repo found.[/]")
        repo   = console.input("  Repo name (optional): ").strip() or None
        branch = (console.input("  Branch (optional): ").strip() or None) if repo else None

    default = repo or "project"
    name = console.input(f"  Project name [{default}]: ").strip() or default
    note = console.input("  Note (optional): ").strip()

    result = None
    if any(p.name == name for p in app.projects):
        app.message   = f"'{name}' already exists"
        app.msg_style = "yellow"
    else:
        result = ProjectSession(name=name, repo=repo, branch=branch, note=note)

    live.start()
    return result


def dialog_manage_log(live: Live):
    live.stop()
    rows = []
    if LOG_FILE.exists():
        with open(LOG_FILE, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            rows = list(reader)

    idx = 0

    def render():
        console.clear()
        t = Table(
            box=box.SIMPLE_HEAD, border_style=f"white on {BG}",
            header_style=f"bold white on {BG}", style=f"on {BG}",
            show_edge=False, expand=True,
        )
        t.add_column("#",       style=f"dim white on {BG}",        no_wrap=True, width=4)
        t.add_column("Date",    style=f"white on {BG}",             no_wrap=True)
        t.add_column("Project", style=f"bright_white on {BG}",      overflow="fold")
        t.add_column("Branch",  style=f"dim white on {BG}",         overflow="fold")
        t.add_column("Min",     style=f"{RUN} on {BG}",             justify="right", no_wrap=True)
        t.add_column("Note",    style=f"dim white on {BG}",         overflow="fold")

        for i, r in enumerate(rows):
            style = f"bold black on {SEL}" if i == idx else ""
            t.add_row(
                str(i + 1),
                r.get("date", ""),
                r.get("project", ""),
                r.get("branch", ""),
                str(round(float(r.get("duration_min", 0) or 0), 1)),
                r.get("note", ""),
                style=style,
            )

        console.print(Panel(
            t if rows else f"[dim on {BG}]  No entries.[/]",
            title=f"[bold bright_white on {BG}] LOG MANAGER [/]",
            subtitle=f"[dim on {BG}] ↑↓ navigate · d delete · q back [/]",
            border_style=f"bright_white on {BG}", style=f"on {BG}", padding=(0, 1),
        ))

    while True:
        render()
        key = get_key(5.0)
        if key == 'up':
            idx = max(0, idx - 1)
        elif key == 'down':
            idx = min(len(rows) - 1, idx + 1) if rows else 0
        elif key == 'd' and rows:
            r = rows[idx]
            render()
            console.print(
                f"\n  [bold red]Delete entry: {r.get('date')}  {r.get('project')}  "
                f"{r.get('duration_min')} min[/]\n"
                f"  [red]Are you sure? [d] confirm · any other key cancel[/]"
            )
            confirm = get_key(10.0)
            if confirm == 'd':
                rows.pop(idx)
                if rows:
                    idx = min(idx, len(rows) - 1)
                csv_ensure()
                with open(LOG_FILE, "w", newline="", encoding="utf-8") as f:
                    w = csv.DictWriter(f, fieldnames=CSV_HEADER)
                    w.writeheader()
                    w.writerows(rows)
        elif key in ('q', 'esc'):
            break

    live.start()

# ── Main ────────────────────────────────────────────────────────────────────────

def main():
    signal.signal(signal.SIGINT, lambda *_: None)

    app = AppState()
    entries: list   = csv_read()
    refresh_counter = 0
    REFRESH_EVERY   = 10   # × 0.5 s = 5 s

    live = Live(screen=True, auto_refresh=False, console=console)
    live.start()

    if not app.projects:
        p = dialog_add_project(live, app)
        if p:
            app.projects.append(p)

    try:
        while True:
            refresh_counter += 1
            if refresh_counter >= REFRESH_EVERY:
                refresh_counter = 0
                entries = csv_read()

            live.update(build_layout(app, entries), refresh=True)
            key = get_key(0.5)

            if key is None:
                continue

            if key != 'q':
                app.quit_confirm = False
            app.message = None

            if key == 'up':
                app.selected_idx = max(0, app.selected_idx - 1)

            elif key == 'down':
                app.selected_idx = min(len(app.projects) - 1, app.selected_idx + 1)

            elif key == 'n':
                p = dialog_add_project(live, app)
                if p:
                    app.projects.append(p)
                    app.selected_idx = len(app.projects) - 1
                    refresh_counter = REFRESH_EVERY

            elif key in ('s', 'enter'):
                do_start(app)

            elif key == 'p':
                do_pause(app)

            elif key == 'r':
                do_resume(app)

            elif key == 'e':
                if do_end(app):
                    refresh_counter = REFRESH_EVERY

            elif key == 'l':
                dialog_manage_log(live)
                entries = csv_read()

            elif key == 'q':
                running = [p for p in app.projects if p.state in ("running", "paused")]
                if running and not app.quit_confirm:
                    names = ", ".join(p.name for p in running)
                    app.message   = f"Active: {names} — press Q again to quit"
                    app.msg_style = "yellow"
                    app.quit_confirm = True
                else:
                    break

    finally:
        live.stop()


if __name__ == "__main__":
    main()
