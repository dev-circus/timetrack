# timetrack

Projektbezogene Zeiterfassung im Terminal. Ein gemeinsamer Core (State + Log), darauf drei Frontends:

- **TUI** im Norton-Commander-Look (`timetrack`)
- **CLI** (`timetrack start|pause|resume|stop|status|log|summary|projects|migrate`)
- **MCP-Server** für Claude (`timetrack-mcp`)

Das Projekt wird aus dem Git-Repo des Arbeitsverzeichnisses erkannt. Sessions überleben das Schließen
des Terminals: Die TUI zeigt live, was per CLI oder Claude gestartet wurde.

## Setup

```bash
brew install uv
uv tool install /Users/patrick/Dev/timetrack   # legt timetrack und timetrack-mcp in ~/.local/bin ab
timetrack migrate --dry-run                      # alte Logs prüfen, dann ohne --dry-run
```

## TUI

`n` neues Projekt · `s`/`↵` Start · `p` Pause · `r` Resume · `e` Ende (mit Notiz) · `l` Log-Manager · `x` Projekt entfernen · `q` Quit

Im Log-Manager: `↑↓` bewegen, `d` löschen (Bestätigung mit `y`).

## Claude (MCP)

```bash
claude mcp add --scope user timetrack -- /Users/patrick/.local/bin/timetrack-mcp
```

Tools: `status`, `start`, `pause`, `resume`, `stop` (mit `note` und nachträglichem `at`), `log`, `summary`, `projects`.
Claude startet und stoppt die Zeit nur auf Zuruf. Das Arbeitsverzeichnis wird als `cwd` übergeben.

## Daten

Standard: `~/.timetrack/` (überschreibbar mit `TIMETRACK_HOME`).

| Datei | Inhalt |
|---|---|
| `state.json` | Projekte und offene Sessions (flock + atomarer Write) |
| `log.csv` | `date,project,repo,branch,start,end,duration_min,note,paused_min` |

`duration_min` ist die **Aktivzeit ohne Pausen**, `paused_min` die Pausenzeit. Einträge vor v0.1 haben keine
Pausenangabe und enthalten die Bruttozeit.

## Entwicklung

```bash
uv sync
uv run pytest
```

`legacy/timetrack-v3.py` ist die alte Einzeldatei-Version (nur Archiv).

---

© 2026 dev_circus · www.dev-circus.com · by Patrick B. Stang
