"""A change of the threat rule re-checks past answers (vision/migrate.py): what was right stays right where the rule
did not change for it — found 2026-11-25: an answer whose chance was taken by the move drawn at once (no arrow for it)
became wrong on re-check, because the re-check did not know that move."""

import json

import chess

from vision import migrate, store, threats

# White to move after …Nxe5: fxe5 wins the knight (a chance); the user drew fxe5 as his move at once, no marks
FEN, MOVE = "r1bqr1k1/ppp1bppp/4pnn1/3pN3/5P2/1P1PP2P/PBPNB1P1/R2QK2R b KQ - 2 10", "g6e5"


def _item_and_answer(result_extra: dict | None = None, with_played: bool = True):
    after = chess.Board(FEN)
    after.push_uci(MOVE)
    key = threats.analyse(after)
    store.add_item("series", FEN, MOVE, "w", key)
    item = store.q("SELECT id FROM items")[0]["id"]
    played = {"from": "f4", "to": "e5"}
    res = threats.check(key, [], after, played=played, with_checks=False)
    assert res["ok"]
    if with_played:
        res["played"] = played
    else:
        res.pop("played", None)
        res.pop("checks", None)  # as stored before rule 8
    store.record(item, "s", 1000, res["n"], res, [])
    return item


def _rerun():
    store.meta_set("rule_version", "0")
    migrate.run()
    return store.q("SELECT ok, result FROM attempts")[0]


def test_a_chance_taken_by_the_move_drawn_at_once_stays_found(clean_db):
    _item_and_answer(with_played=True)
    row = _rerun()
    assert row["ok"] == 1 and "e5" in json.loads(row["result"])["found"]


def test_an_old_answer_without_the_move_keeps_what_it_had_found(clean_db):
    _item_and_answer(with_played=False)
    row = _rerun()
    assert row["ok"] == 1


def test_the_move_drawn_at_once_is_kept_in_the_result():
    after = chess.Board(FEN)
    after.push_uci(MOVE)
    key = threats.analyse(after)
    assert threats.check(key, [], after, played={"from": "f4", "to": "e5"})["played"] == {"from": "f4", "to": "e5"}
