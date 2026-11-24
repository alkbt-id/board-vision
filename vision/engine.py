"""Stockfish for the check of the user's own move: does the move lose material to a reply that is not a capture
right now (a fork, a pin, a trapped piece, a mating attack)? The rule (vision/threats.py) sees captures; the engine
sees the rest. One engine, opened on first use and shared under a lock.
"""

import threading

import chess
import chess.engine

from . import config

LIMIT = chess.engine.Limit(nodes=200_000)
LOSS = 150  # centipawns: a move that loses at least this much against the best one may have a tactical refutation
PLIES = 4  # …and it is a tactic only if the engine's line wins material within this many half-moves (or mates)
VALUE = {chess.PAWN: 1, chess.KNIGHT: 3, chess.BISHOP: 3, chess.ROOK: 5, chess.QUEEN: 9, chess.KING: 0}


def _material(board: chess.Board, color: chess.Color) -> int:
    return sum(VALUE[p.piece_type] * (1 if p.color == color else -1) for p in board.piece_map().values())
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
    """(the opponent's best reply, centipawns the user's move loses against his best move) when the move loses at
    least LOSS, the position after it is not still clearly won, and the loss is a tactic: the engine's line mates
    or wins at least 2 pawns of material within PLIES half-moves. A positional loss (a weakened king, the centre
    given up) is not something to draw an arrow for. None otherwise or without Stockfish."""
    global _engine
    me = before.turn
    after = before.copy(stack=False)
    after.push(move)
    if after.is_game_over():
        return None
    with _lock:
        engine = _open()
        if engine is None:
            return None
        try:
            best = engine.analyse(before, LIMIT)["score"].pov(me).score(mate_score=10_000) or 0
            info = engine.analyse(after, LIMIT)
        except chess.engine.EngineError, chess.engine.EngineTerminatedError, OSError:
            _engine = None  # the check goes on without the engine; a new one is opened next time
            return None
    score = info["score"].pov(me)
    now = score.score(mate_score=10_000) or 0
    pv = info.get("pv") or []
    if not pv or best - now < LOSS or now >= 500:
        return None
    if not (score.is_mate() and (score.mate() or 0) < 0):  # not a mate: the line must win material
        line = after.copy(stack=False)
        start = _material(line, me)
        lowest = start
        for m in pv[:PLIES]:
            if m not in line.legal_moves:
                break
            line.push(m)
            lowest = min(lowest, _material(line, me)) if len(line.move_stack) % 2 == 0 else lowest
        if start - _material(line, me) < 2 and start - lowest < 2:
            return None
    return pv[0], best - now


def evaluate(board: chess.Board, pov: chess.Color) -> int | None:
    """Stockfish's evaluation of `board` for `pov` in centipawns (a mate is ±10000); None without the engine."""
    global _engine
    if board.is_game_over():
        return None
    with _lock:
        engine = _open()
        if engine is None:
            return None
        try:
            info = engine.analyse(board, LIMIT)
        except chess.engine.EngineError, chess.engine.EngineTerminatedError, OSError:
            _engine = None
            return None
    return info["score"].pov(pov).score(mate_score=10_000)


def close() -> None:
    global _engine
    with _lock:
        if _engine is not None:
            try:
                _engine.quit()
            except chess.engine.EngineError, OSError:
                pass
            _engine = None
