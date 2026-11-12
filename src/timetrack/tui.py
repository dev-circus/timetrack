"""timetrack TUI – Norton-Commander-Optik auf dem gemeinsamen Core.

Die TUI hält keinen eigenen State: Sessions liegen in state.json und sind live sichtbar,
auch wenn sie von CLI oder Claude (MCP) gestartet wurden. Beenden der TUI stoppt nichts.

© 2026 >devcircus_ · www.dev-circus.com · by Patrick B. Stang
"""

from __future__ import annotations

import codecs
import os
import select
import sys
import termios
import tty
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from itertools import groupby
from typing import Callable, Optional

from rich import box
from rich.align import Align
from rich.console import Console, Group
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from . import __version__, core
from .core import ProjectInfo, TimetrackError

# ── Palette (Norton Commander) ────────────────────────────────────────────────
BG, SEL, RUN, PAU = "#0200ae", "#60fffe", "#e8ea5f", "#ff9bff"
TXT, DIM, ERR = "#ffffff", "#9d9dff", "#aa0000"

ICONS = {"idle": "○", "running": "►", "paused": "‖"}
BIG_MIN_H = 16  # Session-Panel ab dieser Höhe mit großen Ziffern

GLYPHS = {
    "0": ("███", "█ █", "█ █", "█ █", "███"), "1": ("  █", "  █", "  █", "  █", "  █"),
    "2": ("███", "  █", "███", "█  ", "███"), "3": ("███", "  █", "███", "  █", "███"),
    "4": ("█ █", "█ █", "███", "  █", "  █"), "5": ("███", "█  ", "███", "  █", "███"),
    "6": ("███", "█  ", "███", "█ █", "███"), "7": ("███", "  █", "  █", "  █", "  █"),
    "8": ("███", "█ █", "███", "█ █", "███"), "9": ("███", "█ █", "███", "  █", "███"),
    ":": (" ", "█", " ", "█", " "),
}


def st(fg: str, bold: bool = False, bg: str = BG) -> str:
    return f"{'bold ' if bold else ''}{fg} on {bg}"


def big(text: str) -> list[str]:
    """Große Ziffern, jedes Pixel 2 Zeichen breit (Zellen sind ca. 1:2 hoch)."""
    rows = [""] * 5
    for ch in text:
        for i, part in enumerate(GLYPHS[ch]):
            rows[i] += "".join(c * 2 for c in part) + "  "
    return [r.rstrip() for r in rows]


def big_text(text: str, color: str) -> list[Text]:
    """Gefüllte Pixel als Leerzeichen mit Hintergrundfarbe: solide, unabhängig vom Zeilenabstand."""
    return [Text.assemble(*[(" " * len(list(run)), f"on {color}" if c == "█" else st(color)) for c, run in groupby(row)])
            for row in big(text)]


# ── Tastatur ──────────────────────────────────────────────────────────────────

_CSI = {"A": "up", "B": "down", "C": "right", "D": "left", "H": "home", "F": "end"}
_TILDE = {"5~": "pgup", "6~": "pgdn", "1~": "home", "4~": "end"}


def parse_keys(text: str) -> list[str]:
    keys, i = [], 0
    while i < len(text):
        c = text[i]
        if c == "\x1b":
            if i + 1 < len(text) and text[i + 1] in "[O":
                j = i + 2
                while j < len(text) and not ("@" <= text[j] <= "~"):
                    j += 1
                seq = text[i + 2:j + 1]
                key = _TILDE.get(seq) or _CSI.get(seq[-1:])
                if key:
                    keys.append(key)
                i = j + 1
                continue
            keys.append("esc")
        elif c in "\r\n":
            keys.append("enter")
        elif c in "\x7f\x08":
            keys.append("backspace")
        elif c.isprintable():
            keys.append(c)
        i += 1
    return keys


class Keys:
    """cbreak wird einmal gesetzt (nicht pro Poll), Eingaben gehen so nicht verloren."""

    def __init__(self, fd: int):
        self.fd = fd
        self.decoder = codecs.getincrementaldecoder("utf-8")(errors="ignore")

    def read(self, timeout: float) -> list[str]:
        ready, _, _ = select.select([self.fd], [], [], timeout)
        if not ready:
            return []
        return parse_keys(self.decoder.decode(os.read(self.fd, 64)))


