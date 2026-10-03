# ♞ Chess Profile Analyzer

![Python 3.9+](https://img.shields.io/badge/python-3.9%2B-blue)
![Engine: Stockfish](https://img.shields.io/badge/engine-Stockfish-green)
![Sites: Chess.com | Lichess](https://img.shields.io/badge/sites-Chess.com%20%7C%20Lichess-orange)

Pull a player's recent games from **Chess.com** or **Lichess**, run every move through **Stockfish**, and get a full report with an anti-cheat score.

I got tired of checking games by hand and guessing whether someone was cheating, so I automated it.

> ⚠️ **This is a heuristic, not proof.** See [Limitations & responsible use](#limitations--responsible-use) before drawing conclusions about anyone.
- [Vynozarell Website](https://vynozarell.ct.ws/)
- [Gunslol Website](https://guns.lol/vynozarell)
---

## Table of contents

- [Features](#features)
- [Quick start](#quick-start)
- [Usage](#usage)
- [Output](#output)
- [Configuration](#configuration)
- [How the cheat detection works](#how-the-cheat-detection-works)
- [Limitations & responsible use](#limitations--responsible-use)
- [Troubleshooting](#troubleshooting)
- [Credits](#credits)
- [License](#license)

---

## Features

- **Auto-installs Stockfish** if you don't have it (Windows, macOS Intel, macOS Apple Silicon, Linux)
- **Two sources:** Chess.com and Lichess
- **Multi-core analysis:** uses all your CPU cores
- **Resumable:** results are cached in SQLite, so hitting `Ctrl+C` doesn't lose progress
- **Live terminal UI** with real-time progress
- **Detailed per-move stats:** accuracy, ACPL, blunders, mistakes, and opening / middlegame / endgame breakdowns
- **Anti-cheat scoring** built from 6 weighted signals
- **Four export formats:** HTML dashboard, PNG chart, CSV, JSON

---

## Quick start

**Requirements:** Python 3.9 or newer.

```bash
git clone https://github.com/vynozarell/Chess-Cheat-Analyzer/
cd Chess-Cheat-Analyzer

# optional but recommended: use a virtual environment
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate

pip install -r requirements.txt
python main.py
```

Stockfish is downloaded automatically on first run. To use your own copy instead:

```bash
# macOS / Linux
export STOCKFISH_PATH=/path/to/your/stockfish

# Windows (PowerShell)
$env:STOCKFISH_PATH = "C:\path\to\stockfish.exe"
```

---

## Usage

Run the script and answer the prompts:

```bash
python main.py
```

| Prompt | Meaning |
|---|---|
| **Site** | `chesscom` or `lichess` |
| **Username** | The account to analyze |
| **Months back / Max games** | How far back to download games |
| **Games to analyze** | How many of those games actually go through Stockfish (each one takes a while) |

Your answers are saved to `~/.chess-analyzer/config.json`, so the next run pre-fills your previous choices.

**Example session:**

```text
♞  CHESS PROFILE ANALYZER
By VYNOZARELL?
   Hidden Cheater? No problem!

Site (chesscom, lichess) [chesscom]:
Username: hikaru
Months back [2]:
Games to analyze (Stockfish) [15]:
```

---

## Output

Everything is written to `results/<username>_<site>/`:

```text
results/ (sorry hikaru)
└── hikaru_chesscom/
    ├── hikaru_profile.html    ← open this one in a browser
    ├── hikaru_profile.png     ← 4-panel chart
    ├── hikaru_games.csv       ← per-game spreadsheet
    └── hikaru_profile.json    ← raw data
```

The HTML report is fully self-contained (the chart is embedded as base64), so you can send the single file to someone and it will open correctly.

---

## Configuration

Two constants at the top of `main.py` control the speed/accuracy trade-off:

```python
ANALYSIS_DEPTH = 12     # higher = more accurate but slower
ENGINE_HASH_MB = 128    # hash size per engine thread
```

| Goal | Suggested change |
|---|---|
| Analyzing many games and it's dragging | Lower `ANALYSIS_DEPTH` to `10` |
| Most use cases | Keep the default of `12` |
| Maximum precision | Raise `ANALYSIS_DEPTH` to `15` or more (expect long waits) |

| Environment variable | Purpose |
|---|---|
| `STOCKFISH_PATH` | Use your own Stockfish binary instead of the auto-downloaded one |

---

## How the cheat detection works

The player's measured performance is compared against baselines built for each **rating bracket** (1000–2200) and **time control** (bullet / blitz / rapid). Six signals feed into one severity score:

| Signal | Weight | What it catches |
|---|:---:|---|
| Selective assistance | 0.30 | Accuracy spikes in some games but not others |
| Raw engine strength | 0.25 | Play that is simply too accurate for the rating |
| Cosmetic blunders | 0.15 | Blunders that are "just bad enough" (300–450 CPL range) |
| Tactical precision | 0.15 | Engine top-choice rate in the middlegame/endgame that's above typical human levels |
| Clock uniformity | 0.10 | Think times that are too consistent (engines don't get distracted) |
| Rating gap | 0.05 | Play that implies a much higher Elo than the account shows |

**Verdicts**

| Condition | Label |
|---|---|
| Severity ≥ 0.60 **or** 3+ strong flags | ⛔ Likely Cheater |
| Severity ≥ 0.30 **or** 2+ strong flags | ⚠ Maybe Cheater |
| Otherwise | ✓ Legit |

---

## Limitations & responsible use

This tool produces a **statistical hint, not evidence**. False positives are expected, especially for:

- Genuinely strong players or fast-improving ones
- Smurfs and returning titled players
- Players who simply had an unusually good stretch
- Small samples: analyzing only a handful of games makes every signal noisy

Please **don't report or publicly accuse anyone based on this output alone**. I built this for fun and personal curiosity. Also be considerate of Chess.com and Lichess: the tool uses their public APIs, so keep request volume reasonable.

---

## Troubleshooting

| Problem | Fix |
|---|---|
| `Stockfish unusable` | Install Stockfish manually and set `STOCKFISH_PATH` |
| `Fetch failed` | Double-check the username. Chess.com may also be rate-limiting you (this happens often), so wait a few minutes and retry |
| `No clock data` in the timing section | Some PGNs don't include `%clk` comments. Nothing can be done, and the clock-uniformity signal is skipped for those games |
| Analysis is too slow | Lower `ANALYSIS_DEPTH` or analyze fewer games |
| Stopped halfway | Just run it again. Already-analyzed games are cached and skipped |

---

## Credits

- [python-chess](https://github.com/niklasf/python-chess): board, PGN, and engine handling
- [Stockfish](https://stockfishchess.org/): the engine doing the analysis
- [Rich](https://github.com/Textualize/rich): terminal UI
- [Matplotlib](https://matplotlib.org/): charts

---

## License

Personal use. Stockfish is licensed under GPLv3. Chess.com and Lichess data comes from their public APIs; please follow their terms of use.
