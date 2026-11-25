"""The steps of a series (vision/play.py) with the engines stood in for (conftest.FakeEngine): what the server answers
at each step of a task, what it records, when a series ends."""

import chess

from conftest import BACK_RANK, BACK_RANK_MOVE, BACK_RANK_STEPS, add_task
from vision import play, store, tasks

SCHOLAR = "r1bqkbnr/pppp1ppp/2n5/4p3/2B1P3/5Q2/PPPP1PPP/RNB1K1NR b KQkq - 3 3"  # Black is about to play Nd4??


def _start():
    v = play.start()
    return v["play"], v


def _to_move(pid):
    """Through step 1 (no marks) to your move."""
    p = play.PLAYS[pid]
    if p["phase"] == "check":
        play.answer(pid, [], 100)
    assert p["phase"] == "plan"
    return p


def test_a_series_starts_on_the_task_and_its_whole_line_counts(fake):
    item = add_task(BACK_RANK, BACK_RANK_MOVE, BACK_RANK_STEPS)
    pid, v = _start()
    p = play.PLAYS[pid]
    assert p["task"]["item"] == item and v["series"]["length"] >= 2  # the series is as long as the line at least
    _to_move(pid)
    c = play.commit(pid, "e2e8", [], 100)
    assert c["decision"] == {"ok": True, "done": False, "step": 0}
    assert c["phase"] == "bot"
    fake.best[fake.key(p["board"])] = "g8h8"  # not legal, and not what the task says: the task's reply comes
    b = play.next_series(pid)
    assert b["bot"]["san"] == "Rxe8"
    _to_move(pid)
    c = play.commit(pid, "e1e8", [], 100)
    assert c["decision"]["ok"] and c["decision"]["done"]
    assert c["series_end"]["game_over"]
    row = store.q("SELECT ok, moves FROM decisions")[0]
    assert row["ok"] == 1 and row["moves"] == '["e2e8", "e1e8"]'


def test_a_wrong_move_in_the_line_shows_the_rest_and_both_lines(fake):
    add_task(BACK_RANK, BACK_RANK_MOVE, BACK_RANK_STEPS)
    pid, _ = _start()
    p = _to_move(pid)
    play.commit(pid, "e2e8", [], 100)
    play.next_series(pid)
    _to_move(pid)
    fake.pv[fake.key(_after(p["board"], "g2g3"))] = ["e8e1"]  # his answer to your move
    c = play.commit(pid, "g2g3", [], 100)
    d = c["decision"]
    assert not d["ok"] and d["done"]
    assert d["kind"] == "tactic" and d["name"] == tasks.NAME["tactic"]
    assert [s["san"] for s in d["line"]] == ["Rxe8#"]  # the rest of the solution, from this step
    assert [s["san"] for s in d["your_line"]] == ["g3", "Rxe1+"]  # your move, then his answer
    assert d["your_line"][1]["who"] == "he" and d["your_line"][1]["fen"]
    assert d["why"] and d["idea"] == "Идея решения."
    assert store.q("SELECT ok FROM decisions")[0]["ok"] == 0
    assert store.q("SELECT item FROM schedule")  # the task comes back as a repeat


def test_a_one_step_task_draws_only_its_move(fake):
    add_task(SCHOLAR, "c6d4", [{"best": "f3f7", "accept": ["f3f7"], "reply": None, "margin": None}],
             pv=["f3f7"])
    pid, _ = _start()
    _to_move(pid)
    c = play.commit(pid, "c4d5", [], 100)
    d = c["decision"]
    assert not d["ok"] and d["draw"] == 1 and d["line"][0]["san"] == "Qxf7#"


def test_a_move_within_the_margin_counts_the_trap_never(fake):
    steps = [{"best": "f3f7", "accept": ["f3f7"], "reply": None, "margin": 10.0, "refuse": ["c4f7"]}]
    add_task(SCHOLAR, "c6d4", steps, kind="quiet")
    pid, _ = _start()
    _to_move(pid)
    fake.losses["e1d1"] = 4.0
    assert play.commit(pid, "e1d1", [], 100)["decision"]["ok"]
    store.run("DELETE FROM decisions")
    store.run("DELETE FROM schedule")
    pid, _ = _start()
    _to_move(pid)
    fake.losses["c4f7"] = 0.0  # even if the engine liked it: the trap move never counts
    assert not play.commit(pid, "c4f7", [], 100)["decision"]["ok"]


def test_the_series_ends_when_your_move_loses(fake):
    add_task(SCHOLAR, "c6d4", [{"best": "f3f7", "accept": ["f3f7"], "reply": None, "margin": None}])
    pid, _ = _start()
    p = _to_move(pid)
    fake.evals[fake.key(_after(p["board"], "f3d3"))] = -500
    c = play.commit(pid, "f3d3", [], 100)
    assert c["phase"] == "next" and c["series_end"]["lost"]


def test_the_series_ends_when_his_move_leaves_you_lost(fake):
    add_task(SCHOLAR, "c6d4", [{"best": "f3f7", "accept": ["f3f7"], "reply": None, "margin": None}])
    play_level(3)
    pid, _ = _start()
    p = _to_move(pid)
    c = play.commit(pid, "c4b5", [], 100)  # a wrong move, not lost yet
    assert c["phase"] == "bot"
    after = p["board"].copy()
    fake.best[fake.key(after)] = "d4f3"
    after.push_uci("d4f3")
    fake.evals[fake.key(after)] = -600
    b = play.next_series(pid)
    assert b["bot"]["san"] == "Nxf3+" and b["phase"] == "next" and b["series_end"]["lost"]


def test_the_opponent_plays_the_engine_move(fake):
    add_task(SCHOLAR, "c6d4", [{"best": "f3f7", "accept": ["f3f7"], "reply": None, "margin": None}])
    play_level(3)
    pid, _ = _start()
    p = _to_move(pid)
    play.commit(pid, "g1e2", [], 100)  # a wrong move: the task is over, the game goes on
    fake.best[fake.key(p["board"])] = "d4c2"
    assert play.next_series(pid)["bot"]["san"] == "Nxc2+"


def test_tasks_above_the_rating_wait(clean_db):
    store.meta_set("decisions_start", "1300")
    add_task(BACK_RANK, BACK_RANK_MOVE, BACK_RANK_STEPS, rating=1450)
    assert tasks.pick(set(), tasks.current()) is None
    assert tasks.ready_counts()["tactic"] == 0  # not counted in the stock either
    easy = add_task(SCHOLAR, "c6d4", [{"best": "f3f7", "accept": ["f3f7"], "reply": None, "margin": None}],
                    rating=1150)
    assert tasks.pick(set(), tasks.current())["item"] == easy


def test_a_repeat_does_not_move_the_rating(clean_db):
    store.meta_set("decisions_start", "1300")
    tasks.record(1, "s", 1, "tactic", False, ["e2e4"], 1000, 1300, repeat=True)
    assert tasks.current() == 1300
    tasks.record(2, "s", 1, "tactic", True, ["e2e4"], 1000, 1300, repeat=False)
    assert tasks.current() > 1300


def play_level(n: int) -> None:
    store.meta_set("series_length", str(n))


def _after(board: chess.Board, uci: str) -> chess.Board:
    b = board.copy()
    b.push_uci(uci)
    return b