# ── App-State ─────────────────────────────────────────────────────────────────

@dataclass
class Dialog:
    kind: str                       # "input" | "confirm"
    title: str
    label: str
    on_ok: Callable[[str], None]
    value: str = ""


@dataclass
class App:
    snap: dict = field(default_factory=lambda: {"sessions": [], "projects": []})
    entries: list = field(default_factory=list)
    log_mtime: int = -1
    sel: int = 0
    mode: str = "main"              # "main" | "log"
    log_idx: int = 0
    dialog: Optional[Dialog] = None
    message: str = ""
    error: bool = False
    quit: bool = False
    detected: Optional[ProjectInfo] = None

    def names(self) -> list[str]:
        return [p["name"] for p in self.snap["projects"]]

    def selected(self) -> Optional[str]:
        names = self.names()
        return names[self.sel] if 0 <= self.sel < len(names) else None

    def session(self, name: Optional[str]) -> Optional[dict]:
        return next((s for s in self.snap["sessions"] if s["project"] == name), None)


def say(app: App, text: str, error: bool = False) -> None:
    app.message, app.error = text, error


def refresh(app: App) -> None:
    try:
        app.snap = core.snapshot()
        f = core.log_file()
        mtime = f.stat().st_mtime_ns if f.exists() else 0
        if mtime != app.log_mtime:
            app.entries, app.log_mtime = core.read_log(), mtime
    except (TimetrackError, OSError, ValueError) as e:
        say(app, str(e), True)
    app.sel = max(0, min(app.sel, len(app.names()) - 1))
    app.log_idx = max(0, min(app.log_idx, len(app.entries) - 1))


def act(app: App, fn: Callable, *args, **kw):
    """Core-Aktion ausführen. Erfolg: Ergebnis (oder True), Fehler: Meldung setzen und None."""
    try:
        result = fn(*args, **kw)
    except TimetrackError as e:
        say(app, str(e), True)
        return None
    refresh(app)
    return True if result is None else result


# ── Eingabe-Logik ─────────────────────────────────────────────────────────────

def _start(app: App, name: str) -> None:
    r = act(app, core.start, name)
    if r:
        say(app, f"Started {name}" + (f" · paused {', '.join(r['auto_paused'])}" if r["auto_paused"] else ""))


def _resume(app: App, name: str) -> None:
    r = act(app, core.resume, name)
    if r:
        say(app, f"Resumed {name}" + (f" · paused {', '.join(r['auto_paused'])}" if r["auto_paused"] else ""))


def _pause(app: App, name: str) -> None:
    if act(app, core.pause, name):
        say(app, f"Paused {name}")


def _end(app: App, name: str) -> None:
    s = app.session(name)

    def done(note: str) -> None:
        e = act(app, core.stop, name, note=note.strip())
        if e:
            say(app, f"Saved · {core.fmt_min(e['duration_min'])} active")

    app.dialog = Dialog("input", "END SESSION", f"Note for {name} (optional)", done, s["note"] if s else "")


def _new_project(app: App) -> None:
    def done(value: str) -> None:
        name = value.strip()
        if not name:
            return
        if name in app.names():
            say(app, f"'{name}' exists", True)
            return
        d = app.detected
        if act(app, core.add_project, d if d and d.name == name else ProjectInfo(name)):
            app.sel = app.names().index(name)
            say(app, f"Added {name}")

    app.dialog = Dialog("input", "NEW PROJECT", "Name", done, app.detected.name if app.detected else "")


def _remove_project(app: App, name: str) -> None:
    if app.session(name):
        say(app, f"'{name}' has a session — end it first", True)
        return

    def done(_: str) -> None:
        if act(app, core.remove_project, name):
            say(app, f"Removed {name}")

    app.dialog = Dialog("confirm", "REMOVE PROJECT", f"Remove '{name}' from the list? (log stays)", done)


def _delete_entry(app: App) -> None:
    if not app.entries:
        return
    e = app.entries[app.log_idx]

    def done(_: str) -> None:
        if act(app, core.delete_log_entry, e["index"], e):
            say(app, "Entry deleted")

    app.dialog = Dialog("confirm", "DELETE ENTRY",
                        f"{e['date']}  {e['project']}  {core.fmt_min(e['duration_min'])}", done)


