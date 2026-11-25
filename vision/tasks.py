"""Tasks for the move: series starts where the move itself is checked, with one try («счёт и стратегия», 2026-11-25).

A series starts on one of six kinds of positions, not named before the review (KINDS holds the share of starts):
  threat    he threatens something: parry it                    pool positions of games with a threat (the capture rule)
  tactic    you have a win: play the whole line                 Lichess puzzles; game positions with a winning line
  quiet     nothing is going on: any move that does not spoil it  quiet pool positions (Stockfish-checked)
  trap      a natural move (Maia's likeliest) loses to a tactic: do not play it   pool positions; Lichess puzzles
            turned round (the move before the puzzle's mistake is his, the mistake would be yours)
  strategy  no tactics, one plan is clearly better              neilgd's positional set (checked by strong players)
  endgame   endgame technique: the move that keeps the result   neilgd's endgame theme, Lichess endgame puzzles,
            ≤7-piece positions of games checked by the Lichess tablebase
A task is `steps`: the moves to find, each with the moves that count (`accept`) and his reply after it (`reply`, the
position of the next step). One step for most kinds; a line for tactics and tablebase endgames — the whole line
counts, any mate too. A step with a `margin` also counts a move that keeps Stockfish's winning chances within that many
points of the best (live, vision/engine.loss); `refuse` (the trap) never counts.
A wrong move ends the task: the rest of the solution is shown with Claude's explanation prepared in the background
(vision/sources/prepare.py: the idea, and why the likeliest wrong moves fail); another wrong move gets the engine's
line in words (vision/explain.py). The game then goes on from the move made.

Each task has a rating on the scale of game ratings (`rating_of`, from Maia 1100–1900: how often players of each level
play a right move); the «Решения» rating (`replay`) is an Elo over first tries, started at the user's Lichess rapid
rating. Tasks are picked a little below it (BELOW), so most are solved.
"""

import json
import math
import random

import chess

from . import store

KINDS = {"threat": 0.20, "tactic": 0.20, "quiet": 0.15, "trap": 0.15, "strategy": 0.15, "endgame": 0.15}
NAME = {"threat": "Его угроза", "tactic": "Тактика", "quiet": "Тихая позиция", "trap": "Ловушка",
        "strategy": "Стратегия", "endgame": "Эндшпиль"}
START = 1300  # the «Решения» rating before the first answer, when the Lichess rapid rating is not known
K = 32
BELOW = 100  # tasks are picked near the rating minus this: about two of three solved
WINDOW = 200  # …and never above the rating plus this (the user: no tasks too hard for my level); harder ones wait
CANDIDATES = 40


def win_pct(cp: float) -> float:
    """Winning chances (0–100) of the side with `cp` centipawns, Lichess's formula."""
    return 50 + 50 * (2 / (1 + math.exp(-0.00368208 * max(-2000, min(2000, cp)))) - 1)


def rating_of(p_by_level: dict[int, float]) -> int:
    """The rating of a task on the game scale: for each Maia level L whose players find a right move with probability
    p, the Elo of an opponent that L scores p against — L + 400·log10((1−p)/p) — averaged over the levels."""
    vals = []
    for level, p in p_by_level.items():
        p = min(0.97, max(0.03, p))
        vals.append(level + 400 * math.log10((1 - p) / p))
    return round(sum(vals) / len(vals)) if vals else START


def expected(difficulty: float, rating: float) -> float:
    return 1 / (1 + 10 ** ((difficulty - rating) / 400))


def load(item_id: int) -> dict | None:
    rows = store.q("SELECT * FROM tasks WHERE item=?", (item_id,))
    if not rows:
        return None
    r = dict(rows[0])
    data = json.loads(r.pop("data"))
    return {**data, **r}


def save(item_id: int, kind: str, status: str, data: dict, rating: int | None = None, origin: str = "",
         note: str = "") -> None:
    store.run("INSERT INTO tasks (item, kind, status, rating, origin, data, note, added, prepared)"
              " VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(item) DO UPDATE SET kind=excluded.kind, status=excluded.status,"
              " rating=excluded.rating, origin=excluded.origin, data=excluded.data, note=excluded.note,"
              " prepared=excluded.prepared",
              (item_id, kind, status, rating, origin, json.dumps(data, ensure_ascii=False), note or None,
               store.now_ms(), store.now_ms() if status == "ready" else None))


def ready_counts(rating: float | None = None) -> dict[str, int]:
    """Ready tasks not tried yet and not above `rating` + WINDOW (the current «Решения» rating by default), per kind."""
    top = (current() if rating is None else rating) + WINDOW
    out = {k: 0 for k in KINDS}
    for r in store.q("SELECT kind, COUNT(*) AS n FROM tasks WHERE status='ready' AND rating <= ?"
                     " AND item NOT IN (SELECT item FROM decisions) GROUP BY kind", (top,)):
        out[r["kind"]] = r["n"]
    return out


