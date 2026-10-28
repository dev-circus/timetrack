# timetrack

Terminal-based time tracker with Git integration. Track working hours across multiple projects directly in the terminal — no browser, no account, no cloud.

```
┌─ PROJECTS ──────────────┬─ SESSION · myproject ──────────────────────────────┐
│ ● myproject   00:23:14  │ ──────────────────────────────────────────────────  │
│ ○ website               │ ●  RUNNING                                          │
│ ○ design                │ ──────────────────────────────────────────────────  │
│                         │                                                     │
│                         │ Project    myproject                                │
│                         │ Repo       timetrack  (main)                       │
│                         │                                                     │
│                         │ Active     00:23:14                                 │
│                         │ Total      00:23:14                                 │
├─────────────────────────┴─────────────────────────────────────────────────────┤
│ [n] New  [s] Start  [p] Pause  [r] Resume  [e] End  [l] Log  [q] Quit        │
└───────────────────────────────────────────────────────────────────────────────┘
```

## Features

- **Multi-project** — manage multiple projects side by side
- **Pause & resume** — tracks active time separately from total elapsed time
- **Auto-pause** — switching to another project pauses the active one automatically
- **Git integration** — detects current repo and branch on project creation
- **Log manager** — view, navigate, and delete past sessions
- **Local only** — all data stays on your machine, saved as CSV

## Tech Stack

- **Python 3.7+**
- **[rich](https://github.com/Textualize/rich)** — terminal UI (panels, tables, live rendering)
- **CSV** — session log at `~/timetrack/log.csv`
- No database, no network, no dependencies beyond `rich`

## Installation

```bash
# 1. Clone the repo
git clone https://github.com/dev-circus/timetrack.git
cd timetrack

# 2. Install dependency
pip install rich

# 3. Run
python3 timetrack.py
```

**Optional — add to PATH for global access:**

```bash
cp timetrack.py ~/bin/timetrack
chmod +x ~/bin/timetrack
```

Then run from anywhere:

```bash
timetrack
```

> Requires macOS or Linux. Uses `tty` and `termios` for raw terminal input.

## Usage

On first launch, timetrack prompts you to add a project. It auto-detects the current Git repo and branch — accept or override manually.

### Controls

| Key | Action |
|-----|--------|
| `↑` `↓` | Navigate project list |
| `n` | Add new project |
| `s` | Start tracking |
| `p` | Pause |
| `r` | Resume |
| `e` | End session & save |
| `l` | Open log manager |
| `q` | Quit |

### Log Manager (`l`)

Opens a full-screen view of all recorded sessions. Navigate with `↑` `↓`, press `d` to delete the selected entry (with confirmation prompt), `q` to go back.

## Log Format

Sessions are saved to `~/timetrack/log.csv`:

```
date, project, repo, branch, start, end, duration_min, note
```

Open in Excel, Numbers, or any CSV viewer to analyze your tracked time.

## License

MIT — © 2026 Patrick B. Stang — dev_circus · [www.dev-circus.com](https://www.dev-circus.com)