def handle_dialog(app: App, key: str) -> None:
    d = app.dialog
    assert d is not None
    if d.kind == "confirm":
        app.dialog = None
        if key.lower() == "y":
            d.on_ok("")
    elif key == "enter":
        app.dialog = None
        d.on_ok(d.value)
    elif key == "esc":
        app.dialog = None
    elif key == "backspace":
        d.value = d.value[:-1]
    elif len(key) == 1:
        d.value += key


def handle_log(app: App, k: str) -> None:
    last = len(app.entries) - 1
    if k in ("q", "esc", "l"):
        app.mode = "main"
    elif k in ("up", "k"):
        app.log_idx = max(0, app.log_idx - 1)
    elif k in ("down", "j"):
        app.log_idx = min(last, app.log_idx + 1)
    elif k == "pgup":
        app.log_idx = max(0, app.log_idx - 10)
    elif k == "pgdn":
        app.log_idx = min(last, app.log_idx + 10)
    elif k == "home":
        app.log_idx = 0
    elif k == "end":
        app.log_idx = max(0, last)
    elif k == "d":
        _delete_entry(app)


def handle_key(app: App, key: str) -> None:
    if app.dialog:
        return handle_dialog(app, key)
    app.message = ""
    k = key.lower() if len(key) == 1 else key
    if app.mode == "log":
        return handle_log(app, k)
    name = app.selected()
    s = app.session(name)
    if k in ("up", "k"):
        app.sel = max(0, app.sel - 1)
    elif k in ("down", "j"):
        app.sel = min(max(0, len(app.names()) - 1), app.sel + 1)
    elif k == "n":
        _new_project(app)
    elif k == "l":
        app.mode, app.log_idx = "log", max(0, len(app.entries) - 1)
    elif k == "q":
        app.quit = True
    elif name is None:
        return
    elif k == "x":
        _remove_project(app, name)
    elif k == "s" and not s:
        _start(app, name)
    elif k == "p" and s and s["state"] == "running":
        _pause(app, name)
    elif k == "r" and s and s["state"] == "paused":
        _resume(app, name)
    elif k == "e" and s:
        _end(app, name)
    elif k == "enter":
        if not s:
            _start(app, name)
        elif s["state"] == "running":
            _pause(app, name)
        else:
            _resume(app, name)


# ── Rendering ─────────────────────────────────────────────────────────────────

def panel(content, title: str, subtitle: str = "", active: bool = False, padding=(0, 1)) -> Panel:
    t = Text(f" {title} ", style=st("#000000", True, SEL) if active else st(TXT, True))
    return Panel(content, title=t, subtitle=Text(f" {subtitle} ", style=st(DIM)) if subtitle else None,
                 box=box.DOUBLE, border_style=st(TXT), style=f"on {BG}", padding=padding)


def fit(text: str, width: int) -> str:
    return text[:width - 1] + "…" if len(text) > width else text


def minutes_for(entries: list, name: str) -> tuple[float, float]:
    today = datetime.now().date()
    monday = today - timedelta(days=today.weekday())
    d = w = 0.0
    for e in entries:
        if e["project"] != name:
            continue
        day = datetime.strptime(e["date"], "%Y-%m-%d").date()
        w += e["duration_min"] if day >= monday else 0
        d += e["duration_min"] if day == today else 0
    return d, w


def projects_panel(app: App, width: int) -> Panel:
    inner = max(10, width - 4)
    name_w = max(4, inner - 3 - 9)
    lines = Text()
    for i, name in enumerate(app.names()):
        s = app.session(name)
        state = s["state"] if s else "idle"
        timer = core.fmt_hms(s["active_sec"]) if s else ""
        row = f" {ICONS[state]} {fit(name, name_w):<{name_w}} {timer:>8}"[:inner].ljust(inner)
        if i == app.sel:
            lines.append(row, style=st("#000000", True, SEL))
        elif state == "running":
            lines.append(row, style=st(RUN, True))
        elif state == "paused":
            lines.append(row, style=st(PAU))
        else:
            lines.append(row, style=st(TXT))
        lines.append("\n")
    if not app.names():
        lines.append("  No projects.\n  Press n.", style=st(DIM))
    return panel(lines, "PROJECTS", f"{len(app.names())}", active=True)


