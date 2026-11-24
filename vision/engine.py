"""Stockfish for the check of the user's own move: does the move lose material to a reply that is not a capture
right now (a fork, a pin, a trapped piece, a mating attack)? The rule (vision/threats.py) sees captures; the engine
sees the rest. One engine, opened on first use and shared under a lock.
"""

import threading

import chess
import chess.engine

from . import config

LIMIT = chess.engine.Limit(nodes=200_000)
LOSS = 150  # centipawns: a move that loses at least this much against the best one has a tactical refutation
_lock = threading.Lock()
_engine: chess.engine.SimpleEngine | None = None


def _open() -> chess.engine.SimpleEngine | None:
    global _engine
    if _engine is None:
        path = config.stockfish()
        if not path:
            return None
        engine = chess.engine.SimpleEngine.popen_uci(path)
        engine.configure({"Threads": 2, "Hash": 64})
        _engine = engine
    return _engine


def refutation(before: chess.Board, move: chess.Move) -> tuple[chess.Move, int] | None:
    """(the opponent's best reply, centipawns the user's move loses against his best move), when the move loses at
    least LOSS and the position after it is not still clearly won; None otherwise or without Stockfish."""
    me = before.turn
    with _lock:
        engine = _open()
        if engine is None:
            return None
        best = engine.analyse(before, LIMIT)["score"].pov(me).score(mate_score=10_000) or 0
        after = before.copy(stack=False)
        after.push(move)
        if after.is_game_over():
            return None
        info = engine.analyse(after, LIMIT)
    now = info["score"].pov(me).score(mate_score=10_000) or 0
    pv = info.get("pv") or []
    if not pv or best - now < LOSS or now >= 500:
        return None
    return pv[0], best - now


def close() -> None:
    global _engine
    with _lock:
        if _engine is not None:
            try:
                _engine.quit()
            except chess.engine.EngineError, OSError:
                pass
            _engine = None
