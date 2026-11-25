"""A threat marked before your move and still the same after it — the same piece of his hits the same piece of yours —
is found after the move too (2026-11-25: Kxh5 marked in step 1, then …Kd3 left it as it was, and step 3 counted it
as missed). A threat your move creates, or one that changed, is still to be marked."""

import chess

from vision import threats

# Black to move after 70.Kg5 (a tablebase endgame): the king hits h5; the user marked g5→h5, then played …Kd3
KG5 = "8/8/8/6Kp/7P/4p3/4k3/8 b - - 1 70"


def _own(fen: str, uci: str, arrows: list[dict], prior: list[dict]) -> dict:
    """Step 1 on `fen` with the `prior` arrows (what they found right is kept), then the move and step 3."""
    board = chess.Board(fen)
    step1 = threats.analyse(board)
    prior = threats.found_before(step1, threats.check(step1, prior, board))
    mv = chess.Move.from_uci(uci)
    key = threats.own_move(board, mv)
    after = board.copy()
    after.push(mv)
    return threats.check_own(key, arrows, after, prior=prior)


def test_a_threat_marked_before_the_move_and_unchanged_is_found_after_it():
    res = _own(KG5, "e2d3", [], prior=[{"from": "g5", "to": "h5"}])
    assert res["ok"] and "h5" in res["found"] and res["carried"] == ["h5"]


def test_without_the_mark_before_it_is_still_missed():
    res = _own(KG5, "e2d3", [], prior=[])
    assert "h5" in res["missed"]


def test_a_mark_before_the_move_does_not_cover_a_new_threat():
    # Black to move: his rook hits the loose a6 pawn (marked a1→a6); …Nd4?? puts the knight where the queen takes it
    fen = "4k3/8/p1n5/8/8/8/8/R2QK3 b - - 0 1"
    res = _own(fen, "c6d4", [], prior=[{"from": "a1", "to": "a6"}])
    assert res["carried"] == ["a6"] and "d4" in res["missed"]


def test_a_wrong_mark_before_the_move_carries_nothing():
    # an arrow of step 1 that found nothing (d1→a4: no threat there while the knight is on c6) does not count after
    # the move, even where it happens to fit a new check (Qa4+ once the knight has gone)
    fen = "r3k3/8/2n5/8/8/8/8/3QK3 b - - 0 1"
    res = _own(fen, "c6d4", [], prior=[{"from": "d1", "to": "a4"}])
    assert not res["carried"] and "d4" in res["missed"]


def test_a_mark_whose_target_moved_away_is_not_carried():
    # the threatened knight itself moves to another attacked square: the old take (on c6) is gone, the new one is new
    fen = "r3k3/8/2n5/8/Q7/8/8/4K3 b - - 0 1"  # Qa4 hits the loose knight c6
    res = _own(fen, "c6b4", [], prior=[{"from": "a4", "to": "c6"}])
    assert "b4" in res["missed"] and "c6" not in res["carried"]


def test_in_a_series_step_1_marks_count_after_the_move(fake):
    from vision import play, store

    # the user's endgame: before 70.Kg5 (the opponent's move); the user marks g5→h5, plays …Kd3 and marks nothing after
    before = "8/8/8/7p/5K1P/4p3/4k3/8 w - - 0 70"
    after = chess.Board(before)
    after.push_uci("f4g5")
    store.add_item("own", before, "f4g5", "b", threats.analyse(after))
    store.meta_set("series_length", "3")
    v = play.start()
    pid = v["play"]
    a = play.answer(pid, [{"from": "g5", "to": "h5"}], 100)
    assert a["result"]["ok"]
    c = play.commit(pid, "e2d3", [], 100)
    assert c["own"]["result"]["ok"] and c["own"]["result"]["carried"] == ["h5"]
