"""Step 1 (his threats before your move) is judged by the same rule as step 3 (after your move) — the user,
2026-11-25, after a real check (Bb5+) drawn in step 1 was marked as an extra arrow: every check of his that does not
just lose the checking piece is a mark to make; a check that loses it is optional; a winning capture with check and a
mate stay what they are (one item each)."""

import chess

from vision import threats


def _key(fen: str) -> dict:
    key = threats.analyse(chess.Board(fen))
    assert key is not None
    return key


def _checks(key: dict, optional: bool | None = None) -> set[str]:
    return {t["best"] for t in key["threats"] if t.get("kind") == "check"
            and (optional is None or bool(t.get("optional")) == optional)}


# the user's position (2026-11-25 10:05): Black to move after 11.Qd2; Be2–b5+ is a check that loses nothing
BB5 = "rnb1k1nr/ppp2pp1/3p4/4p1N1/3P1qP1/2P4P/PP1QBP2/RN2K1R1 b Q - 1 11"


def test_his_check_before_your_move_is_a_mark_to_make():
    key = _key(BB5)
    assert "e2b5" in _checks(key, optional=False)
    board = chess.Board(BB5)
    assert "e2b5" in threats.check(key, [], board)["missed"]


def test_the_check_is_found_by_the_checking_move_or_by_an_arrow_to_the_king():
    key, board = _key(BB5), chess.Board(BB5)
    for arrow in ({"from": "e2", "to": "b5"}, {"from": "e2", "to": "e8"}):
        res = threats.check(key, [arrow], board)
        assert "e2b5" in res["found"] and not res["extra"]


def test_a_check_that_loses_the_checking_piece_is_optional():
    # 1.e4 e5 2…f5?-like: Black's f-pawn gone, Nf6 guards h5 — White's Qh5+ just loses the queen
    fen = "rnbqkb1r/pppp2pp/5n2/4p3/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 4"
    key, board = _key(fen), chess.Board(fen)
    assert _checks(key, optional=True) == {"d1h5"} and not _checks(key, optional=False)
    assert "d1h5" not in threats.check(key, [], board)["missed"]  # left out: no miss
    res = threats.check(key, [{"from": "d1", "to": "h5"}], board)
    assert "d1h5" in res["found"] and not res["extra"]  # drawn: right, not an extra arrow


def test_a_winning_capture_with_check_is_one_item_not_two():
    # Qxb3+ takes a loose bishop with check: the capture's threat has it, no second «check» item for the same move
    fen = "r5k1/6pp/8/8/8/1b6/5PPP/3Q2K1 b - - 0 1"
    key = _key(fen)
    assert "b3" in {t["target"] for t in key["threats"]}
    assert "d1b3" not in _checks(key)
    # Qd8+ (Rxd8) and Qd5+ (Bxd5) just lose the queen: optional
    assert _checks(key, optional=True) == {"d1d8", "d1d5"}


def test_a_mate_in_one_stays_a_mate_threat():
    fen = "r1bqkbnr/pppp1ppp/2n5/4p3/2B1P3/5Q2/PPPP1PPP/RNB1K1NR b KQkq - 3 3"  # Qxf7# is on
    key = _key(fen)
    assert any(t["mate"] for t in key["threats"])
    assert "f3f7" not in _checks(key)


def test_step_1_and_step_3_see_the_same_checks():
    # after a move of yours that changes nothing around (the rook h8–h7), his checks are the same as before it
    board = chess.Board(BB5)
    before = _checks(_key(BB5))
    after_move = {t["best"] for t in threats.own_move(board, chess.Move.from_uci("h8h7"))["threats"]
                  if t.get("kind") == "check"}
    assert before == after_move


def test_old_answers_are_checked_without_checks():
    # an answer given before checks were asked for in step 1 keeps its result
    key, board = _key(BB5), chess.Board(BB5)
    res = threats.check(key, [], board, with_checks=False)
    assert res["ok"] and not res["checks"]


def test_his_quiet_fork_is_asked_for_and_is_a_mark_to_make():
    # …e5–e4 forks the bishop d3 and the knight f3 (White to move); the engine's line is given, as it would say it
    fen = "r2q1rk1/ppp2ppp/5n2/4p3/8/3B1N2/PPP2PPP/R2Q1RK1 w - - 0 10"
    board = chess.Board(fen)
    key = threats.analyse(board)
    asked = []

    def forcing(b, credit=0, quiet=False):
        asked.append((b.turn, quiet))
        line = [chess.Move.from_uci(u) for u in ("e5e4", "d3e2", "e4f3", "e2f3")]
        return [{"pv": line, "mate": None}] if b.turn == chess.BLACK else []

    threats.add_tactics(key, board, forcing)
    assert (chess.BLACK, True) in asked  # his quiet moves are asked for, as after your move
    fork = next(t for t in key["threats"] if t["best"] == "e5e4")
    assert fork["tactic"] == "quiet" and not fork.get("optional")
    assert "e5e4" in threats.check(key, [], board)["missed"]
    assert "e5e4" in threats.check(key, [{"from": "e5", "to": "e4"}], board)["found"]
