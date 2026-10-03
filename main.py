# discord : vynozarell // github : vynozarell // website : vynozarell.ct.ws
# Cheater?
from __future__ import annotations

import base64
import concurrent.futures as cf
import hashlib
import io
import json
import math
import os
import platform
import re
import shutil
import sqlite3
import stat
import string
import sys
import tarfile
import tempfile
import threading
import time
import traceback
import zipfile
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean, median, pstdev, stdev
from typing import Optional, Callable

import chess
import chess.engine
import chess.pgn
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import requests
from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.progress import (
    Progress, BarColumn, TextColumn, TimeElapsedColumn,
    TimeRemainingColumn, MofNCompleteColumn, SpinnerColumn,
)
from rich.table import Table
from rich.text import Text
from rich.prompt import Prompt, IntPrompt
from rich.align import Align

console = Console()
os.system('cls' if os.name == 'nt' else 'clear')

# ── config ──────────────────────────────────────────────────────────────────
HEADERS = {"User-Agent": "chess-profile-analyzer/7.0 (personal)"}
CHESSCOM = "https://api.chess.com/pub"
LICHESS = "https://lichess.org/api"
CACHE_DB = Path("cache.db")
CONFIG_FILE = Path.home() / ".chess-analyzer" / "config.json"
SF_DIR = Path.home() / ".chess-analyzer" / "engines"
RESULTS_ROOT = Path("results")
ANALYSIS_DEPTH = 12
ENGINE_HASH_MB = 128

SF_RELEASES = {
    ("Windows", "AMD64"):  "https://github.com/official-stockfish/Stockfish/releases/download/sf_16.1/stockfish-windows-x86-64.zip",
    ("Windows", "x86"):    "https://github.com/official-stockfish/Stockfish/releases/download/sf_16.1/stockfish-windows-x86-32.zip",
    ("Darwin",  "arm64"):  "https://github.com/official-stockfish/Stockfish/releases/download/sf_16.1/stockfish-macos-m1-apple-silicon.tar",
    ("Darwin",  "x86_64"): "https://github.com/official-stockfish/Stockfish/releases/download/sf_16.1/stockfish-macos-x86-64-avx2.tar",
    ("Linux",   "x86_64"): "https://github.com/official-stockfish/Stockfish/releases/download/sf_16.1/stockfish-ubuntu-x86-64-avx2.tar",
}


# ════════════════════════════════════════════════════════════════════════════
# 0. UTILITIES
# ════════════════════════════════════════════════════════════════════════════
def _safe_name(s: str) -> str:
    """Sanitize a username for use as a folder name."""
    s = (s or "unknown").strip()
    s = re.sub(r"[^\w.\-]+", "_", s)
    return s[:64] or "unknown"


def result_dir(username: str, site: str) -> Path:
    """results/<username>_<site>/ (created on demand)."""
    d = RESULTS_ROOT / f"{_safe_name(username)}_{site}"
    d.mkdir(parents=True, exist_ok=True)
    return d


def http_get(url, *, headers=None, params=None, stream=False, timeout=30,
             retries=3):
    hdrs = {**HEADERS, **(headers or {})}
    last = None
    for attempt in range(retries):
        try:
            r = requests.get(url, headers=hdrs, params=params,
                             stream=stream, timeout=timeout)
            r.raise_for_status()
            return r
        except (requests.ConnectionError, requests.Timeout,
                requests.HTTPError) as e:
            last = e
            if isinstance(e, requests.HTTPError) and e.response is not None:
                code = e.response.status_code
                if 400 <= code < 500 and code != 429:
                    raise
            time.sleep(2 ** attempt)
    raise last


def load_config():
    if CONFIG_FILE.exists():
        try: return json.loads(CONFIG_FILE.read_text())
        except Exception: return {}
    return {}


def save_config(cfg):
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2))


# ════════════════════════════════════════════════════════════════════════════
# 1. STOCKFISH AUTO-SETUP
# ════════════════════════════════════════════════════════════════════════════
def _machine_arch():
    m = platform.machine().lower()
    if m in ("x86_64", "amd64"):
        return "AMD64" if platform.system() == "Windows" else "x86_64"
    if m in ("arm64", "aarch64"): return "arm64"
    if m in ("i386", "i686", "x86"): return "x86"
    return m


def _is_binary(p: Path):
    try:
        if not p.is_file(): return False
        head = p.read_bytes()[:4]
        if platform.system() == "Windows": return head[:2] == b"MZ"
        return head[:4] == b"\x7fELF" or bool(p.stat().st_mode & stat.S_IXUSR)
    except Exception:
        return False


def _which_stockfish():
    env = os.environ.get("STOCKFISH_PATH")
    if env and Path(env).exists(): return env
    try:
        import stockfish as sfpkg
        base = Path(sfpkg.__file__).parent
        for c in base.rglob("stockfish*"):
            if _is_binary(c): return str(c)
    except Exception: pass
    if SF_DIR.exists():
        for exe in SF_DIR.rglob("*"):
            if exe.is_file() and exe.name.lower().startswith("stockfish"):
                if _is_binary(exe): return str(exe)
    for name in ("stockfish", "stockfish.exe"):
        p = shutil.which(name)
        if p: return p
    for p in ("/usr/games/stockfish", "/usr/local/bin/stockfish",
              "/opt/homebrew/bin/stockfish",
              "C:\\Program Files\\Stockfish\\stockfish.exe"):
        if Path(p).exists(): return p
    return None


def _download(url, dest, label="Stockfish"):
    dest.parent.mkdir(parents=True, exist_ok=True)
    with http_get(url, stream=True, timeout=120) as r:
        total = int(r.headers.get("content-length", 0))
        with Progress(SpinnerColumn(), TextColumn(f"[cyan]{label}"),
                      BarColumn(), MofNCompleteColumn(),
                      TextColumn("·"), TimeElapsedColumn(),
                      console=console) as prog:
            task = prog.add_task("dl", total=total or None)
            with open(dest, "wb") as f:
                for chunk in r.iter_content(1 << 16):
                    f.write(chunk); prog.update(task, advance=len(chunk))


def _extract(archive, dest):
    dest.mkdir(parents=True, exist_ok=True)
    if archive.suffix == ".zip":
        with zipfile.ZipFile(archive) as z: z.extractall(dest)
    else:
        with tarfile.open(archive) as t: t.extractall(dest)


def _find_extracted(folder):
    for p in folder.rglob("*"):
        if p.is_file() and p.name.lower().startswith("stockfish"):
            if _is_binary(p) or p.suffix == ".exe": return p
    return None


