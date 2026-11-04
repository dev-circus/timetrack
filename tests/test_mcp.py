import asyncio
import json
import os
import subprocess
import sys

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client


def _payload(result) -> dict:
    if result.structured_content:
        return result.structured_content
    return json.loads(result.content[0].text)


def test_mcp_roundtrip_ueber_stdio(tmp_path):
    repo = tmp_path / "demo-repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "timetrack.mcp_server"],
        env={**os.environ, "TIMETRACK_HOME": str(tmp_path / "tt"), "HOME": str(fake_home)},
        cwd=repo,
    )

    async def run():
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as s:
                await s.initialize()
                call = lambda name, **a: s.call_tool(name, a)  # noqa: E731

                st = _payload(await call("status"))
                assert st["project"] == "demo-repo" and st["session"] is None

                started = _payload(await call("start", note="refactor"))
                assert "gestartet" in started["message"]

                st = _payload(await call("status"))
                assert st["session"]["state"] == "running"
                assert st["session"]["branch"] == "main"

                assert _payload(await call("pause"))["state"] == "paused"
                assert _payload(await call("resume"))["state"] == "running"

                done = _payload(await call("stop", note="fertig"))
                assert done["project"] == "demo-repo" and done["note"] == "fertig"

                entries = _payload(await call("log"))["entries"]
                assert [e["note"] for e in entries] == ["fertig"]
                assert _payload(await call("summary", period="today"))["by_project"][0]["project"] == "demo-repo"

                err = await call("stop")
                assert err.is_error and "Keine aktive Session" in err.content[0].text

                # Ordner ohne Repo: Ordnername wird Projekt, kein Repo im Log
                plain = tmp_path / "Studium"
                plain.mkdir()
                assert _payload(await call("start", cwd=str(plain)))["project"] == "Studium"
                assert _payload(await call("stop", cwd=str(plain)))["repo"] == ""

                # Home ohne `project`: Fehler mit Handlungsanweisung; mit Namen läuft es
                no_dir = await s.call_tool("start", {"cwd": str(fake_home)})
                assert no_dir.is_error and "`project`" in no_dir.content[0].text
                named = _payload(await call("start", project="Netcup Server", cwd=str(fake_home)))
                assert named["project"] == "Netcup Server"
                assert _payload(await call("stop", project="Netcup Server", cwd=str(fake_home)))["repo"] == ""

                guard = await s.call_tool("status", {"cwd": str(fake_home)})
                assert guard.is_error and "Kein Projekt erkannt" in guard.content[0].text

    asyncio.run(run())