def pick(exclude: set[int], rating: float) -> dict | None:
    """A ready task not tried yet: a kind drawn by KINDS (the next kind by share when that one has none), and of its
    tasks one of the three whose rating is nearest to `rating` − BELOW, never above `rating` + WINDOW. None when no
    kind has any."""
    kinds = list(KINDS)
    first = random.choices(kinds, [KINDS[k] for k in kinds])[0]
    order = [first] + sorted((k for k in kinds if k != first), key=lambda k: -KINDS[k] * random.random())
    target = rating - BELOW
    for kind in order:
        rows = [dict(r) for r in store.q(
            "SELECT item, rating FROM tasks WHERE status='ready' AND kind=? AND rating <= ?"
            " AND item NOT IN (SELECT item FROM decisions) ORDER BY random() LIMIT ?",
            (kind, rating + WINDOW, CANDIDATES)) if r["item"] not in exclude]
        if rows:
            rows.sort(key=lambda r: abs((r["rating"] or START) - target))
            return load(random.choice(rows[:3])["item"])
    return None


def grade(task: dict, step: int, board: chess.Board, move: chess.Move, loss=None) -> dict:
    """Your move at `step` of `task` (`board`: the position you moved in). {"ok", "done": the task is over, "alt": a
    right move off the line (the line cannot go on: solved here)}. `loss(board, move)` → winning chances the move
    gives up against the best (vision/engine.loss), for steps with a margin."""
    st = task["steps"][step]
    last = step == len(task["steps"]) - 1
    uci = move.uci()
    after = board.copy(stack=False)
    after.push(move)
    if after.is_checkmate():
        return {"ok": True, "done": True, "alt": uci != st["best"]}
    if uci in (st.get("refuse") or []):
        return {"ok": False, "done": True, "alt": False}
    if uci in st["accept"]:
        alt = uci != st["best"]
        return {"ok": True, "done": last or alt, "alt": alt}
    if st.get("margin") is not None and loss is not None:
        lost = loss(board, move)
        if lost is not None and lost <= st["margin"]:
            return {"ok": True, "done": True, "alt": True}
    return {"ok": False, "done": True, "alt": False}


def line_from(task: dict, step: int, board: chess.Board) -> list[dict]:
    """The rest of the solution from `step` on (`board`: that step's position), for the board: [{from, to, san, who}]
    — your moves and his replies, numbered on the board; a one-step task shows its engine line (`pv`) after the
    move too, at most 4 half-moves."""
    out = []
    b = board.copy(stack=False)
    moves: list[str] = []
    for st in task["steps"][step:]:
        moves.append(st["best"])
        if st.get("reply"):
            moves.append(st["reply"])
    if len(task["steps"]) == 1 and task.get("pv"):
        moves = task["pv"][:4]
    me = b.turn
    for u in moves:
        m = chess.Move.from_uci(u)
        if m not in b.legal_moves:
            break
        out.append({"from": u[:2], "to": u[2:4], "san": b.san(m), "who": "you" if b.turn == me else "he"})
        b.push(m)
    return out


def record(item_id: int, session: str, series: int, kind: str, ok: bool, moves: list[str], ms: int, rating: int | None,
           repeat: bool) -> None:
    store.run("INSERT INTO decisions (item, session, series, ts, kind, ok, moves, ms, rating, repeat)"
              " VALUES (?,?,?,?,?,?,?,?,?,?)",
              (item_id, session, series, store.now_ms(), kind, int(ok), json.dumps(moves), ms, rating, int(repeat)))


def start_rating() -> float:
    return float(store.meta_get("decisions_start", "") or START)


def replay() -> list[dict]:
    """Every first try of a task in order, with the «Решения» rating after it: [{session, series, ts, rating, kind,
    ok, difficulty}]. Repeats do not count (the answer can be remembered)."""
    rows = store.q("SELECT d.* FROM decisions d WHERE d.repeat = 0"
                   " AND d.id = (SELECT MIN(e.id) FROM decisions e WHERE e.item = d.item) ORDER BY d.ts")
    rating = start_rating()
    out = []
    for r in rows:
        diff = r["rating"] or START
        rating += K * ((1 if r["ok"] else 0) - expected(diff, rating))
        out.append({"session": r["session"], "series": r["series"], "ts": r["ts"], "rating": rating, "kind": r["kind"],
                    "ok": bool(r["ok"]), "difficulty": diff})
    return out


def current() -> float:
    hist = replay()
    return hist[-1]["rating"] if hist else start_rating()


def progress() -> dict:
    """The «Решения» chart: a point per task tried. {"start", "rating", "points": [{ts, rating, kind, ok}]}."""
    hist = replay()
    return {"start": round(start_rating()), "rating": round(hist[-1]["rating"]) if hist else round(start_rating()),
            "points": [{"ts": h["ts"], "rating": round(h["rating"]), "kind": h["kind"], "ok": h["ok"]} for h in hist]}
