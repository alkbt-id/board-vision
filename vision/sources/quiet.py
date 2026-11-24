"""Stockfish's check of the positions where the rule finds no threat.

A position is truly quiet when giving the move back to the opponent (a null move) costs the user little: the
evaluation with the user to move and with the opponent to move differ by less than THRESHOLD centipawns. When it
costs more, the opponent has a threat the rule does not see (a quiet mate, a trap, a combination): such a position
is never shown as quiet (quiet_ok = 0). Lopsided positions (one side far ahead) are left out too.
"""

import chess
import chess.engine

from .. import config, store
from . import say

THRESHOLD = 120
LOPSIDED = 900
LIMIT = chess.engine.Limit(nodes=120_000)


def score(engine: chess.engine.SimpleEngine, board: chess.Board, pov: chess.Color) -> int:
    info = engine.analyse(board, LIMIT)
    return info["score"].pov(pov).score(mate_score=10_000) or 0


def run(limit: int) -> None:
    rows = store.q("SELECT id, fen, move FROM items WHERE n_threats = 0 AND quiet_ok IS NULL ORDER BY random() LIMIT ?",
                   (limit,))
    if not rows:
        return
    path = config.stockfish()
    if not path:
        say("нет Stockfish: тихие позиции не проверены (впиши stockfish в config.toml)")
        return
    ok = bad = 0
    with chess.engine.SimpleEngine.popen_uci(path) as engine:
        engine.configure({"Threads": 2, "Hash": 64})
        for i, r in enumerate(rows, 1):
            after = chess.Board(r["fen"])
            after.push(chess.Move.from_uci(r["move"]))
            me = after.turn
            passed = after.copy(stack=False)
            passed.push(chess.Move.null())
            now = score(engine, after, me)
            if abs(now) >= LOPSIDED:
                verdict = 0
            else:
                verdict = int(now - score(engine, chess.Board(passed.fen()), me) < THRESHOLD)
            store.run("UPDATE items SET quiet_ok=? WHERE id=?", (verdict, r["id"]))
            ok, bad = ok + verdict, bad + 1 - verdict
            if i % 50 == 0:
                say(f"проверено тихих {i} из {len(rows)}: подходят {ok}")
    say(f"тихие позиции: подходят {ok}, со скрытой угрозой или неравные {bad}")