def install_stockfish(force=False):
    if not force:
        ex = _which_stockfish()
        if ex: return ex
    system, arch = platform.system(), _machine_arch()
    url = SF_RELEASES.get((system, arch))
    if not url:
        console.print(f"[red]✗ No prebuilt Stockfish for {system}/{arch}.[/]")
        return None
    console.print(f"[cyan]⇣ Installing Stockfish ({system} {arch})…[/]")
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        ext = ".zip" if url.endswith(".zip") else ".tar"
        archive = td / f"sf{ext}"
        try:
            _download(url, archive)
            _extract(archive, SF_DIR)
        except Exception as e:
            console.print(f"[red]✗ Install failed:[/] {e}")
            return None
    exe = _find_extracted(SF_DIR)
    if not exe:
        console.print("[red]✗ No binary found after extraction.[/]")
        return None
    if system != "Windows":
        try: exe.chmod(exe.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        except Exception: pass
    console.print(f"[green]✓[/] Installed → [bold]{exe}[/]")
    return str(exe)


# ════════════════════════════════════════════════════════════════════════════
# 2. FETCHERS
# ════════════════════════════════════════════════════════════════════════════
def chesscom_profile(u): return http_get(f"{CHESSCOM}/player/{u}").json()
def chesscom_archives(u): return http_get(f"{CHESSCOM}/player/{u}/games/archives").json()["archives"]

def chesscom_games(u, months=3):
    archives = chesscom_archives(u)[-months:]
    out = []
    for url in archives:
        try:
            out.extend(http_get(url).json().get("games", []))
            time.sleep(0.25)
        except Exception: pass
    return out

def lichess_profile(u): return http_get(f"{LICHESS}/user/{u}").json()

def lichess_games(u, max_games=50):
    params = {"max": max_games, "pgnInJson": "true", "opening": "true",
              "clocks": "true", "evals": "true"}
    r = http_get(f"{LICHESS}/games/user/{u}",
                 headers={"Accept": "application/x-ndjson"},
                 params=params, timeout=60)
    return [json.loads(l) for l in r.text.splitlines() if l.strip()]


# ════════════════════════════════════════════════════════════════════════════
# 3. GAME MODEL
# ════════════════════════════════════════════════════════════════════════════
@dataclass
class Game:
    url: str
    site: str
    pgn: str
    white: str
    black: str
    white_rating: int
    black_rating: int
    result: str
    time_class: str
    time_control: str
    rated: bool
    end_time: datetime
    opening: Optional[str] = None
    eco: Optional[str] = None
    move_data: list = field(default_factory=list)
    metrics_w: Optional[dict] = None
    metrics_b: Optional[dict] = None

    @property
    def uid(self):
        return hashlib.sha1(self.pgn.encode()).hexdigest()[:16]


def normalize_chesscom(games):
    out = []
    for g in games:
        try:
            w, b = g["white"], g["black"]
            pgn = g.get("pgn", "")
            result = "1/2-1/2"
            for tok in reversed(pgn.split()):
                if tok in ("1-0", "0-1", "1/2-1/2"): result = tok; break
            opening = eco = None
            try:
                n = chess.pgn.read_game(io.StringIO(pgn))
                if n:
                    opening = n.headers.get("ECOUrl", "").split("/")[-1].replace("-", " ") or None
                    eco = n.headers.get("ECO")
            except Exception: pass
            out.append(Game(
                url=g.get("url", ""), site="chesscom", pgn=pgn,
                white=w["username"], black=b["username"],
                white_rating=w.get("rating") or 0,
                black_rating=b.get("rating") or 0,
                result=result, time_class=g.get("time_class") or "blitz",
                time_control=g.get("time_control", ""),
                rated=bool(g.get("rated")),
                end_time=datetime.fromtimestamp(g["end_time"], tz=timezone.utc),
                opening=opening, eco=eco))
        except Exception: continue
    return out


def normalize_lichess(games):
    out = []
    for g in games:
        try:
            p = g["players"]; w, b = p["white"], p["black"]
            winner = g.get("winner")
            result = "1-0" if winner == "white" else "0-1" if winner == "black" else "1/2-1/2"
            clock = g.get("clock", {})
            tc = f"{clock.get('initial','?')}+{clock.get('increment','?')}"
            out.append(Game(
                url=f"https://lichess.org/{g['id']}", site="lichess",
                pgn=g.get("pgn", ""),
                white=w.get("user", {}).get("name", "anon"),
                black=b.get("user", {}).get("name", "anon"),
                white_rating=w.get("rating") or 0,
                black_rating=b.get("rating") or 0,
                result=result, time_class=g.get("speed", "blitz"),
                time_control=tc, rated=bool(g.get("rated")),
                end_time=datetime.fromtimestamp(g["createdAt"] / 1000, tz=timezone.utc),
                opening=g.get("opening", {}).get("name"),
                eco=g.get("opening", {}).get("eco")))
        except Exception: continue
    return out


# ════════════════════════════════════════════════════════════════════════════
# 4. ENGINE (with crash recovery)
# ════════════════════════════════════════════════════════════════════════════
class Engine:
    def __init__(self, path, depth=ANALYSIS_DEPTH, threads=1):
        self.path, self.depth, self.threads = path, depth, threads
        self._eng = None

    def __enter__(self):
        self._spawn()
        return self

    def _spawn(self):
        if self._eng:
            try: self._eng.quit()
            except Exception: pass
        self._eng = chess.engine.SimpleEngine.popen_uci(self.path)
        self._eng.configure({"Threads": self.threads, "Hash": ENGINE_HASH_MB})

    def __exit__(self, *a):
        if self._eng:
            try: self._eng.quit()
            except Exception: pass

    def analyse(self, board):
        try:
            info = self._eng.analyse(board, chess.engine.Limit(depth=self.depth))
        except (chess.engine.EngineTerminatedError, BrokenPipeError,
                chess.engine.EngineError):
            self._spawn()
            info = self._eng.analyse(board, chess.engine.Limit(depth=self.depth))
        cp = info["score"].white().score(mate_score=10000) or 0
        pv = [m.uci() for m in (info.get("pv") or [])]
        return cp, pv


# ════════════════════════════════════════════════════════════════════════════
# 5. METRICS
# ════════════════════════════════════════════════════════════════════════════
def win_percent(cp):
    return 50.0 + 50.0 * (2.0 / (1.0 + math.exp(-0.00368208 * cp)) - 1.0)

def move_accuracy(wb, wa):
    if wb == wa: return 100.0
    d = wb - wa
    return max(0.0, min(100.0, 103.1668 * math.exp(-0.04354 * d) - 3.1669))

def classify(cpl):
    if cpl <= 10:  return "best"
    if cpl <= 50:  return "good"
    if cpl <= 100: return "inaccuracy"
    if cpl <= 300: return "mistake"
    return "blunder"

def lichess_phase(ply, board):
    if ply < 20: return "opening"
    mj = mn = 0
    for sq in chess.SQUARES:
        p = board.piece_at(sq)
        if not p: continue
        if p.piece_type in (chess.QUEEN, chess.ROOK): mj += 1
        elif p.piece_type in (chess.BISHOP, chess.KNIGHT): mn += 1
    return "endgame" if mj + mn <= 6 else "middlegame"


def analyze_game(game, engine, progress_cb=None):
    try:
        pgn = chess.pgn.read_game(io.StringIO(game.pgn))
    except Exception:
        return []
    if not pgn: return []
    board = pgn.board()
    moves = list(pgn.mainline_moves())
    if not moves: return []

    prev_cp, prev_pv = engine.analyse(board)
    md = []
    for i, mv in enumerate(moves):
        mover_white = board.turn
        was_best = bool(prev_pv) and prev_pv[0] == mv.uci()
        board.push(mv)
        cp_after, pv_after = engine.analyse(board)
        before = prev_cp if mover_white else -prev_cp
        after = cp_after if mover_white else -cp_after
        cpl = max(0, before - after)
        acc = move_accuracy(win_percent(before), win_percent(after))
        md.append({
            "ply": i + 1, "color": "white" if mover_white else "black",
            "cp_loss": cpl, "accuracy": acc,
            "classification": classify(cpl), "was_best": was_best,
            "phase": lichess_phase(i, board),
        })
        prev_cp, prev_pv = cp_after, pv_after
        if progress_cb: progress_cb(i + 1, len(moves))
    return md


def game_metrics(md, color):
    pm = [m for m in md if m["color"] == color]
    if not pm: return None
    accs = [m["accuracy"] for m in pm]
    weighted = mean(accs)
    harm = len(accs) / sum(1 / a for a in accs if a > 0)
    acc = (weighted + harm) / 2
    best = sum(1 for m in pm if m["classification"] == "best")
    bl = sum(1 for m in pm if m["classification"] == "blunder")
    mk = sum(1 for m in pm if m["classification"] == "mistake")
    ina = sum(1 for m in pm if m["classification"] == "inaccuracy")
    good = sum(1 for m in pm if m["classification"] == "good")
    by_phase = {}
    for p in ("opening", "middlegame", "endgame"):
        sub = [m for m in pm if m["phase"] == p]
        if sub:
            by_phase[p] = {"moves": len(sub),
                           "accuracy": round(mean(x["accuracy"] for x in sub), 1),
                           "acpl": round(mean(x["cp_loss"] for x in sub), 1)}
    return {
        "color": color, "moves": len(pm), "accuracy": round(acc, 1),
        "acpl": round(mean(m["cp_loss"] for m in pm), 1),
        "median_cpl": int(median(m["cp_loss"] for m in pm)),
        "cpl_std": round(stdev([m["cp_loss"] for m in pm]) if len(pm) > 1 else 0, 1),
        "best_move_rate": round(best / len(pm), 3),
        "good_move_rate": round(good / len(pm), 3),
        "blunder_rate": round(bl / len(pm), 3),
        "blunders": bl, "mistakes": mk, "inaccuracies": ina,
        "best_moves": best, "good_moves": good,
        "by_phase": by_phase,
    }


# ════════════════════════════════════════════════════════════════════════════
# 6. CACHE (with WAL for crash safety)
# ════════════════════════════════════════════════════════════════════════════
def cache_init():
    c = sqlite3.connect(str(CACHE_DB), check_same_thread=False, timeout=30)
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA synchronous=NORMAL")
    c.execute("""CREATE TABLE IF NOT EXISTS analysis (
        uid TEXT PRIMARY KEY, move_data TEXT,
        metrics_w TEXT, metrics_b TEXT)""")
    c.commit()
    return c

def cache_get(c, uid):
    r = c.execute("SELECT move_data, metrics_w, metrics_b FROM analysis WHERE uid=?",
                  (uid,)).fetchone()
    if not r: return None
    return (json.loads(r[0]),
            json.loads(r[1]) if r[1] else None,
            json.loads(r[2]) if r[2] else None)

def cache_put(c, uid, md, mw, mb):
    c.execute("INSERT OR REPLACE INTO analysis VALUES (?,?,?,?)",
              (uid, json.dumps(md), json.dumps(mw) if mw else None,
               json.dumps(mb) if mb else None))
    c.commit()


# ════════════════════════════════════════════════════════════════════════════
# 7. BASELINES
# ════════════════════════════════════════════════════════════════════════════
BASELINES = {
    "bullet": {1000: (68, .20, 110), 1200: (70, .23, 95), 1400: (72, .26, 82),
               1600: (74, .29, 70),  1800: (76, .32, 60), 2000: (78, .36, 52),
               2200: (80, .40, 45)},
    "blitz":  {1000: (70, .22, 100), 1200: (72.5, .25, 85), 1400: (75, .29, 72),
               1600: (77, .32, 60),  1800: (79, .36, 50),   2000: (81, .40, 43),
               2200: (83, .44, 37)},
    "rapid":  {1000: (73, .26, 85), 1200: (75.5, .30, 70), 1400: (77.5, .34, 58),
               1600: (79.5, .38, 48), 1800: (81.5, .42, 40), 2000: (83.5, .46, 34),
               2200: (85, .50, 29)},
}

def baseline_for(rating, tc):
    table = BASELINES.get(tc, BASELINES["blitz"])
    r = max(min(table), min(max(table), rating))
    keys = sorted(table)
    for i in range(len(keys) - 1):
        lo, hi = keys[i], keys[i + 1]
        if lo <= r <= hi:
            t = (r - lo) / (hi - lo)
            a, b = table[lo], table[hi]
            return tuple(a[j] + (b[j] - a[j]) * t for j in range(3))
    return table[keys[-1]]


# ════════════════════════════════════════════════════════════════════════════
# 8. SIGNALS
# ════════════════════════════════════════════════════════════════════════════
def performance_signal(agg, rating, tc):
    ba, bb, bacpl = baseline_for(rating, tc)
    da = agg["accuracy"] - ba
    db = (agg["best_move_rate"] - bb) * 100
    dc = (bacpl - agg["acpl"]) / bacpl * 100 if bacpl else 0
    n = (max(-1, min(1, da / 5)) + max(-1, min(1, db / 15)) +
         max(-1, min(1, dc / 20))) / 3
    return {"score": round(max(0, min(4.5, 2.25 + n * 2.25)), 2), "max": 4.5,
            "acc_delta": round(da, 1), "best_delta": round(db, 1),
            "acpl_delta": round(dc, 1),
            "baseline_acc": round(ba, 1), "baseline_acpl": round(bacpl, 1)}


def errors_signal(agg, rating, tc):
    exp = max(0.02, 0.15 - (rating - 800) * 0.00008)
    ratio = agg["blunder_rate"] / exp if exp else 1
    sc = max(0, min(3.0, 3.0 * (1 - (ratio - 0.6))))
    return {"score": round(sc, 2), "max": 3.0,
            "actual": round(agg["blunder_rate"] * 100, 2),
            "expected": round(exp * 100, 2)}


def consistency_signal(per_game):
    accs = [g["accuracy"] for g in per_game if g]
    if len(accs) < 3:
        return {"score": 2.5, "max": 2.5, "std": None}
    sd = pstdev(accs)
    return {"score": round(max(0, min(2.5, 2.5 * (1 - (sd - 2) / 13))), 2),
            "max": 2.5, "std": round(sd, 2)}


def timing_signal(games):
    instant = total = 0
    for g in games:
        try:
            pgn = chess.pgn.read_game(io.StringIO(g.pgn))
        except Exception: continue
        if not pgn: continue
        node = pgn; prev = None
        while node.variations:
            node = node.variations[0]
            clk = node.clock()
            if clk is not None and prev is not None:
                total += 1
                if prev - clk < 1.0: instant += 1
            prev = clk
    if total == 0:
        return {"score": 2.9, "max": 3.5, "note": "no clock data"}
    ratio = instant / total
    sc = max(0, min(3.5, 3.5 * (1 - (ratio - 0.10) / 0.40)))
    return {"score": round(sc, 2), "max": 3.5, "instant_ratio": round(ratio, 3)}


def opening_repertoire_signal(games, per_game, username):
    tally = {}
    for g, m in zip(games, per_game):
        if not m or not g.opening: continue
        key = g.opening[:40]
        if key not in tally:
            tally[key] = {"n": 0, "win": 0, "draw": 0, "loss": 0, "other": 0}
        d = tally[key]
        d["n"] += 1
        oc = m.get("outcome", "other")
        if oc in ("win", "draw", "loss"):
            d[oc] += 1
        else:
            d["other"] += 1
    if not tally:
        return {"score": 1.5, "max": 3.0, "top": []}
    top = sorted(tally.items(), key=lambda x: -x[1]["n"])[:3]
    scores = []
    for _, d in top:
        scores.append((d["win"] + 0.5 * d["draw"]) / d["n"])
    avg = mean(scores) if scores else 0.5
    sc = max(0, min(3.0, (avg - 0.3) / 0.4 * 3.0))
    return {"score": round(sc, 2), "max": 3.0,
            "top": [{"name": n, "n": d["n"],
                     "score": round((d["win"] + 0.5 * d["draw"]) / d["n"] * 100, 1)}
                    for n, d in top]}


def compute_signals(agg, per_game, games, rating, tc, username):
    perf = performance_signal(agg, rating, tc)
    err = errors_signal(agg, rating, tc)
    cons = consistency_signal(per_game)
    tim = timing_signal(games)
    rep = opening_repertoire_signal(games, per_game, username)
    strong = min(10.0, round(
        perf["score"] + err["score"] * 1.2 + cons["score"] +
        tim["score"] * 0.8 + rep["score"] * 0.7, 2))
    return {"performance": perf, "errors": err, "consistency": cons,
            "timing": tim, "repertoire": rep,
            "strong": {"score": strong, "max": 10.0}}


# ════════════════════════════════════════════════════════════════════════════
# 9. ANTI-CHEAT
# ════════════════════════════════════════════════════════════════════════════
def _player_color(g, username):
    u = username.lower()
    if g.white.lower() == u: return "white"
    if g.black.lower() == u: return "black"
    return None


def cheat_analysis(games, per_game, username, rating, time_class):
    flags = defaultdict(list)
    scores = {}

    if not per_game:
        return _cheat_result(scores, flags, rating, time_class)

    ba, bb, bacpl = baseline_for(rating, time_class)
    valid = [g for g in per_game if g]
    if not valid:
        return _cheat_result(scores, flags, rating, time_class)

    accs = [g["accuracy"] for g in valid]
    bests = [g["best_move_rate"] for g in valid]
    acpls = [g["acpl"] for g in valid]
    mean_acc = mean(accs)
    mean_best = mean(bests) * 100
    mean_acpl = mean(acpls)

    # A) Raw engine strength
    a = 0.0
    ad = mean_acc - ba
    bd = mean_best - bb * 100
    cd = (bacpl - mean_acpl) / bacpl * 100 if bacpl else 0
    if ad > 8:   a += min(0.5, (ad - 8) / 15)
    if bd > 15:  a += min(0.4, (bd - 15) / 25)
    if cd > 25:  a += min(0.4, (cd - 25) / 40)
    scores["Raw engine strength"] = min(1.0, a)
    if a > 0.2:
        flags["Raw engine strength"].append(
            f"acc {ad:+.1f} pts, best-move {bd:+.1f} pts, ACPL {cd:+.1f}% vs peers")

    # B) Selective assistance
    if len(accs) >= 5:
        sd = pstdev(accs)
        above = sum(1 for x in accs if x > ba + 10)
        frac_above = above / len(accs)
        top_n = max(1, len(accs) // 5)
        top_mean = mean(sorted(accs, reverse=True)[:top_n])
        b = 0.0
        if frac_above >= 0.3:
            b += min(0.6, (frac_above - 0.3) / 0.4)
        if top_mean - ba > 15:
            b += min(0.5, (top_mean - ba - 15) / 20)
        if sd < 3.0 and mean_acc > ba + 3:
            b += 0.3
        scores["Selective assistance"] = min(1.0, b)
        if b > 0.2:
            flags["Selective assistance"].append(
                f"{above}/{len(accs)} games ≥{ba+10:.0f}%; top-quintile avg "
                f"{top_mean:.1f}%; σ={sd:.1f}")

    # C) Cosmetic blunders
    cosmetic = total_bl = 0
    for g in games:
        color = _player_color(g, username)
        if not color: continue
        for m in g.move_data:
            if m["color"] != color: continue
            if m["classification"] == "blunder":
                total_bl += 1
                if m["cp_loss"] < 450:
                    cosmetic += 1
    if total_bl >= 4:
        ratio = cosmetic / total_bl
        c = max(0.0, (ratio - 0.65) / 0.30)
        scores["Cosmetic blunders"] = min(1.0, c)
        if c > 0.2:
            flags["Cosmetic blunders"].append(
                f"{cosmetic}/{total_bl} blunders in 300-450 CPL range "
                f"({ratio*100:.0f}%)")

    # D) Tactical precision
    sharp_best = sharp_tot = 0
    for g in games:
        color = _player_color(g, username)
        if not color: continue
        for m in g.move_data:
            if m["color"] != color: continue
            if m["phase"] in ("middlegame", "endgame"):
                sharp_tot += 1
                if m["was_best"]:
                    sharp_best += 1
    if sharp_tot > 30:
        rate = sharp_best / sharp_tot
        d = max(0.0, (rate - 0.55) / 0.25)
        scores["Tactical precision"] = min(1.0, d)
        if d > 0.2:
            flags["Tactical precision"].append(
                f"{rate*100:.1f}% engine top-choice in mid+endgame "
                f"(typical: 35-50%)")

    # E) Clock uniformity
    times = []
    for g in games:
        try:
            pgn = chess.pgn.read_game(io.StringIO(g.pgn))
        except Exception: continue
        if not pgn: continue
        node = pgn; prev = None
        while node.variations:
            node = node.variations[0]
            clk = node.clock()
            if clk is not None and prev is not None:
                spent = prev - clk
                if 0 <= spent < 600:
                    times.append(spent)
            prev = clk
    if len(times) >= 30:
        mu = mean(times); sd = stdev(times)
        cv = sd / mu if mu > 0 else 0
        e = max(0.0, (0.35 - cv) / 0.35)
        scores["Clock uniformity"] = min(1.0, e)
        if e > 0.2:
            flags["Clock uniformity"].append(
                f"think-time CV {cv:.2f} (humans > 0.8) — very uniform")

    # F) Rating gap
    implied_rating = rating + (bacpl - mean_acpl) * 4
    gap = implied_rating - rating
    f = max(0.0, min(1.0, (gap - 150) / 400))
    scores["Rating gap"] = f
    if f > 0.1:
        flags["Rating gap"].append(
            f"skill implies ~{int(implied_rating)} but rated {rating} "
            f"(gap {gap:+.0f})")

    return _cheat_result(scores, flags, rating, time_class)


def _cheat_result(scores, flags, rating, tc):
    if not scores:
        return {"classification": "Legit", "confidence": "Low",
                "severity": 0.0, "scores": {}, "strong_flags": [],
                "signals": [], "detail": {}}

    weights = {
        "Raw engine strength":   0.25,
        "Selective assistance":  0.30,
        "Cosmetic blunders":     0.15,
        "Tactical precision":    0.15,
        "Clock uniformity":      0.10,
        "Rating gap":            0.05,
    }
    total_w = sum(weights.get(k, 0.1) for k in scores)
    severity = sum(scores[k] * weights.get(k, 0.1) for k in scores) / total_w

    strong_flags = [k for k, v in scores.items() if v >= 0.3]

    if severity >= 0.60 or len(strong_flags) >= 3:
        cls = "Likely Cheater"
    elif severity >= 0.30 or len(strong_flags) >= 2:
        cls = "Maybe Cheater"
    else:
        cls = "Legit"

    if len(scores) >= 5:  conf = "High"
    elif len(scores) >= 3: conf = "Medium"
    else:                 conf = "Low"

    return {"classification": cls, "confidence": conf,
            "severity": round(severity, 2),
            "scores": {k: round(v, 2) for k, v in scores.items()},
            "strong_flags": strong_flags,
            "signals": list(scores.keys()), "detail": dict(flags)}


# ════════════════════════════════════════════════════════════════════════════
# 10. VERDICT
# ════════════════════════════════════════════════════════════════════════════
def verdict(agg, rating, tc):
    ba, _, _ = baseline_for(rating, tc)
    d = agg["accuracy"] - ba
    implied = int(round(rating + d * 25))
    n = agg["moves"]
    conf = "Low" if n < 150 else "Medium" if n < 400 else "High"
    if abs(d) < 1.5:
        note = f"Accuracy at the {tc} norm for this rating — normal range."
    elif d > 0:
        note = f"Accuracy sits +{d:.1f} pts above the {tc} norm — within normal range."
    else:
        note = f"Accuracy sits {d:.1f} pts below the {tc} norm."
    return {"plays_like": implied, "confidence": conf, "moves": n,
            "note": note, "delta": round(d, 1)}


# ════════════════════════════════════════════════════════════════════════════
# 11. UI
# ════════════════════════════════════════════════════════════════════════════
def banner():
    console.clear()
    txt = Text()
    txt.append("♞  CHESS PROFILE ANALYZER\n", style="bold cyan")
    txt.append("By VYNOZARELL?", style="italic magenta")
    txt.append("\n   Hidden Cheater? No problem!", style="dim")
    console.print(Panel(Align.center(txt), border_style="cyan", padding=(1, 4)))
    console.print()


def ask_inputs():
    cfg = load_config()
    site = Prompt.ask("[bold]Site[/]", choices=["chesscom", "lichess"],
                      default=cfg.get("site", "chesscom"))
    while True:
        username = Prompt.ask("[bold]Username[/]",
                              default=cfg.get("username") or None).strip()
        if username: break
        console.print("[red]Username required[/]")
    if site == "chesscom":
        n = IntPrompt.ask("[bold]Months back[/]", default=cfg.get("months", 2))
    else:
        n = IntPrompt.ask("[bold]Max games to fetch[/]", default=cfg.get("max_fetch", 40))
    max_games = IntPrompt.ask("[bold]Games to analyze (Stockfish)[/]",
                              default=cfg.get("max_games", 15))
    cfg.update({"site": site, "username": username, "months": n,
                "max_fetch": n, "max_games": max_games})
    save_config(cfg)
    return site, username, n, max_games


def bar_str(score, mx, width=12):
    filled = int(round(score / mx * width)) if mx else 0
    return "█" * filled + "░" * (width - filled)


def signal_color(score, mx):
    p = score / mx if mx else 0
    return "green" if p >= 0.75 else "yellow" if p >= 0.5 else "red"


def print_verdict_panel(v, sig, cheat):
    t = Text()
    t.append(f"Plays like a genuine ~{v['plays_like']}.\n", style="bold green")
    t.append(v["note"] + "\n", style="white")
    t.append(f"● {v['confidence']} confidence · {v['moves']} moves analyzed",
             style="dim cyan")
    console.print(Panel(t, title="[bold green]PERFORMANCE VERDICT[/]",
                        border_style="green"))
    console.print()

    cls = cheat["classification"]
    if cls == "Likely Cheater":   color, icon = "red", "⛔"
    elif cls == "Maybe Cheater":  color, icon = "yellow", "⚠"
    else:                         color, icon = "green", "✓"

    body = Text()
    body.append(f"{icon} {cls}\n", style=f"bold {color}")
    body.append(f"severity {cheat['severity']:.2f}  ·  "
                f"{cheat['confidence']} confidence\n", style="dim")
    if cheat.get("strong_flags"):
        body.append("Flagged: ", style="bold")
        body.append(", ".join(cheat["strong_flags"]), style=f"bold {color}")
    console.print(Panel(body, title="[bold]ANTI-CHEAT[/]", border_style=color))
    console.print()

    tbl = Table(title="[bold]SIGNALS[/]", border_style="cyan")
    tbl.add_column("Signal", style="bold white")
    tbl.add_column("Progress")
    tbl.add_column("Score", justify="right")
    tbl.add_column("Detail", style="dim")
    rep_top = sig["repertoire"]["top"][0]["score"] if sig["repertoire"]["top"] else 0
    rows = [
        ("Performance", sig["performance"]["score"], 4.5,
         f"acc {sig['performance']['acc_delta']:+.1f} pts"),
        ("Errors", sig["errors"]["score"], 3.0,
         f"blunders {sig['errors']['actual']}% (exp {sig['errors']['expected']}%)"),
        ("Consistency", sig["consistency"]["score"], 2.5,
         f"σ = {sig['consistency']['std']}" if sig["consistency"]["std"] else "n/a"),
        ("Timing", sig["timing"]["score"], 3.5,
         sig["timing"].get("note") or
         f"instant {sig['timing'].get('instant_ratio', 0)*100:.0f}%"),
        ("Repertoire", sig["repertoire"]["score"], 3.0,
         f"top opening {rep_top:.0f}%"),
        ("Strong", sig["strong"]["score"], 10.0, "weighted composite"),
    ]
    for name, sc, mx, detail in rows:
        c = signal_color(sc, mx)
        tbl.add_row(name, f"[{c}]{bar_str(sc, mx)}[/]",
                    f"[bold {c}]{sc:.1f} / {mx}[/]", detail)
    console.print(tbl)

    if cheat.get("scores"):
        tbl2 = Table(title="[bold red]ANTI-CHEAT BREAKDOWN[/]", border_style=color)
        tbl2.add_column("Anomaly")
        tbl2.add_column("Severity")
        tbl2.add_column("Evidence", style="dim")
        for k, val in sorted(cheat["scores"].items(), key=lambda x: -x[1]):
            c = "red" if val >= 0.6 else "yellow" if val >= 0.3 else "green"
            ev = "; ".join(cheat["detail"].get(k, []))[:70]
            tbl2.add_row(k, f"[{c}]{bar_str(val, 1.0, 10)} {val:.2f}[/]", ev)
        console.print(tbl2)


def print_profile_summary(agg, per_game, username):
    w = sum(1 for g in per_game if g and g["outcome"] == "win")
    d = sum(1 for g in per_game if g and g["outcome"] == "draw")
    l = sum(1 for g in per_game if g and g["outcome"] == "loss")
    tbl = Table(show_header=False, box=None, padding=(0, 2))
    tbl.add_column(style="dim cyan"); tbl.add_column(style="bold white")
    tbl.add_row("Player", username)
    tbl.add_row("Games", str(len(per_game)))
    tbl.add_row("W / D / L", f"[green]{w}[/] / [white]{d}[/] / [red]{l}[/]")
    tbl.add_row("Accuracy", f"[bold green]{agg['accuracy']:.1f}%[/]")
    tbl.add_row("Best moves", f"{agg['best_move_rate']*100:.1f}%")
    tbl.add_row("ACPL", f"{agg['acpl']:.1f}")
    tbl.add_row("Blunders", f"[red]{agg['blunders']}[/]")
    tbl.add_row("Mistakes", f"[yellow]{agg['mistakes']}[/]")
    tbl.add_row("Inaccuracies", f"{agg['inaccuracies']}")
    console.print(Panel(tbl, title="[bold]PROFILE[/]", border_style="cyan"))


def print_phase_table(agg):
    phases = agg.get("by_phase", {})
    if not phases: return
    tbl = Table(title="Accuracy by phase", border_style="cyan")
    tbl.add_column("Phase"); tbl.add_column("Moves", justify="right")
    tbl.add_column("Accuracy", justify="right"); tbl.add_column("ACPL", justify="right")
    for p, s in phases.items():
        tbl.add_row(p, str(s["moves"]),
                    f"[green]{s['accuracy']:.1f}%[/]", f"{s['acpl']:.1f}")
    console.print(tbl)


# ════════════════════════════════════════════════════════════════════════════
# 12. PARALLEL ANIMATED ANALYSIS
# ════════════════════════════════════════════════════════════════════════════
def pick_color(g, username):
    return _player_color(g, username)


def outcome_for(g, color):
    if g.result == "1/2-1/2": return "draw"
    if g.result == "1-0": return "win" if color == "white" else "loss"
    if g.result == "0-1": return "win" if color == "black" else "loss"
    return "?"


def animated_analysis(games_to_analyze, sf_path, cache, username):
    n_workers = max(1, min(len(games_to_analyze), (os.cpu_count() or 2) - 1))
    console.print(f"[cyan]⚙ Analyzing with {n_workers} parallel engine(s)[/]\n")

    state = {
        "active": {i: {"label": "", "ply": 0, "plies": 0} for i in range(n_workers)},
        "results": [], "done": 0, "total": len(games_to_analyze),
    }
    lock = threading.Lock()

    progress = Progress(
        SpinnerColumn(style="cyan"),
        TextColumn("[bold cyan]Analyzing"),
        BarColumn(bar_width=30, complete_style="green"),
        MofNCompleteColumn(), TextColumn("·"),
        TimeElapsedColumn(), TextColumn("·"), TimeRemainingColumn(),
        console=console,
    )
    task = progress.add_task("games", total=len(games_to_analyze))

    def make_panel():
        with lock:
            lines = Text()
            for i, s in state["active"].items():
                if not s["label"]:
                    lines.append(f"  w{i}  idle\n", style="dim"); continue
                pct = (s["ply"] / s["plies"] * 100) if s["plies"] else 0
                lines.append(f"  w{i}  ", style="magenta")
                lines.append(f"{s['label'][:36]:38s} ", style="white")
                lines.append(f"{s['ply']:>3}/{s['plies']:<3} ", style="dim")
                lines.append(f"{bar_str(pct, 100, 14)}\n", style="cyan")
            if state["results"]:
                lines.append("\n  Recent:\n", style="bold dim")
                for r in state["results"][-4:]:
                    w, b, acc, mvs, bl, _ = r
                    c = "green" if acc >= 80 else "yellow" if acc >= 70 else "red"
                    lines.append(
                        f"    [{c}]●[/] {w[:14]:14s} vs {b[:14]:14s} "
                        f"acc {acc:5.1f}%  {mvs:>3} mv  {bl} bl\n", style="white")
            return Panel(Group(progress,
                               Panel(lines, border_style="cyan",
                                     title=f"[bold]LIVE · {state['done']}/{state['total']}[/]")),
                         border_style="cyan")

    def worker(wid, games_slice):
        try:
            with Engine(sf_path, threads=1) as eng:
                for g in games_slice:
                    color = pick_color(g, username)
                    with lock:
                        state["active"][wid] = {
                            "label": f"{g.white} vs {g.black}", "ply": 0, "plies": 0}
                    def cb(ply, plies, w=wid):
                        with lock:
                            state["active"][w]["ply"] = ply
                            state["active"][w]["plies"] = plies
                    try:
                        g.move_data = analyze_game(g, eng, progress_cb=cb)
                        g.metrics_w = game_metrics(g.move_data, "white")
                        g.metrics_b = game_metrics(g.move_data, "black")
                        cache_put(cache, g.uid, g.move_data, g.metrics_w, g.metrics_b)
                    except Exception as e:
                        console.print(f"[red]✗ game failed:[/] {e}")
                    m = g.metrics_w if color == "white" else g.metrics_b
                    with lock:
                        if m:
                            state["results"].append(
                                (g.white, g.black, m["accuracy"], m["moves"],
                                 m["blunders"], g.url))
                        state["done"] += 1
                        state["active"][wid] = {"label": "", "ply": 0, "plies": 0}
                    progress.update(task, advance=1)
        except Exception:
            traceback.print_exc()

    slices = [[] for _ in range(n_workers)]
    for i, g in enumerate(games_to_analyze):
        slices[i % n_workers].append(g)

    with Live(make_panel(), console=console, refresh_per_second=8) as live:
        with cf.ThreadPoolExecutor(max_workers=n_workers) as pool:
            fs = [pool.submit(worker, i, s) for i, s in enumerate(slices)]
            while not all(f.done() for f in fs):
                live.update(make_panel()); time.sleep(0.1)
            for f in fs: f.result()
            live.update(make_panel())


# ════════════════════════════════════════════════════════════════════════════
# 13. AGGREGATE
# ════════════════════════════════════════════════════════════════════════════
def aggregate(per_game):
    valid = [m for m in per_game if m]
    if not valid:
        return {"moves": 0, "accuracy": 0, "acpl": 0, "best_move_rate": 0,
                "blunder_rate": 0, "blunders": 0, "mistakes": 0,
                "inaccuracies": 0, "best_moves": 0, "by_phase": {}}
    tot = sum(m["moves"] for m in valid)
    def wavg(k): return sum(m[k] * m["moves"] for m in valid) / tot
    phases = {}
    for p in ("opening", "middlegame", "endgame"):
        pm = [(m["by_phase"][p], m["moves"]) for m in valid
              if p in m.get("by_phase", {})]
        if pm:
            t = sum(x[1] for x in pm)
            phases[p] = {"moves": t,
                         "accuracy": round(sum(x[0]["accuracy"] * x[1] for x in pm) / t, 1),
                         "acpl": round(sum(x[0]["acpl"] * x[1] for x in pm) / t, 1)}
    return {"moves": tot, "accuracy": round(wavg("accuracy"), 1),
            "acpl": round(wavg("acpl"), 1),
            "median_cpl": int(median([m["median_cpl"] for m in valid])),
            "best_move_rate": round(wavg("best_move_rate"), 3),
            "blunder_rate": round(wavg("blunder_rate"), 3),
            "blunders": sum(m["blunders"] for m in valid),
            "mistakes": sum(m["mistakes"] for m in valid),
            "inaccuracies": sum(m["inaccuracies"] for m in valid),
            "best_moves": sum(m["best_moves"] for m in valid),
            "by_phase": phases}


def export_csv(username, games, per_game, out_dir: Path):
    rows = []
    for g, m in zip(games, per_game):
        color = _player_color(g, username)
        if not color: continue
        me = g.white if color == "white" else g.black
        opp = g.black if color == "white" else g.white
        my_r = g.white_rating if color == "white" else g.black_rating
        opp_r = g.black_rating if color == "white" else g.white_rating
        rows.append({
            "date": g.end_time.isoformat(),
            "site": g.site,
            "url": g.url,
            "time_class": g.time_class,
            "color": color,
            "me": me, "my_rating": my_r,
            "opponent": opp, "opp_rating": opp_r,
            "result": g.result,
            "outcome": outcome_for(g, color),
            "opening": g.opening or "",
            "moves": m["moves"] if m else 0,
            "accuracy": m["accuracy"] if m else 0,
            "acpl": m["acpl"] if m else 0,
            "best_moves": m["best_moves"] if m else 0,
            "best_move_rate": m["best_move_rate"] if m else 0,
            "blunders": m["blunders"] if m else 0,
            "mistakes": m["mistakes"] if m else 0,
            "inaccuracies": m["inaccuracies"] if m else 0,
        })
    import csv
    out = out_dir / f"{_safe_name(username)}_games.csv"
    with open(out, "w", newline="", encoding="utf-8") as f:
        if rows:
            w = csv.DictWriter(f, fieldnames=rows[0].keys())
            w.writeheader(); w.writerows(rows)
    return out


# ════════════════════════════════════════════════════════════════════════════
# 14. CHART
# ════════════════════════════════════════════════════════════════════════════
def plot_report(username, games, per_game, outfile):
    fig, axes = plt.subplots(2, 2, figsize=(13, 8), facecolor="#0b0f19")
    fig.suptitle(f"Chess profile — {username}", fontsize=14, color="#e6edf7")
    for ax in axes.flat:
        ax.set_facecolor("#121826")
        for sp in ax.spines.values(): sp.set_color("#1e2739")
        ax.tick_params(colors="#8b9ab4")
        ax.xaxis.label.set_color("#8b9ab4"); ax.yaxis.label.set_color("#8b9ab4")
        ax.title.set_color("#e6edf7")

    accs = [g["accuracy"] if g else 0 for g in per_game]
    outs = [g.get("outcome", "?") if g else "?" for g in per_game]
    cmap = {"win": "#4caf50", "loss": "#f44336", "draw": "#9e9e9e"}

    ax = axes[0, 0]
    ax.bar(range(len(accs)), accs, color=[cmap.get(o, "#888") for o in outs])
    ax.set_title("Accuracy per game"); ax.set_ylabel("%")
    if accs:
        ax.axhline(mean(accs), ls="--", color="#22d3ee", alpha=.7,
                   label=f"mean {mean(accs):.1f}")
        ax.legend(facecolor="#121826", edgecolor="#1e2739")

    ax = axes[0, 1]
    phases = ["opening", "middlegame", "endgame"]
    vals = []
    for p in phases:
        sub = [g["by_phase"][p]["accuracy"] for g in per_game
               if g and p in g.get("by_phase", {})]
        vals.append(mean(sub) if sub else 0)
    ax.bar(phases, vals, color=["#2196f3", "#ff9800", "#9c27b0"])
    ax.set_ylim(0, 100); ax.set_title("Accuracy by phase")
    for i, v in enumerate(vals):
        ax.text(i, v + 1, f"{v:.1f}%", ha="center", color="#e6edf7")

    ax = axes[1, 0]
    cats = ["best", "good", "inaccuracy", "mistake", "blunder"]
    counts = Counter()
    for g in games:
        for m in g.move_data: counts[m["classification"]] += 1
    tot = sum(counts.values()) or 1
    ax.bar(cats, [counts[c] / tot * 100 for c in cats],
           color=["#4caf50", "#8bc34a", "#ffc107", "#ff9800", "#f44336"])
    ax.set_title("Move classification")

    ax = axes[1, 1]
    if games:
        ax.plot([g.end_time for g in games], [g.white_rating for g in games],
                "o-", label="white", alpha=.7)
        ax.plot([g.end_time for g in games], [g.black_rating for g in games],
                "s-", label="black", alpha=.7)
        ax.set_title("Ratings over games")
        ax.legend(facecolor="#121826", edgecolor="#1e2739")
        ax.tick_params(axis="x", rotation=30)

    plt.tight_layout()
    plt.savefig(outfile, dpi=120, facecolor="#0b0f19")
    plt.close(fig)
    console.print(f"[green]✓[/] Saved chart → [bold]{outfile}[/]")


# ════════════════════════════════════════════════════════════════════════════
# 15. HTML DASHBOARD
# ════════════════════════════════════════════════════════════════════════════
HTML_TPL = string.Template(r"""<!doctype html>
<html><head><meta charset="utf-8"><title>$username · Chess Profile</title>
<style>
:root { --bg:#0b0f19; --card:#121826; --line:#1e2739; --txt:#e6edf7; --dim:#8b9ab4;
--green:#4ade80; --yellow:#fbbf24; --red:#f87171; --blue:#3b82f6; --cyan:#22d3ee; }
* { box-sizing:border-box; }
body { background:var(--bg); color:var(--txt); margin:0; padding:32px 20px;
  font-family: ui-sans-serif, -apple-system, "Segoe UI", Roboto, sans-serif; }
.wrap { max-width:960px; margin:0 auto; }
.card { background:var(--card); border:1px solid var(--line); border-radius:14px;
  padding:22px 26px; margin-bottom:20px; }
h1 { font-size:22px; margin:0 0 4px; }
.dim { color:var(--dim); font-size:13px; }
.kpis { display:flex; gap:36px; flex-wrap:wrap; margin-top:14px; }
.kpi .v { font-size:26px; font-weight:700; }
.kpi .l { color:var(--dim); font-size:11px; letter-spacing:.08em; text-transform:uppercase; }
.verdict { display:flex; align-items:center; gap:24px; }
.circle { width:120px; height:120px; border-radius:50%;
  background: conic-gradient(var(--green) $circle_pct%, #1e2739 0);
  display:flex; align-items:center; justify-content:center; position:relative; flex:0 0 auto; }
.circle::after { content:""; position:absolute; inset:8px; border-radius:50%;
  background:var(--card); }
.circle span { position:relative; font-size:26px; font-weight:700; z-index:1; }
.sig { display:grid; grid-template-columns:140px 1fr 90px; gap:14px;
  align-items:center; padding:12px 14px; border:1px solid var(--line);
  border-radius:10px; }
.bar { height:8px; background:#1e2739; border-radius:4px; overflow:hidden; }
.bar > i { display:block; height:100%; }
.score { text-align:right; font-weight:600; }
table { width:100%; border-collapse:collapse; margin-top:8px; }
th, td { padding:9px 10px; text-align:left; border-bottom:1px solid var(--line); font-size:13px; }
th { color:var(--dim); font-weight:500; font-size:11px; letter-spacing:.06em;
  text-transform:uppercase; }
.pill { display:inline-block; padding:3px 9px; border-radius:999px; font-size:11px;
  background:#1e2739; color:var(--dim); margin-right:6px; }
img { width:100%; border-radius:10px; margin-top:8px; }
.cheat-card { border:1px solid var(--$cheat_color); }
.cheat-title { color:var(--$cheat_color); font-size:22px; font-weight:700; }
</style></head><body><div class="wrap">

<div class="card">
  <div style="display:flex; justify-content:space-between; align-items:flex-start; flex-wrap:wrap; gap:16px;">
    <div>
      <h1>$username</h1>
      <div class="dim">$site · $n_games games · $time_class</div>
    </div>
    <div class="kpis">
      <div class="kpi"><div class="v">$acc</div><div class="l">Accuracy</div></div>
      <div class="kpi"><div class="v">$winrate</div><div class="l">Win rate</div></div>
      <div class="kpi"><div class="v">$best_rate</div><div class="l">Best moves</div></div>
      <div class="kpi"><div class="v">$acpl</div><div class="l">ACPL</div></div>
    </div>
  </div>
  <div style="margin-top:14px;">
    <span class="pill">♟ $time_class</span>
    <span class="pill">rating ~$rating</span>
    <span class="pill">$n_moves moves analyzed</span>
  </div>
</div>

<div class="card cheat-card">
  <div class="dim" style="margin-bottom:8px;">ANTI-CHEAT VERDICT</div>
  <div class="cheat-title">$cheat_icon $cheat_class</div>
  <div class="dim" style="margin-top:6px;">
    severity $cheat_severity · $cheat_confidence confidence · $n_strong strong signals flagged
  </div>
  $cheat_flags_html
  $cheat_breakdown_html
</div>

<div class="card verdict">
  <div class="circle"><span>$strong_score</span></div>
  <div>
    <h1 style="color:var(--green);">Plays like a genuine ~$plays_like.</h1>
    <div>$verdict_note</div>
    <div class="dim" style="margin-top:6px;">● $confidence confidence · $n_moves moves across $n_games $time_class games</div>
  </div>
</div>

<div class="card">
  <div class="dim" style="margin-bottom:14px;">SIGNALS · thresholds scaled to ~$plays_like · $time_class</div>
  <div>$signal_rows</div>
</div>

<div class="card">
  <div class="dim" style="margin-bottom:12px;">Accuracy by phase</div>
  <table><tr><th>Phase</th><th>Moves</th><th>Accuracy</th><th>ACPL</th></tr>
  $phase_rows</table>
</div>

<div class="card">
  <div class="dim" style="margin-bottom:8px;">Per-game accuracy (green=win, red=loss, gray=draw)</div>
  <img src="$chart_data_uri" alt="chart"/>
</div>

<div class="card">
  <div class="dim" style="margin-bottom:8px;">Top openings</div>
  <table><tr><th>Opening</th><th>Games</th><th>Score</th></tr>
  $opening_rows</table>
</div>

<div class="card dim" style="text-align:center;">
  Generated by Chess Profile Analyzer v7 · $timestamp
</div>

</div></body></html>
""")


def _img_data_uri(path):
    try:
        data = Path(path).read_bytes()
        b64 = base64.b64encode(data).decode("ascii")
        return f"data:image/png;base64,{b64}"
    except Exception:
        return Path(path).name


def write_html(username, site, games, per_game, agg, sig, verd, cheat, rating,
               time_class, chart_path, out_dir: Path):
    signal_rows = []
    signals = [
        ("Performance", sig["performance"]["score"], sig["performance"]["max"],
         f"acc {sig['performance']['acc_delta']:+.1f} pts vs baseline"),
        ("Errors", sig["errors"]["score"], sig["errors"]["max"],
         f"blunders {sig['errors']['actual']}% (expected {sig['errors']['expected']}%)"),
        ("Consistency", sig["consistency"]["score"], sig["consistency"]["max"],
         f"σ = {sig['consistency']['std']}" if sig['consistency']['std'] else "n/a"),
        ("Timing", sig["timing"]["score"], sig["timing"]["max"],
         f"instant {sig['timing'].get('instant_ratio', 0)*100:.0f}%"
         if "instant_ratio" in sig["timing"] else sig["timing"].get("note", "")),
        ("Repertoire", sig["repertoire"]["score"], sig["repertoire"]["max"],
         "avg score in top openings"),
        ("Strong", sig["strong"]["score"], sig["strong"]["max"],
         "weighted composite"),
    ]
    for name, sc, mx, detail in signals:
        pct = sc / mx * 100 if mx else 0
        c = "var(--green)" if pct >= 75 else "var(--yellow)" if pct >= 50 else "var(--red)"
        signal_rows.append(f"""<div class="sig">
            <div>{name}</div>
            <div><div class="bar"><i style="width:{pct:.0f}%; background:{c}"></i></div>
                 <div class="dim" style="font-size:11px; margin-top:5px;">{detail}</div></div>
            <div class="score" style="color:{c}">{sc:.1f}/{mx}</div></div>""")
    signal_rows = "\n".join(signal_rows)

    phase_rows = ""
    for p, s in agg.get("by_phase", {}).items():
        phase_rows += (f"<tr><td>{p}</td><td>{s['moves']}</td>"
                       f"<td style='color:var(--green)'>{s['accuracy']:.1f}%</td>"
                       f"<td>{s['acpl']:.1f}</td></tr>")

    op_tally = Counter(); op_score = {}
    for g, m in zip(games, per_game):
        if not m or not g.opening: continue
        op_tally[g.opening] += 1
        op_score.setdefault(g.opening, []).append(
            1 if m["outcome"] == "win" else 0.5 if m["outcome"] == "draw" else 0)
    opening_rows = ""
    for op, n in op_tally.most_common(8):
        sc = mean(op_score[op]) * 100
        opening_rows += (f"<tr><td>{op[:70]}</td><td>{n}</td>"
                         f"<td style='color:var(--green)'>{sc:.0f}%</td></tr>")

    cls = cheat["classification"]
    if cls == "Likely Cheater":   cheat_color, icon = "red", "⛔"
    elif cls == "Maybe Cheater":  cheat_color, icon = "yellow", "⚠"
    else:                         cheat_color, icon = "green", "✓"

    if cheat.get("strong_flags"):
        flags_html = ("<div style='margin-top:12px;'><b>Flagged signals:</b> "
                      + ", ".join(cheat["strong_flags"]) + "</div>")
    else:
        flags_html = ("<div style='margin-top:12px; color:var(--green);'>"
                      "No significant anomalies detected.</div>")

    breakdown_rows = ""
    for k, v in sorted((cheat.get("scores") or {}).items(), key=lambda x: -x[1]):
        c = "var(--red)" if v >= 0.6 else "var(--yellow)" if v >= 0.3 else "var(--green)"
        ev = "; ".join(cheat["detail"].get(k, []))[:120]
        breakdown_rows += (
            f"<tr><td>{k}</td>"
            f"<td style='color:{c}; font-weight:600'>{v:.2f}</td>"
            f"<td class='dim'>{ev}</td></tr>")
    breakdown_html = ""
    if breakdown_rows:
        breakdown_html = ("<table style='margin-top:14px;'><tr><th>Anomaly</th>"
                          "<th>Severity</th><th>Evidence</th></tr>"
                          + breakdown_rows + "</table>")

    wins = sum(1 for m in per_game if m and m["outcome"] == "win")
    n_games = len(per_game)
    winrate = f"{wins / n_games * 100:.0f}%" if n_games else "—"
    best_rate = f"{agg['best_move_rate']*100:.0f}%"

    html = HTML_TPL.safe_substitute(
        username=username, site=site, n_games=n_games, time_class=time_class,
        acc=f"{agg['accuracy']:.1f}%", winrate=winrate, best_rate=best_rate,
        acpl=f"{agg['acpl']:.1f}", rating=rating, n_moves=agg["moves"],
        plays_like=verd["plays_like"], confidence=verd["confidence"],
        verdict_note=verd["note"],
        circle_pct=int(sig["strong"]["score"] / sig["strong"]["max"] * 100)
                   if sig["strong"]["max"] else 0,
        strong_score=f"{sig['strong']['score']:.1f}",
        signal_rows=signal_rows, phase_rows=phase_rows,
        opening_rows=opening_rows, chart_data_uri=_img_data_uri(chart_path),
        cheat_class=cls, cheat_color=cheat_color, cheat_icon=icon,
        cheat_severity=f"{cheat['severity']:.2f}",
        cheat_confidence=cheat["confidence"],
        n_strong=len(cheat.get("strong_flags", [])),
        cheat_flags_html=flags_html, cheat_breakdown_html=breakdown_html,
        timestamp=datetime.now().strftime("%Y-%m-%d %H:%M"))
    out = out_dir / f"{_safe_name(username)}_profile.html"
    out.write_text(html, encoding="utf-8")
    return out


# ════════════════════════════════════════════════════════════════════════════
# 16. MAIN
# ════════════════════════════════════════════════════════════════════════════
def main():
    banner()
    site, username, months_or_max, max_games = ask_inputs()

    # Create results folder early so failed runs still show where outputs go
    out_dir = result_dir(username, site)
    console.print(f"[cyan]📁 Results folder:[/] [bold]{out_dir}[/]\n")

    with console.status(f"[cyan]Fetching {site} games for {username}…",
                        spinner="dots"):
        try:
            if site == "chesscom":
                prof = chesscom_profile(username)
                raw = chesscom_games(username, months_or_max)
                games = normalize_chesscom(raw)
            else:
                prof = lichess_profile(username)
                raw = lichess_games(username, months_or_max)
                games = normalize_lichess(raw)
        except Exception as e:
            console.print(f"[red]✗ Fetch failed:[/] {e}"); return

    games = [g for g in games if pick_color(g, username)]
    games.sort(key=lambda g: g.end_time, reverse=True)
    games = games[:max_games]
    console.print(f"[green]✓[/] Fetched [bold]{len(games)}[/] games\n")
    if not games:
        console.print("[red]No games found.[/]"); return

    cache = cache_init()
    cached, fresh = [], []
    for g in games:
        hit = cache_get(cache, g.uid)
        if hit:
            g.move_data, g.metrics_w, g.metrics_b = hit
            cached.append(g)
        else:
            fresh.append(g)
    if cached:
        console.print(f"[dim]✓ {len(cached)} game(s) loaded from cache[/]")

    sf = _which_stockfish()
    if not sf:
        console.print("[yellow]⚠ Stockfish not found — installing…[/]")
        sf = install_stockfish()
        if not sf:
            console.print("[red]✗ Install failed. Install manually:[/]")
            console.print("  · https://stockfishchess.org/download/")
            console.print("  · Or set STOCKFISH_PATH=/path/to/stockfish")
            return

    try:
        with Engine(sf) as _e:
            _e.analyse(chess.Board())
        console.print(f"[green]✓[/] Stockfish: [bold]{sf}[/]  ·  depth {ANALYSIS_DEPTH}\n")
    except Exception as e:
        console.print(f"[red]✗ Stockfish unusable:[/] {e}"); return

    if fresh:
        animated_analysis(fresh, sf, cache, username)
        console.print()

    per_game = []
    for g in games:
        color = pick_color(g, username)
        m = g.metrics_w if color == "white" else g.metrics_b
        if m:
            m = dict(m)
            m["outcome"] = outcome_for(g, color)
            m["opponent"] = g.black if color == "white" else g.white
            m["rating"] = g.white_rating if color == "white" else g.black_rating
            m["opp_rating"] = g.black_rating if color == "white" else g.white_rating
            m["url"] = g.url
            per_game.append(m)
        else:
            per_game.append(None)

    agg = aggregate(per_game)
    ratings = [g.white_rating if pick_color(g, username) == "white"
               else g.black_rating for g in games]
    avg_rating = int(mean(ratings)) if ratings else 1500
    tc = Counter(g.time_class for g in games).most_common(1)[0][0] if games else "blitz"

    sig = compute_signals(agg, per_game, games, avg_rating, tc, username)
    v = verdict(agg, avg_rating, tc)
    cheat = cheat_analysis(games, per_game, username, avg_rating, tc)

    console.rule("[bold cyan]REPORT[/]"); console.print()
    print_profile_summary(agg, per_game, username); console.print()
    print_verdict_panel(v, sig, cheat); console.print()
    print_phase_table(agg)

    # ── all outputs go into results/<username>_<site>/
    chart = out_dir / f"{_safe_name(username)}_profile.png"
    plot_report(username, games, per_game, chart)

    try:
        html = write_html(username, site, games, per_game, agg, sig, v, cheat,
                          avg_rating, tc, chart, out_dir)
        console.print(f"[green]✓[/] Saved HTML → [bold]{html}[/]")
    except Exception as e:
        console.print(f"[yellow]⚠ HTML generation failed:[/] {e}")
        traceback.print_exc()

    try:
        csv_path = export_csv(username, games, per_game, out_dir)
        console.print(f"[green]✓[/] Saved CSV  → [bold]{csv_path}[/]")
    except Exception as e:
        console.print(f"[yellow]⚠ CSV export failed:[/] {e}")

    jf = out_dir / f"{_safe_name(username)}_profile.json"
    with open(jf, "w") as f:
        json.dump({"username": username, "site": site, "rating": avg_rating,
                   "time_class": tc, "aggregate": agg, "signals": sig,
                   "verdict": v, "cheat": cheat}, f, indent=2, default=str)
    console.print(f"[green]✓[/] Saved JSON → [bold]{jf}[/]\n")

    console.print(f"[bold cyan]📁 All results in:[/] {out_dir.resolve()}\n")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted — progress saved to cache.db[/]")
        sys.exit(0)
    except Exception:
        console.print("[red]Fatal error:[/]")
        traceback.print_exc()
        sys.exit(1)