def session_panel(app: App, height: int, inner_w: int) -> Panel:
    name = app.selected()
    s = app.session(name)
    if name is None:
        return panel(Text("  Nothing selected.", style=st(DIM)), "SESSION", padding=(1, 2))
    state = s["state"] if s else "idle"
    color = {"running": RUN, "paused": PAU, "idle": DIM}[state]
    meta = next((p for p in app.snap["projects"] if p["name"] == name), {})

    out: list = [Text(f"{ICONS[state]}  {state.upper()}", style=st(color, True)), Text("")]
    digits = big_text(core.fmt_hms(s["active_sec"]), color) if s else []
    show_big = bool(digits) and height >= BIG_MIN_H and len(digits[0]) <= inner_w
    if show_big:
        out += digits + [Text("")]

    def field_(label: str, value: str, style: str = st(TXT)) -> Text:
        return Text.assemble((f"{label:<9}", st(SEL, True)), (value, style))

    out.append(field_("Project", name, st(TXT, True)))
    if meta.get("repo"):
        out.append(field_("Repo", f"{meta['repo']}" + (f"  ({meta['branch']})" if meta.get("branch") else "")))
    if s:
        if s["note"]:
            out.append(field_("Note", s["note"], st(DIM)))
        out.append(field_("Started", s["started"][11:]))
        if not show_big:
            out.append(field_("Active", core.fmt_hms(s["active_sec"]), st(color, True)))
        out.append(field_("Paused", core.fmt_hms(s["paused_sec"]), st(PAU if s["paused_sec"] else DIM)))
        out.append(field_("Total", core.fmt_hms(s["wall_sec"])))
    else:
        today, week = minutes_for(app.entries, name)
        out.append(field_("Today", core.fmt_min(today)))
        out.append(field_("Week", core.fmt_min(week)))
    return panel(Group(*out), f"SESSION · {fit(name, 30)}", datetime.now().strftime("%H:%M:%S"), padding=(0, 2))


def entry_cells(e: dict) -> list[str]:
    return [e["date"], e["project"], f"{e['start'][:5]}–{e['end'][:5]}", core.fmt_min(e["duration_min"]),
            "–" if e["paused_min"] is None else core.fmt_min(e["paused_min"]), e["note"]]


def log_table(rows: list, sel_name: Optional[str] = None, cursor: Optional[int] = None) -> Table:
    t = Table(box=box.SIMPLE_HEAD, border_style=st(TXT), header_style=st(SEL, True),
              style=f"on {BG}", show_edge=False, expand=True, padding=(0, 1))
    flex = {"overflow": "ellipsis", "min_width": 6}
    for c, kw in (("Date", {"width": 10}), ("Project", {**flex, "ratio": 2}), ("Time", {"width": 11}),
                  ("Active", {"width": 6, "justify": "right"}), ("Pause", {"width": 6, "justify": "right"}),
                  ("Note", {**flex, "ratio": 3})):
        t.add_column(c, no_wrap=True, **kw)
    for i, (idx, e) in enumerate(rows):
        if cursor is not None and idx == cursor:
            style = st("#000000", True, SEL)
        elif sel_name and e["project"] == sel_name:
            style = st(TXT, True)
        else:
            style = st(TXT)
        t.add_row(*[Text(c) for c in entry_cells(e)], style=style)
    return t


def log_panel(app: App, height: int) -> Panel:
    n = max(1, height - 4)
    rows = list(reversed(list(enumerate(app.entries))[-n:]))
    body = log_table(rows, app.selected()) if rows else Text("  No sessions yet.", style=st(DIM))
    return panel(body, "LOG", f"{len(app.entries)} entries")


