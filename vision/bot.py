"""The opponent in a series: Maia 1300 — a net that plays like a 1300 Lichess player, not like an engine.

Maia (https://maiachess.com, CSSLab, GPL-3.0) gives every legal move the probability that a player of its level
plays it; the bot picks a move by those probabilities, so it varies, makes human mistakes and punishes human ones.
Runs on lc0 with the weights «Шахматный тренер» already has (ChessCoach/maia/maia-1300.pb.gz) — nothing to set up.
Without lc0 or the weights the bot falls back to a weak Stockfish move.
"""

import random
import re
import shutil
import subprocess
import threading
from pathlib import Path

import chess
import chess.engine

from . import config

WEIGHTS = config.COACH / "maia" / "maia-1300.pb.gz"
_P = re.compile(r"^(\S+)\s+\(\s*\d+\s*\)\s+N:.*\(P:\s*([\d.]+)%\)")
_lock = threading.Lock()
_engine: chess.engine.SimpleEngine | None = None
_kind = ""


def _lc0() -> str | None:
    for path in (shutil.which("lc0"), "/opt/homebrew/bin/lc0", "/usr/local/bin/lc0"):
        if path and Path(path).exists():
            return path
    return None


def _open() -> chess.engine.SimpleEngine:
    global _engine, _kind
    if _engine is None:
        exe = _lc0()
        if exe and WEIGHTS.exists():
            engine = chess.engine.SimpleEngine.popen_uci(exe, stderr=subprocess.DEVNULL)
            # temperature 1: the net's own probabilities (lc0 flattens them to widen its search by default)
            engine.configure({"WeightsFile": str(WEIGHTS), "VerboseMoveStats": True, "PolicyTemperature": 1.0})
            _kind = "maia"
        else:
            sf = config.stockfish()
            if not sf:
                raise RuntimeError("нет ни Maia (lc0 + веса), ни Stockfish")
            engine = chess.engine.SimpleEngine.popen_uci(sf)
            engine.configure({"Skill Level": 3})
            _kind = "stockfish"
        _engine = engine
    return _engine


def policy(board: chess.Board) -> dict[str, float]:
    """uci -> probability that a 1300 player plays it (empty with the Stockfish fallback)."""
    engine = _open()
    if _kind != "maia":
        return {}
    out = {}
    with engine.analysis(board, chess.engine.Limit(nodes=1)) as a:
        for info in a:
            m = _P.match(info.get("string", ""))
            if m and m[1] != "node":
                out[m[1]] = float(m[2]) / 100
    return out


def move(board: chess.Board, rng: random.Random | None = None) -> chess.Move:
    """The bot's move in `board` (with its move stack: Maia looks at the last positions)."""
    rng = rng or random.Random()
    with _lock:
        p = policy(board)
        legal = {m.uci(): m for m in board.legal_moves}
        choices = [(legal[u], w) for u, w in p.items() if u in legal and w > 0]
        if choices:
            moves, weights = zip(*choices)
            return rng.choices(moves, weights=weights, k=1)[0]
        return _open().play(board, chess.engine.Limit(time=0.1, nodes=2000)).move or next(iter(board.legal_moves))


def close() -> None:
    global _engine
    with _lock:
        if _engine is not None:
            try:
                _engine.quit()
            except chess.engine.EngineError, OSError:
                pass
            _engine = None
