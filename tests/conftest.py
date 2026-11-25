"""Shared by the tests: a data folder of their own, a clean database, a stand-in for the engines, a task in the pool.

BV_HOME is set before anything of `vision` is imported (vision/config.py reads it once): the tests never touch the
real data folder.
"""

import os
import tempfile

os.environ["BV_HOME"] = tempfile.mkdtemp(prefix="bv-test-")

import chess  # noqa: E402
import pytest  # noqa: E402

from vision import bot, config, engine, play, store, tasks, threats  # noqa: E402

TABLES = ("items", "attempts", "schedule", "sessions", "meta", "tasks", "decisions", "skips", "web_cache")


@pytest.fixture
def clean_db():
    for t in TABLES:
        store.run(f"DELETE FROM {t}")
    play.PLAYS.clear()
    yield store


class FakeEngine:
    """What Stockfish would say, set by the test: evaluations of positions (by FEN, else `default`), the best move
    of positions, the line after a move, what a move gives up. Nothing is searched; the bot must not be asked."""

    def __init__(self) -> None:
        self.default = 0
        self.evals: dict[str, int] = {}  # board FEN -> centipawns for the side asked about
        self.best: dict[str, str] = {}  # board FEN -> uci
        self.pv: dict[str, list[str]] = {}  # board FEN (after your move) -> his line
        self.losses: dict[str, float] = {}  # uci -> winning chances given up
        self.loss_default = 50.0

    @staticmethod
    def key(board: chess.Board) -> str:
        return board.board_fen() + (" w" if board.turn else " b")

    def evaluate(self, board, pov):
        return self.evals.get(self.key(board), self.default)

    def best_move(self, board):
        u = self.best.get(self.key(board))
        return chess.Move.from_uci(u) if u else next(iter(board.legal_moves), None)

    def line(self, board, pov):
        return self.evals.get(self.key(board), self.default), [chess.Move.from_uci(u) for u in self.pv.get(self.key(board), [])]

    def loss(self, board, move):
        return self.losses.get(move.uci(), self.loss_default)


@pytest.fixture
def fake(monkeypatch, clean_db):
    f = FakeEngine()
    monkeypatch.setattr(engine, "evaluate", f.evaluate)
    monkeypatch.setattr(engine, "best_move", f.best_move)
    monkeypatch.setattr(engine, "line", f.line)
    monkeypatch.setattr(engine, "loss", f.loss)
    monkeypatch.setattr(engine, "refutation", lambda board, move: None)
    monkeypatch.setattr(engine, "forcing", lambda board, credit=0, quiet=False: [])

    def no_bot(board, rng=None):
        raise AssertionError("the opponent is Stockfish: the bot (Maia) must not be asked")

    monkeypatch.setattr(bot, "move", no_bot)
    monkeypatch.setattr(play, "REPEAT_SHARE", 0)  # a series starts on the task, never on a repeat
    return f


def add_task(fen: str, move: str, steps: list[dict], kind: str = "tactic", rating: int = 1200, **extra) -> int:
    """A pool position (before the opponent's `move`) that is a ready task; returns its item id."""
    after = chess.Board(fen)
    after.push_uci(move)
    answer = threats.analyse(after)
    assert answer is not None
    store.add_item("db", fen, move, "w" if after.turn == chess.WHITE else "b", answer)
    item = store.q("SELECT id FROM items WHERE key=?", (f"db|{fen}|{move}",))[0]["id"]
    data = {"kind": kind, "steps": steps, "title": "Тестовая идея", "idea": "Идея решения.", "wrong": {},
            "rating": rating, "origin": "test", **extra}
    tasks.save(item, kind, "ready", data, rating, "test")
    return item


# a two-step line: 1.Re8+ Rxe8 2.Rxe8# (the back rank), after Black's Rc7–c8
BACK_RANK = "6k1/2r2ppp/8/8/8/8/4RPPP/4R1K1 b - - 0 1"
BACK_RANK_MOVE = "c7c8"
BACK_RANK_STEPS = [{"best": "e2e8", "accept": ["e2e8"], "reply": "c8e8", "margin": None},
                   {"best": "e1e8", "accept": ["e1e8"], "reply": None, "margin": None}]


@pytest.fixture
def stockfish():
    """The real Stockfish, one thread and a fresh hash: with a node limit its answers are then the same every run
    (two threads search differently each time — a borderline line may or may not count)."""
    if not config.stockfish():
        pytest.skip("нет Stockfish")
    engine.close()
    sf = engine._open()
    assert sf is not None
    sf.configure({"Threads": 1})
    yield
    engine.close()