def log_manager(app: App, height: int) -> Panel:
    n = max(1, height - 4)
    start = max(0, min(app.log_idx - n // 2, len(app.entries) - n))
    rows = list(enumerate(app.entries))[start:start + n]
    body = log_table(rows, cursor=app.log_idx) if rows else Text("  No entries.", style=st(DIM))
    return panel(body, "LOG MANAGER", f"{app.log_idx + 1 if app.entries else 0}/{len(app.entries)}", active=True)


def dialog_view(app: App) -> Align:
    d = app.dialog
    assert d is not None
    if d.kind == "input":
        body = Group(Text(d.label, style=st(SEL, True, "#000000")),
                     Text(f" {d.value}█".ljust(48), style=st("#000000", False, SEL)))
        hint = "Enter OK · Esc Cancel"
    else:
        body = Text(d.label, style=st(TXT, True, ERR))
        hint = "Y Yes · any other key Cancel"
    box_ = Panel(body, title=Text(f" {d.title} ", style=st("#000000", True, SEL)),
                 subtitle=Text(f" {hint} ", style=st(DIM, False, ERR if d.kind == "confirm" else "#000000")),
                 box=box.DOUBLE, border_style=st(TXT, True, ERR if d.kind == "confirm" else "#000000"),
                 style=f"on {ERR if d.kind == 'confirm' else '#000000'}", width=56, padding=(1, 2))
    return Align.center(box_, vertical="middle", style=f"on {BG}")


def header(app: App, width: int) -> Text:
    left = f" timetrack {__version__} "
    clock = f" {datetime.now():%a %d.%m.%Y %H:%M:%S} "
    room = max(0, width - len(left) - len(clock))
    msg = fit(f" {app.message}", room).ljust(room) if app.message else " " * room
    bar = Text()
    bar.append(left, style=st("#000000", True, SEL))
    bar.append(msg, style=f"bold {TXT} on {ERR}" if app.error and app.message else st("#000000", True, SEL))
    bar.append(clock, style=st("#000000", False, SEL))
    return bar


def keybar(app: App, width: int) -> Text:
    s = app.session(app.selected())
    has = app.selected() is not None
    if app.dialog:
        items = [("↵", "OK", True), ("Esc", "Cancel", True)]
    elif app.mode == "log":
        items = [("↑↓", "Move", True), ("d", "Delete", bool(app.entries)), ("q", "Back", True)]
    else:
        items = [("n", "New", True), ("s", "Start", has and not s),
                 ("p", "Pause", bool(s) and s["state"] == "running"),
                 ("r", "Resume", bool(s) and s["state"] == "paused"),
                 ("e", "End", bool(s)), ("l", "Log", True), ("x", "Remove", has), ("q", "Quit", True)]
    seg = max(8, width // len(items))
    bar = Text()
    for i, (key, label, on) in enumerate(items):
        room = (width - seg * (len(items) - 1) if i == len(items) - 1 else seg) - len(key)
        bar.append(key, style=f"bold {TXT} on #000000")
        bar.append(label.ljust(room)[:room], style=st("#000000", True, SEL) if on else st(DIM))
    bar.append(" " * max(0, width - len(bar.plain)), style=st(DIM))
    return bar


def render(app: App, console: Console) -> Layout:
    w, h = console.size
    body_h = max(4, h - 2)
    root = Layout()
    main = Layout(name="body")
    if app.dialog:
        main.update(dialog_view(app))
    elif app.mode == "log":
        main.update(log_manager(app, body_h))
    else:
        sess_h = body_h * 55 // 100
        left_w = w * 30 // 100
        right = Layout()
        right_inner = w - left_w - 6
        right.split_column(Layout(session_panel(app, sess_h, right_inner), ratio=55),
                           Layout(log_panel(app, body_h - sess_h), ratio=45))
        main.split_row(Layout(projects_panel(app, left_w), ratio=30), Layout(right, ratio=70))
    root.split_column(Layout(header(app, w), size=1), main, Layout(keybar(app, w), size=1))
    return root


# ── Main ──────────────────────────────────────────────────────────────────────

def run(console: Optional[Console] = None) -> None:
    console = console or Console()
    if not sys.stdin.isatty():
        raise TimetrackError("Die TUI braucht ein Terminal.")
    app = App()
    info = core.detect_project()
    if not core.is_generic_dir(info):
        app.detected = info  # Vorschlag für `n`: Repo oder Ordnername
    if info.repo:
        try:
            if info.name not in [p["name"] for p in core.snapshot()["projects"]]:
                core.add_project(info)
        except TimetrackError as e:
            say(app, str(e), True)
    refresh(app)
    if app.detected and app.detected.name in app.names():
        app.sel = app.names().index(app.detected.name)

    fd = sys.stdin.fileno()
    saved = termios.tcgetattr(fd)
    keys = Keys(fd)
    try:
        tty.setcbreak(fd)
        with Live(render(app, console), console=console, screen=True, auto_refresh=False) as live:
            while not app.quit:
                for key in keys.read(0.5):
                    handle_key(app, key)
                    if app.quit:
                        break
                refresh(app)
                live.update(render(app, console), refresh=True)
    except KeyboardInterrupt:
        pass
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, saved)
