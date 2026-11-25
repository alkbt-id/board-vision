"""Real positions from the user's sessions, with the real Stockfish (skipped without it): what is counted as right,
what the review shows. A mistake the user reports becomes a test here first, then it is fixed."""

import chess
import pytest

from vision import engine, play, tasks, threats

pytestmark = pytest.mark.engine


def _after(fen: str, uci: str) -> chess.Board:
    b = chess.Board(fen)
    b.push_uci(uci)
    return b


# 2026-11-25 09:58: Black's 16…Rd3 hits e3; the user (White) played 17.Nd5?, which opened the third rank to Rxb3
ND5 = ("r2r2k1/ppp2ppp/5n2/4p3/4P3/PBN1P2P/1P4P1/R4RK1 b - - 0 16", "d8d3")
ND5_TASK = {"kind": "threat", "steps": [{"best": "f1d1", "accept": ["f1d1"], "reply": None, "margin": 10.0}],
            "pv": ["f1d1", "d3d1", "a1d1", "c7c6"], "wrong": {}, "idea": "", "title": ""}


def test_nd5_the_rook_hitting_e3_is_the_threat_of_step_1(stockfish):
    key = threats.analyse(_after(*ND5))
    assert [t["target"] for t in key["threats"]] == ["e3"]


def test_nd5_after_the_move_b3_and_e4_hang(stockfish):
    board = _after(*ND5)
    own = threats.own_move(board, chess.Move.from_uci("c3d5"))
    assert {"b3", "e4"} <= {t["target"] for t in own["threats"]}


def test_nd5_is_not_counted_rfd1_is(stockfish):
    board = _after(*ND5)
    assert not tasks.grade(ND5_TASK, 0, board, chess.Move.from_uci("c3d5"), loss=engine.loss)["ok"]
    assert tasks.grade(ND5_TASK, 0, board, chess.Move.from_uci("f1d1"), loss=engine.loss)["ok"]


def test_nd5_review_draws_his_answer_rxb3(stockfish):
    # the review once named his answer (taking the bishop) only in words: it must be a move on the board too
    board = _after(*ND5)
    review = play._review(ND5_TASK, 0, board, chess.Move.from_uci("c3d5"))
    assert [s["san"] for s in review["your_line"][:2]] == ["Nd5", "Rxb3"]
    assert review["your_line"][1]["who"] == "he"
    assert review["line"][0]["san"] == "Rfd1"
    assert review["draw"] == 1  # a one-step task: only the right move as an arrow


# 2026-11-25 09:22: after 17.Qxa7 the bot (then Maia, by chance) did not take the rook on a1
ROOK = "2b2rk1/Q4ppp/3q4/1p2p3/8/1N1P2N1/PPn2PPP/R4RK1 b - - 0 17"


def test_the_opponent_takes_the_hanging_rook(stockfish):
    assert engine.best_move(chess.Board(ROOK)).uci() == "c2a1"


def test_a_quiet_move_within_the_margin_counts(stockfish):
    # the start of a game: any normal developing move counts in a quiet task
    task = {"kind": "quiet", "steps": [{"best": "e2e4", "accept": ["e2e4"], "reply": None, "margin": 10.0}]}
    board = chess.Board()
    assert tasks.grade(task, 0, board, chess.Move.from_uci("g1f3"), loss=engine.loss)["ok"]
    # and a move that drops a piece does not: 1.e4 e5 2.Nf3 Nc6 3.Bc4 Nd4 4.Nxe5?? Qg5 (the knight and g2 hang)
    b = chess.Board("r1bqkbnr/pppp1ppp/8/4p3/2BnP3/5N2/PPPP1PPP/RNBQK2R w KQkq - 4 4")
    assert not tasks.grade(task, 0, b, chess.Move.from_uci("f3e5"), loss=engine.loss)["ok"]


# 2026-11-25 10:22: he threatened Nxc6 (the user marked it); the user played …c5?! (−15 points: Nd5 takes the outpost).
# His answer Nd5 is a quiet move, no threat: the review must not sign it as one
C5 = ("r1bqkbnr/5pp1/p1pp3p/3Np3/4P3/8/PPP2PPP/R1BQK1NR w KQkq - 0 9", "d5b4")
C5_TASK = {"kind": "threat", "steps": [{"best": "g8f6", "accept": ["g8f6"], "reply": None, "margin": 10.0}],
           "pv": ["g8f6", "f2f3", "d8c7", "b4d3"], "wrong": {}, "idea": "", "title": ""}


def test_c5_his_quiet_answer_has_no_sign(stockfish):
    board = _after(*C5)
    mv = chess.Move.from_uci("c6c5")
    assert not threats.own_move(board, mv)["threats"]  # nothing of his to mark after …c5
    assert not tasks.grade(C5_TASK, 0, board, mv, loss=engine.loss)["ok"]  # but the move is not good enough
    review = play._review(C5_TASK, 0, board, mv)
    reply = review["your_line"][1]
    assert reply["san"] == "Nd5" and reply["sign"] == ""


def test_his_capture_and_check_answers_are_signed(stockfish):
    board = _after(*ND5)
    review = play._review(ND5_TASK, 0, board, chess.Move.from_uci("c3d5"))
    assert review["your_line"][1]["san"] == "Rxb3" and review["your_line"][1]["sign"] == "взятие"
