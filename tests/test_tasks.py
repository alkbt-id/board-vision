"""Tasks for the move: the check of a move (one try, a line, a margin, a trap), the rating on the game scale, the
solution shown, the tablebase's moves, a wrong move in words."""

import chess

from vision import explain, tasks
from vision.sources import prepare


def _task(steps, kind="tactic", **extra):
    return {"kind": kind, "item": 1, "rating": 1300, "steps": steps, **extra}


def test_win_pct_is_even_at_zero_and_symmetric():
    assert tasks.win_pct(0) == 50
    assert abs(tasks.win_pct(300) + tasks.win_pct(-300) - 100) < 1e-9
    assert tasks.win_pct(10_000) > 99


def test_rating_of_half_solved_is_the_mean_level():
    assert tasks.rating_of({1100: 0.5, 1900: 0.5}) == 1500
    # rarely found → hard; nearly always found → easy
    assert tasks.rating_of({1300: 0.05}) > 1700
    assert tasks.rating_of({1300: 0.95}) < 900


def test_a_line_goes_on_after_a_right_move_and_ends_on_its_last_one():
    # 1.e4 e5 2.Qh5 Nc6 3.Bc4 Nf6?? 4.Qxf7#: a two-step "line" from 3.Bc4 (made up for the test)
    board = chess.Board("r1bqkbnr/pppp1ppp/2n5/4p2Q/4P3/8/PPPP1PPP/RNB1KBNR w KQkq - 2 3")
    t = _task([{"best": "f1c4", "accept": ["f1c4"], "reply": "g8f6", "margin": None},
               {"best": "h5f7", "accept": ["h5f7"], "reply": None, "margin": None}])
    g = tasks.grade(t, 0, board, chess.Move.from_uci("f1c4"))
    assert g == {"ok": True, "done": False, "alt": False}
    board.push_uci("f1c4")
    board.push_uci("g8f6")
    assert tasks.grade(t, 1, board, chess.Move.from_uci("h5f7"))["done"]


def test_a_wrong_move_in_a_line_fails_and_any_mate_counts():
    board = chess.Board("r1bqkb1r/pppp1ppp/2n2n2/4p2Q/2B1P3/8/PPPP1PPP/RNB1K1NR w KQkq - 4 4")
    t = _task([{"best": "h5f7", "accept": ["h5f7"], "reply": None, "margin": None}])
    assert not tasks.grade(t, 0, board, chess.Move.from_uci("h5e5"))["ok"]
    # a mate that is not the task's move still counts
    t2 = _task([{"best": "d1d2", "accept": ["d1d2"], "reply": None, "margin": None}])
    g = tasks.grade(t2, 0, board, chess.Move.from_uci("h5f7"))
    assert g["ok"] and g["done"] and g["alt"]


def test_margin_counts_a_move_close_enough_but_never_the_trap():
    board = chess.Board()
    t = _task([{"best": "e2e4", "accept": ["e2e4"], "reply": None, "margin": 10.0, "refuse": ["f2f3"]}], kind="quiet")
    near = lambda b, m: 3.0  # noqa: E731 — the engine says the move gives up 3 points
    far = lambda b, m: 25.0  # noqa: E731
    assert tasks.grade(t, 0, board, chess.Move.from_uci("d2d4"), loss=near)["ok"]
    assert not tasks.grade(t, 0, board, chess.Move.from_uci("d2d4"), loss=far)["ok"]
    assert not tasks.grade(t, 0, board, chess.Move.from_uci("f2f3"), loss=near)["ok"]


def test_line_from_shows_the_rest_of_the_solution_with_whose_move():
    board = chess.Board("r1bqkbnr/pppp1ppp/2n5/4p2Q/4P3/8/PPPP1PPP/RNB1KBNR w KQkq - 2 3")
    t = _task([{"best": "f1c4", "accept": ["f1c4"], "reply": "g8f6", "margin": None},
               {"best": "h5f7", "accept": ["h5f7"], "reply": None, "margin": None}])
    line = tasks.line_from(t, 0, board)
    assert [(x["san"], x["who"]) for x in line] == [("Bc4", "you"), ("Nf6", "he"), ("Qxf7#", "you")]


def test_one_step_task_shows_its_engine_line():
    board = chess.Board()
    t = _task([{"best": "e2e4", "accept": ["e2e4"], "reply": None, "margin": 10.0}], kind="quiet",
              pv=["e2e4", "e7e5", "g1f3", "b8c6", "f1b5"])
    assert [x["san"] for x in tasks.line_from(t, 0, board)] == ["e4", "e5", "Nf3", "Nc6"]


def test_tablebase_good_moves_are_the_ones_that_keep_the_result():
    # the API's move categories are the other side's: a winning move is "loss"
    tb = {"category": "win", "moves": [{"uci": "e6d6", "category": "loss"}, {"uci": "e6f6", "category": "loss"},
                                       {"uci": "e6d5", "category": "draw"}]}
    assert prepare._tb_good(tb) == ("win", ["e6d6", "e6f6"])
    draw = {"category": "draw", "moves": [{"uci": "a1a2", "category": "draw"}, {"uci": "a1b1", "category": "win"}]}
    assert prepare._tb_good(draw) == ("draw", ["a1a2"])


def test_why_worse_names_the_lost_queen():
    # Black's queen on d4 can be taken by the knight after a careless move
    board = chess.Board("rnb1kbnr/pppp1ppp/8/4p3/3q4/2N2N2/PPPPPPPP/R1BQKB1R b KQkq - 3 3")
    mv = chess.Move.from_uci("h7h6")
    pv = [chess.Move.from_uci(u) for u in ("f3d4", "e5d4", "c3d5")]  # the queen for a knight
    text = explain.why_worse(board, mv, pv, -600, 0, "Qd6")
    assert "отдаёшь ферзя за коня" in text
    assert "Nxd4" in text


def test_why_worse_without_material_compares_evaluations():
    board = chess.Board()
    text = explain.why_worse(board, chess.Move.from_uci("g2g4"), [chess.Move.from_uci("e7e5")], -80, 30, "e4")
    assert "−0,8" in text and "+0,3" in text
