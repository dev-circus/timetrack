from datetime import datetime

import pytest

from timetrack import core
from timetrack.core import ProjectInfo, TimetrackError

T0 = datetime(2026, 11, 4, 9, 0, 0).timestamp()
MIN = 60
ALPHA = ProjectInfo("alpha", "alpha", "main", "/x/alpha")
BETA = ProjectInfo("beta", "beta", "dev", "/x/beta")


@pytest.fixture(autouse=True)
def tt_home(tmp_path, monkeypatch):
    monkeypatch.setenv("TIMETRACK_HOME", str(tmp_path / "tt"))
    return tmp_path / "tt"


def test_log_enthaelt_aktivzeit_ohne_pausen():
    core.start("alpha", info=ALPHA, now=T0)
    core.pause("alpha", now=T0 + 10 * MIN)
    core.resume("alpha", now=T0 + 15 * MIN)
    e = core.stop("alpha", now=T0 + 30 * MIN)
    assert e["duration_min"] == 25.0
    assert e["paused_min"] == 5.0
    assert e["wall_min"] == 30.0
    assert core.read_log()[0]["duration_min"] == 25.0


def test_stop_im_pausenzustand():
    core.start("alpha", info=ALPHA, now=T0)
    core.pause("alpha", now=T0 + 10 * MIN)
    e = core.stop("alpha", now=T0 + 20 * MIN)
    assert (e["duration_min"], e["paused_min"]) == (10.0, 10.0)


def test_stop_at_vor_offener_pause():
    core.start("alpha", info=ALPHA, now=T0)
    core.pause("alpha", now=T0 + 10 * MIN)
    e = core.stop("alpha", at="09:05", now=T0 + 60 * MIN)
    assert (e["duration_min"], e["paused_min"], e["end"]) == (5.0, 0.0, "09:05:00")


def test_stop_at_ungueltig():
    core.start("alpha", info=ALPHA, now=T0)
    with pytest.raises(TimetrackError, match="vor dem Start"):
        core.stop("alpha", at="08:00", now=T0 + 60 * MIN)
    assert core.snapshot(T0)["sessions"][0]["project"] == "alpha"


def test_parse_at_zukunft_wird_gestern():
    assert core.parse_at("23:50", T0) == datetime(2026, 11, 3, 23, 50).timestamp()


def test_start_pausiert_laufendes_projekt():
    core.start("alpha", info=ALPHA, now=T0)
    r = core.start("beta", info=BETA, now=T0 + 10 * MIN)
    assert r["auto_paused"] == ["alpha"]
    states = {s["project"]: s["state"] for s in core.snapshot(T0 + 11 * MIN)["sessions"]}
    assert states == {"alpha": "paused", "beta": "running"}
    r = core.resume("alpha", now=T0 + 20 * MIN)
    assert r["auto_paused"] == ["beta"]


def test_session_ueberlebt_neustart_und_fehler_aendern_nichts():
    core.start("alpha", info=ALPHA, now=T0, note="x")
    with pytest.raises(TimetrackError):
        core.start("alpha", now=T0 + MIN)
    s = core.snapshot(T0 + 5 * MIN)["sessions"][0]
    assert (s["state"], s["note"], s["active_sec"]) == ("running", "x", 300)


def test_unbekanntes_projekt_und_keine_session():
    with pytest.raises(TimetrackError, match="nicht angelegt"):
        core.start("nix", now=T0)
    with pytest.raises(TimetrackError, match="Keine aktive Session"):
        core.pause("alpha", now=T0)


def test_altes_log_wird_beim_append_hochgezogen(tt_home):
    tt_home.mkdir()
    (tt_home / "log.csv").write_text(
        "date,project,repo,branch,start,end,duration_min,note\n"
        "2026-03-18,old,Website,dev,09:44:45,09:46:34,1.83,x\n"
    )
    core.start("alpha", info=ALPHA, now=T0)
    core.stop("alpha", now=T0 + 10 * MIN)
    rows = core.read_log()
    assert [r["project"] for r in rows] == ["old", "alpha"]
    assert rows[0]["paused_min"] is None and rows[1]["paused_min"] == 0.0
    assert (tt_home / "log.csv").read_text().splitlines()[0] == ",".join(core.CSV_HEADER)


def test_read_log_ueberlebt_kaputte_dauer(tt_home):
    tt_home.mkdir()
    (tt_home / "log.csv").write_text(
        ",".join(core.CSV_HEADER) + "\n2026-03-18,p,r,b,09:00:00,10:00:00,kaputt,,\n"
    )
    assert core.read_log()[0]["duration_min"] == 0.0


def test_migrate_dedupe_dry_run_und_backup(tt_home, tmp_path):
    tt_home.mkdir()
    (tt_home / "log.csv").write_text(
        "date,project,repo,branch,start,end,duration_min,note\n"
        "2026-03-18,a,R,dev,09:00:00,10:00:00,60,\n"
        "2026-03-20,c,R,dev,09:00:00,10:00:00,60,\n"
    )
    src = tmp_path / "alt.csv"
    src.write_text(
        "date,project,repo,branch,start,end,duration_min,note\n"
        "2026-03-18,a,R,dev,09:00:00,10:00:00,60,\n"
        "2026-03-19,b,R,dev,09:00:00,10:00:00,60,\n"
    )
    before = (tt_home / "log.csv").read_text()
    assert core.migrate([src], dry_run=True) == {"added": 1, "total": 3, "backup": None}
    assert (tt_home / "log.csv").read_text() == before

    r = core.migrate([src])
    assert (r["added"], r["total"]) == (1, 3)
    assert [x["project"] for x in core.read_log()] == ["a", "b", "c"]
    assert open(r["backup"]).read() == before
    assert src.read_text().count("\n") == 3

    assert core.migrate([src]) == {"added": 0, "total": 3, "backup": None}


def test_delete_log_entry_mit_schutz():
    for i, n in enumerate(("a", "b")):
        core.start("alpha", info=ALPHA, now=T0 + i * 60 * MIN)
        core.stop("alpha", note=n, now=T0 + i * 60 * MIN + 5 * MIN)
    rows = core.read_log()
    with pytest.raises(TimetrackError, match="geändert"):
        core.delete_log_entry(0, {"date": "1999-01-01", "project": "alpha", "start": "09:00:00"})
    core.delete_log_entry(0, rows[0])
    assert [r["note"] for r in core.read_log()] == ["b"]


def test_summary_perioden():
    for day, mins in ((2, 30), (3, 20), (4, 10)):  # Mo, Di, Mi der Woche
        t = datetime(2026, 11, day, 9, 0).timestamp()
        core.start("alpha", info=ALPHA, now=t)
        core.stop("alpha", now=t + mins * MIN)
    t = datetime(2026, 10, 1, 9, 0).timestamp()
    core.start("beta", info=BETA, now=t)
    core.stop("beta", now=t + 90 * MIN)
    now = datetime(2026, 11, 4, 12, 0)
    assert core.summary("today", today=now)["total_min"] == 10.0
    assert core.summary("week", today=now)["total_min"] == 60.0
    assert core.summary("month", today=now)["total_min"] == 60.0
    s = core.summary("all", today=now)
    assert s["total_min"] == 150.0 and s["by_project"][0]["project"] == "beta"
    with pytest.raises(TimetrackError):
        core.summary("jahr")
