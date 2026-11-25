"""Maia nets of every level, for the rating of a task on the scale of game ratings (vision/tasks.rating_of).

Maia (https://maiachess.com, CSSLab, GPL-3.0) is a family of nets trained on Lichess games of one rating band each,
1100 to 1900: the policy of maia-1500 is the probability that a 1500 player plays each move. So the share of the
policy on the right moves of a task is how often a player of that level finds the answer in a game.

The nets live in the data folder (maia/, vision/config.py); a missing one is downloaded from CSSLab's GitHub on first
use (about 1 MB each). One lc0 process of its own (not the bot's, vision/bot.py) switches between them; it is used by
the background preparation of tasks only, under a lock.
"""

import re
import shutil
import subprocess
import threading
from pathlib import Path

import chess
import chess.engine

from . import config
from .sources import net

LEVELS = (1100, 1200, 1300, 1400, 1500, 1600, 1700, 1800, 1900)
URL = "https://raw.githubusercontent.com/CSSLab/maia-chess/master/maia_weights/maia-{level}.pb.gz"
DIR = config.HOME / "maia"
_P = re.compile(r"^(\S+)\s+\(\s*\d+\s*\)\s+N:.*\(P:\s*([\d.]+)%\)")
_lock = threading.Lock()
_engine: chess.engine.SimpleEngine | None = None
_loaded = 0


def _lc0() -> str | None:
    for path in (shutil.which("lc0"), "/opt/homebrew/bin/lc0", "/usr/local/bin/lc0"):
        if path and Path(path).exists():
            return path
    return None


def weights(level: int) -> Path | None:
    """The net of `level`, downloaded when missing; the coach's maia-1300 is used when it is there. None when it
    cannot be had."""
    path = DIR / f"maia-{level}.pb.gz"
    if path.exists():
        return path
    coach = config.COACH / "maia" / path.name
    if coach.exists():
        return coach
    DIR.mkdir(parents=True, exist_ok=True)
    try:
        with net.client() as c:
            r = c.get(URL.format(level=level))
            r.raise_for_status()
        tmp = path.with_suffix(".part")
        tmp.write_bytes(r.content)
        tmp.replace(path)
    except Exception:  # noqa: BLE001 — no net: the levels that are there are used
        return None
    return path


def available() -> list[int]:
    return [lv for lv in LEVELS if weights(lv)]


def _open() -> chess.engine.SimpleEngine | None:
    global _engine
    if _engine is None:
        exe = _lc0()
        if not exe:
            return None
        engine = chess.engine.SimpleEngine.popen_uci(exe, stderr=subprocess.DEVNULL)
        engine.configure({"VerboseMoveStats": True, "PolicyTemperature": 1.0, "NNCacheSize": 0})  # no answers of the last net
        _engine = engine
    return _engine


def policy(board: chess.Board, level: int) -> dict[str, float]:
    """uci -> probability that a player of `level` plays it in `board` (with its move stack: Maia looks at the last
    positions). Empty without lc0 or that net."""
    global _engine, _loaded
    path = weights(level)
    if path is None:
        return {}
    with _lock:
        engine = _open()
        if engine is None:
            return {}
        try:
            if _loaded != level:
                engine.configure({"WeightsFile": str(path)})
                _loaded = level
            out = {}
            with engine.analysis(board, chess.engine.Limit(nodes=1), game=object()) as a:  # a new game: no tree kept
                for info in a:
                    m = _P.match(info.get("string", ""))
                    if m and m[1] != "node":
                        out[m[1]] = float(m[2]) / 100
            return out
        except (chess.engine.EngineError, chess.engine.EngineTerminatedError, OSError):
            _engine, _loaded = None, 0
            return {}


def close() -> None:
    global _engine, _loaded
    with _lock:
        if _engine is not None:
            try:
                _engine.quit()
            except (chess.engine.EngineError, OSError):
                pass
            _engine, _loaded = None, 0
