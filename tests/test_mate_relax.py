"""A forced mate of yours (2026-11-25, the user: marking every threat of his there is a chore, and the right moves of a
forced line must not count as mistakes): when Stockfish sees your mate within MATE_RELAX moves, his threats are
optional in step 1 — the mate itself is what to find; after a move of yours that keeps the forced mate, the marks
after it are not checked. Away from the mating line the usual rules hold."""

import chess

from conftest import add_task  # noqa: F401 — the fixtures of conftest are used by name
from vision import play, store, threats

# the user's position, 2026-11-25 10:28 (a Lichess mate-in-one): after …Bf8 (hitting the queen) White mates by Qh7#;
# his threats Bxh6 and Nxe5 are there too
POS = ("r1bqr1k1/1p1n1p2/p1n1p2Q/2bpP2P/6P1/2NB1N2/PPP2P2/R4RK1 b - - 0 15", "c5f8")


def _after(fen, uci):
    b = chess.Board(fen)
    b.push_uci(uci)
    return b


def test_with_a_mate_his_threats_are_optional_in_step_1():
    board = _after(*POS)
    key = threats.analyse(board)
    plain = threats.check(key, [], board)
    assert {"h6", "e5"} <= set(plain["missed"])  # without the relaxing: his threats are to be marked
    relaxed = threats.check(key, [], board, mate_relax=True)
    assert not {"h6", "e5"} & set(relaxed["missed"])
    assert relaxed["missed"]  # the mate itself still is to be found
    assert relaxed["mate_relax"]


def test_with_a_mate_playing_it_at_once_is_all_right():
    board = _after(*POS)
    key = threats.analyse(board)
    res = threats.check(key, [], board, played={"from": "h6", "to": "h7"}, mate_relax=True)
    assert res["ok"]


def test_step_1_relaxes_when_the_engine_sees_your_mate(fake):
    store.add_item("own", POS[0], POS[1], "w", threats.analyse(_after(*POS)))
    board = _after(*POS)
    fake.evals[fake.key(board)] = 9999  # mate in one for you
    store.meta_set("series_length", "1")
    v = play.start()
    a = play.answer(v["play"], [{"from": "h7", "to": "h7"}], 100)  # wrong circle square; the mate: circle the king
    assert a["result"]["mate_relax"]
    assert "h6" not in a["result"]["missed"] and "e5" not in a["result"]["missed"]


def test_step_3_after_a_move_that_keeps_the_mate_is_not_checked(fake):
    store.add_item("own", POS[0], POS[1], "w", threats.analyse(_after(*POS)))
    store.meta_set("series_length", "3")
    v = play.start()
    pid = v["play"]
    play.answer(pid, [], 100)
    p = play.PLAYS[pid]
    after = p["board"].copy()
    after.push_uci("a2a3")  # say this kept a forced mate (the engine stand-in says so): his Bxh6 after it is free
    fake.evals[fake.key(after)] = 9997
    c = play.commit(pid, "a2a3", [], 100)
    assert c["own"]["result"]["ok"] and c["own"]["result"].get("mate_line")


def test_step_3_off_the_mating_line_is_checked_as_usual(fake):
    store.add_item("own", POS[0], POS[1], "w", threats.analyse(_after(*POS)))
    store.meta_set("series_length", "3")
    v = play.start()
    pid = v["play"]
    play.answer(pid, [], 100)
    c = play.commit(pid, "a2a3", [], 100)  # the engine stand-in: a normal position — his Bxh6 is to be marked
    assert not c["own"]["result"].get("mate_line")
    assert "h6" in c["own"]["result"]["missed"]


def test_answers_under_the_mate_relaxing_do_not_move_the_attention_rating(clean_db):
    # with your forced mate his threats were optional (a free step 1, or marks not checked on the mating line): such an
    # answer says nothing about your attention — it must neither raise nor lower the rating (the user, 2026-11-25)
    from vision import rating

    board = _after(*POS)
    key = threats.analyse(board)
    store.add_item("own", POS[0], POS[1], "w", key)
    item = store.q("SELECT id FROM items")[0]["id"]
    relaxed = threats.check(key, [], board, mate_relax=True)
    relaxed["ok"] = True
    store.record(item, "s", 1000, relaxed["n"], relaxed, [])
    assert rating.replay() == [] and rating.current()[0] == rating.START
    store.run("DELETE FROM attempts")
    plain = threats.check(key, [], board)
    store.record(item, "s", 1000, plain["n"], plain, [])
    assert len(rating.replay()) == 1 and rating.current()[0] != rating.START


def test_a_move_on_the_mating_line_does_not_move_the_attention_rating(clean_db):
    from vision import rating

    store.add_item("ownmove", POS[0], POS[1], "w", {"threats": [], "attacks": [], "defended": [], "exchanges": {}})
    item = store.q("SELECT id FROM items")[0]["id"]
    store.record(item, "s", 1000, 0, {"found": [], "missed": [], "extra": [], "ok": True, "mate_line": True}, [])
    assert rating.replay() == []
