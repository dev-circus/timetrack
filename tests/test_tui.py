import io

import pytest
from rich.console import Console

from timetrack import core, tui
from timetrack.core import ProjectInfo


@pytest.fixture(autouse=True)
def tt_home(tmp_path, monkeypatch):
    monkeypatch.setenv("TIMETRACK_HOME", str(tmp_path / "tt"))


@pytest.fixture
def app():
    core.add_project(ProjectInfo("alpha", "alpha", "main", "/x/alpha"))
    core.add_project(ProjectInfo("beta"))
    a = tui.App()
    tui.refresh(a)
    return a


def press(app, *keys):
    for k in keys:
        tui.handle_key(app, k)


def text(app, w=110, h=32) -> str:
    console = Console(file=io.StringIO(), width=w, height=h, force_terminal=True, color_system="truecolor")
    console.print(tui.render(app, console))
    return console.file.getvalue()


def test_parse_keys():
    assert tui.parse_keys("\x1b[A\x1bOB\x1b[5~s\r\x7f\x1bä") == ["up", "down", "pgup", "s", "enter", "backspace", "esc", "ä"]
    assert tui.parse_keys("\x1b[99;5") == []


def test_start_pause_resume_end_mit_notiz(app):
    press(app, "s")
    assert app.session("alpha")["state"] == "running"
    press(app, "p")
    assert app.session("alpha")["state"] == "paused"
    press(app, "r")
    assert app.session("alpha")["state"] == "running"
    press(app, "e", "d", "o", "n", "e", "enter")
    assert app.session("alpha") is None
    assert "Saved" in app.message
    assert [e["note"] for e in app.entries] == ["done"]


def test_enter_schaltet_um_und_start_pausiert_anderes(app):
    press(app, "enter")
    assert app.session("alpha")["state"] == "running"
    press(app, "down", "enter")
    assert app.session("alpha")["state"] == "paused"
    assert app.session("beta")["state"] == "running"
    assert "paused alpha" in app.message
    press(app, "enter")
    assert app.session("beta")["state"] == "paused"


def test_esc_bricht_dialog_ab_und_q_beendet(app):
    press(app, "s", "e", "x", "esc")
    assert app.dialog is None and app.session("alpha") is not None
    press(app, "q")
    assert app.quit


def test_neues_projekt_und_doppelter_name(app):
    press(app, "n", "g", "a", "m", "m", "a", "enter")
    assert app.names()[app.sel] == "gamma"
    press(app, "n", "backspace", "backspace", "backspace", "backspace", "backspace", "a", "l", "p", "h", "a", "enter")
    assert "exists" in app.message and app.error


def test_projekt_entfernen_nur_ohne_session(app):
    press(app, "s", "x")
    assert app.error and "end it first" in app.message and app.dialog is None
    press(app, "down", "x", "n")
    assert "beta" in app.names()
    press(app, "x", "y")
    assert "beta" not in app.names()


def test_log_manager_loeschen_mit_bestaetigung(app):
    press(app, "s", "e", "enter")
    assert len(app.entries) == 1
    press(app, "l")
    assert app.mode == "log"
    press(app, "d", "x")
    assert len(app.entries) == 1
    press(app, "d", "y")
    assert app.entries == [] and app.message == "Entry deleted"
    press(app, "q")
    assert app.mode == "main"


def test_aenderung_von_aussen_ist_sichtbar(app):
    core.start("beta")
    tui.refresh(app)
    assert app.session("beta")["state"] == "running"
    assert "beta" in text(app)


def test_render_alle_modi_und_kleine_terminals(app):
    press(app, "s")
    out = text(app)
    for needle in ("PROJECTS", "SESSION", "LOG", "alpha", "RUNNING", "New", "Quit"):
        assert needle in out
    assert "48;2;232;234;95" in out  # große Ziffern: Hintergrundfarbe RUN
    press(app, "e")
    assert "END SESSION" in text(app)
    press(app, "esc", "l")
    assert "LOG MANAGER" in text(app)
    for w, h in ((60, 15), (40, 8), (200, 60)):
        text(app, w, h)


def test_render_mit_log_eintraegen(app):
    press(app, "s", "e", "r", "e", "f", "enter")
    assert len(app.entries) == 1
    out = text(app)
    assert "alpha" in out and "1 entries" in out and "ref" in out
    press(app, "l")
    out = text(app)
    assert "LOG MANAGER" in out and "1/1" in out
    for w, h in ((60, 15), (40, 8)):
        text(app, w, h)


def test_render_ohne_projekte():
    a = tui.App()
    tui.refresh(a)
    assert "No projects" in text(a)
    press(a, "s", "p", "e", "x")
    assert not a.error
