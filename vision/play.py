"""The one training mode: series of checks, one game fragment each, getting longer as the user gets them right.

A sitting goes on series after series until the user leaves (✕): no count of checks and no summary;
a series of length L is a fragment of a game with L turns:
  1. a position from the pool (vision/sources: the user's games, other people's, Lichess puzzles turned around, or
     a position due for a repeat) and the opponent's real move in it — the first check;
  A series with moves (L > 1) starts only where the user's side is not lost with best play (Stockfish, NOT_LOST):
  traps and hard positions yes, lost ones no. A series ends as soon as the user's side becomes lost (NOT_LOST,
  after his move or the opponent's) or the game ends — the user (2026-11-25): no lost positions.
  2. if L > 1: the user draws his move as an arrow and, before it is played, the arrows of what the opponent can
     do after it («проверь свой ход»: what he can win at once by the capture rule, vision/threats.own_move, and a
     tactical reply Stockfish finds when the move loses material to it, vision/engine.py); «Хожу» checks that, plays the move, and the opponent
     answers — Stockfish's best move (the user's choice, 2026-11-25; Maia 1300 drew moves by chance and once left a
     rook en prise); then check again — until L checks of the bot's moves are done or the game ends.
L is the user's level (meta series_length, starts at 1): a series without a miss (in either kind of check) makes it
one longer (max 30), two missed series in a row make it one shorter (min 1). So at level 1 it is the plain «one position, one move» drill.
The position of a series: a due repeat in REPEAT_SHARE of series, else a position with a threat or a quiet one by
vision/session.threat_share(); the window gets no hint of the kind or the source.

Every check is an answer in `attempts` (the first one on the pool position, later ones on positions stored with
source "series"), so all of them count in the attention rating and the repeat ladder.

Tasks for the move (vision/tasks.py, 2026-11-25): a series starts on a prepared task when there is one near the
«Решения» rating (else as before). Your move in step 2 is the one try: «Хожу» checks it as well. In a line (a tactic, a
tablebase endgame) his replies are the task's, not the bot's, and every move of yours in it is checked, until the line
ends; the series is at least that long. A wrong move ends the task: the review shows the rest of the solution and why
(`decision` in the answer), then the game goes on from the move made. A task counts in the «Решения» rating; its
position comes back as a repeat after a wrong move.
"""

import json
import random
import time
import uuid

import chess

from . import bot, engine, events, explain, rating, session, store, tasks, threats
from .sources import lichess_api

CHECKS = 15
REPEAT_SHARE = 0.3
MAX_LEVEL = 30
NOT_LOST = -300  # a series (level 2+) starts only where your side, playing best, is not worse than this (cp)
TRIES = 8  # start positions tried before falling back to a single check
PLAYS: dict[str, dict] = {}


def level() -> int:
    return max(1, int(store.meta_get("series_length", "1") or 1))


def best() -> int:
    return int(store.meta_get("series_best", "0") or 0)


def _side(color: chess.Color) -> str:
    return "white" if color == chess.WHITE else "black"


def _pick_start(p: dict) -> dict:
    """The first position of the next series: a due repeat (in REPEAT_SHARE of the picks), else a prepared task near
    the «Решения» rating, else a position with a threat or a quiet one by the current share. A repeat of a task's
    position brings the task again (not counted in the rating: it can be remembered)."""
    if random.random() < REPEAT_SHARE:
        due = store.q("SELECT s.item FROM schedule s JOIN items i ON i.id = s.item WHERE s.due <= ?"
                      " AND i.source NOT IN ('ownmove', 'puzzle_old', 'missed_lost', 'db_early', 'missed_early')"
                      " ORDER BY s.due LIMIT 20", (store.now_ms(),))
        for r in due:
            if r["item"] not in p["used"]:
                it = store.item(r["item"])
                if it:
                    p["repeats"] += 1
                    t = tasks.load(it["id"])
                    it.update(repeat=True, task=t if t and t.get("status") == "ready" else None)
                    return it
    picked = tasks.pick(p["used"], p["drating"])
    if picked and (it := store.item(picked["item"])):
        it.update(repeat=False, task=picked)
        return it
    if random.random() < session.threat_share():
        near = session.pick_near(session.SHARP, session.THREAT_SOURCES, p["used"], p["rating"], p["points"])
        ids = [near] if near else session._pick(session.SHARP, session.THREAT_SOURCES, 1, p["used"])
    else:
        ids = session._pick("n_threats = 0 AND quiet_ok = 1", session.QUIET_SOURCES, 1, p["used"])
    if not ids:
        ids = session._pick(session.SHARP, session.THREAT_SOURCES, 1, p["used"])
    it = store.item(ids[0])
    assert it is not None
    it.update(repeat=False, task=None)
    return it


def _view(p: dict, extra: dict | None = None) -> dict:
    board: chess.Board = p["board"]
    sr = p["series"]
    out = {
        "play": p["id"], "phase": p["phase"], "checks": p["checks"], "budget": CHECKS, "fen": board.fen(),
        "orientation": _side(p["user"]),
        "series": {"no": p["series_no"], "length": sr["length"], "checks": sr["checks"], "own": sr.get("own", 0)},
    }
    if p["phase"] in ("plan", "check"):  # in a check too: your move drawn at once ends the marking
        d: dict[str, list[str]] = {}
        for m in board.legal_moves:
            d.setdefault(chess.square_name(m.from_square), []).append(chess.square_name(m.to_square))
        out["dests"] = d
    if extra:
        out.update(extra)
    return out


def _new_series(p: dict) -> dict:
    """The next series. With moves to play (level 2+) it starts only where your side is not lost with best play:
    traps and hard positions yes, positions lost in advance (most turned-around puzzles: the blow has landed) no.
    A lost start is skipped, whatever its source; only when TRIES picks in a row are lost does the last one come as a
    single check."""
    single = False
    for attempt in range(TRIES):
        it = _pick_start(p)
        p["used"].add(it["id"])
        before = chess.Board(it["fen"])
        move = chess.Move.from_uci(it["move"])
        board = before.copy()
        board.push(move)
        if it.get("task"):
            break  # a task is checked when prepared: its start is playable
        e = engine.evaluate(board, board.turn)
        if e is None or e >= NOT_LOST:
            break
        if attempt == TRIES - 1:  # nothing playable found: one check, not a game to play on; the level stays
            single = True
            break
    p["user"] = not before.turn
    p["board"] = board
    p["series_no"] += 1
    task = it.get("task")
    length = 1 if single else max(p["level"], len(task["steps"]) if task else 0)
    p["series"] = {"length": length, "checks": 0, "own": 0, "misses": 0, "single": single}
    p["task"], p["task_step"], p["task_moves"], p["task_ms"] = task, 0, [], 0
    p["task_repeat"] = bool(it.get("repeat"))
    p["forced_reply"] = None
    p["item"] = it["id"]  # the first check is answered on the pool position itself
    p["last"] = {"fen": it["fen"], "move": it["move"]}
    p["phase"] = "check"
    events.log("series_start", session=p["id"], no=p["series_no"], length=p["level"], item=it["id"],
               source=it["source"], motif=it["motif"], threats=it["n_threats"], repeat=it["repeat"], fen=it["fen"],
               move=it["move"], user=_side(p["user"]),
               **({"task_kind": task["kind"], "task_rating": task["rating"], "task_title": task.get("title", "")}
                  if task else {}))
    return _view(p, {"bot": {"before": it["fen"], "move": it["move"], "san": before.san(move),
                             "capture": before.is_capture(move)}, "new_series": True})


def start() -> dict:
    pid = uuid.uuid4().hex[:8]
    r, points = rating.current()  # the positions with a threat are picked by difficulty near the rating
    p = {"id": pid, "level": level(), "checks": 0, "series_no": 0, "used": set(), "repeats": 0, "answers": [],
         "series_done": [], "started": store.now_ms(), "rating": r, "points": points, "drating": tasks.current()}
    PLAYS[pid] = p
    store.run("INSERT INTO sessions (id, started, items) VALUES (?, ?, ?)", (pid, store.now_ms(), "[]"))
    events.log("session_start", session=pid, size=CHECKS, level=p["level"], share=session.threat_share())
    return _new_series(p)


def _bot_turn(p: dict) -> dict:
    board: chess.Board = p["board"]
    before = board.copy()
    t0 = time.time()
    forced = p.pop("forced_reply", None)
    mv = chess.Move.from_uci(forced) if forced else None
    if mv is None or mv not in board.legal_moves:
        # his move in a task's line is the task's; else Stockfish's best (Maia only without Stockfish)
        mv = engine.best_move(board) or bot.move(board)
    san, capture = board.san(mv), board.is_capture(mv)
    board.push(mv)
    p["last"] = {"fen": before.fen(), "move": mv.uci()}
    p["item"] = None
    events.log("bot_move", series=p["id"], san=san, uci=mv.uci(), ms=int((time.time() - t0) * 1000), fen=before.fen())
    shown: dict = {"bot": {"before": before.fen(), "move": mv.uci(), "san": san, "capture": capture}}
    if board.is_game_over():
        shown["series_end"] = _end_series(p, game_over=True)
        p["phase"] = "next"
    elif _lost(p):  # no play on in a lost position: the next series
        shown["series_end"] = _end_series(p, lost=True)
        p["phase"] = "next"
    elif board.is_check():
        p["phase"] = "plan"  # answer the check (no threat check here: the check is the threat)
        shown["check"] = True
    else:
        p["phase"] = "check"
    return shown


def _lost(p: dict) -> bool:
    """Is the user's side lost now with best play (Stockfish, NOT_LOST)? A task's line is never cut."""
    if p.get("task"):
        return False
    e = engine.evaluate(p["board"], p["user"])
    return e is not None and e < NOT_LOST


def _end_series(p: dict, game_over: bool = False, lost: bool = False) -> dict:
    """The level moves: a clean series +1, the second missed one in a row -1. Neutral (no change): a single check
    forced by a lack of playable starts, and a position that became lost without a miss of yours."""
    sr = p["series"]
    clean = sr["misses"] == 0 and sr["checks"] >= sr["length"]
    fails = int(store.meta_get("series_fails", "0") or 0)
    lvl = p["level"]
    if sr.get("single") or (lost and not sr["misses"]):
        clean = False
    elif clean:
        lvl, fails = min(MAX_LEVEL, lvl + 1), 0
        if sr["length"] > best():
            store.meta_set("series_best", str(sr["length"]))
    elif sr["checks"]:
        fails += 1
        if fails >= 2:
            lvl, fails = max(1, lvl - 1), 0
    store.meta_set("series_length", str(lvl))
    store.meta_set("series_fails", str(fails))
    out = {"length": sr["length"], "checks": sr["checks"], "misses": sr["misses"], "clean": clean,
           "game_over": game_over, "lost": lost, "single": bool(sr.get("single")), "next_level": lvl,
           "level_was": p["level"]}
    p["level"] = lvl
    p["series_done"].append(out)
    events.log("series_end", series=p["id"], summary=out)
    return out


def answer(pid: str, arrows: list[dict], ms: int, move: str = "") -> dict:
    """The user's marks after the opponent's move. `move`: his own move, drawn at once instead of «Ничего нет» /
    «Готово» — it ends the marking, and a chance it takes counts as found (the client then goes on to the plan)."""
    p = PLAYS[pid]
    if p["phase"] != "check":
        raise ValueError("сейчас не проверка")
    board: chess.Board = p["board"]
    key = threats.analyse(board) or {"threats": [], "defended": [], "chances": []}
    threats.add_tactics(key, board, engine.forcing)  # forced mates in 2–3, checks that win: the engine's
    arrows = [{"from": str(a.get("from")), "to": str(a.get("to"))} for a in arrows[:30] if isinstance(a, dict)]
    played = None
    if move:
        mv = chess.Move.from_uci(move[:4])
        if any(m.from_square == mv.from_square and m.to_square == mv.to_square for m in board.legal_moves):
            played = {"from": move[:2], "to": move[2:4]}
    # his threats now and his pieces you can win — the threats before your move came back
    result = threats.check(key, arrows, board, played=played)
    chances = key.get("chances") or []
    item_id = p["item"]
    if item_id is None:  # a position reached in play: store it like a pool position
        store.add_item("series", p["last"]["fen"], p["last"]["move"], "w" if p["user"] == chess.WHITE else "b", key,
                       ref=pid, ply=len(board.move_stack))
        row = store.q("SELECT id FROM items WHERE key = ?", (f"series|{p['last']['fen']}|{p['last']['move']}",))
        item_id = row[0]["id"] if row else None
    p["turn_item"] = item_id  # a miss after your move brings this position back as a repeat (vision/play.commit)
    if item_id is not None:
        store.record(item_id, pid, ms, result["n"], result, arrows, series=p["series_no"])
        if key.get("engine"):  # the position now carries its tactics (for its difficulty and its next showing)
            store.run("UPDATE items SET answer=?, n_threats=? WHERE id=?",
                      (json.dumps(key, ensure_ascii=False), threats.required(key), item_id))
    sr = p["series"]
    p["checks"] += 1
    sr["misses"] += not result["ok"]
    p["answers"].append({"ok": result["ok"], "threats": result["n"], "found": len(result["found"]),
                         "extra": len(result["extra"]), "ms": ms})
    events.log("answer", session=pid, index=p["checks"] - 1, size=CHECKS, item=item_id, ok=result["ok"],
               threats=len(key["threats"]), chances=len(chances), found=result["found"], missed=result["missed"],
               extra=result["extra"],
               arrows=arrows, ms=ms, series=p["series_no"], step=sr["checks"],
               **({"move": played["from"] + played["to"]} if played else {}))
    extra: dict = {"result": result, "threats": key["threats"], "chances": chances}
    if board.is_game_over():  # the series ends after your move now (vision/play.commit)
        extra["series_end"] = _end_series(p, game_over=board.is_game_over())
        p["phase"] = "next"
    else:
        p["phase"] = "plan"
    if p["phase"] == "next":
        extra["last"] = False  # the sitting goes on until the user leaves
    return _view(p, extra)


def _decide(p: dict, board: chess.Board, mv: chess.Move, ms: int) -> dict | None:
    """Your move in a task (`board`: before it): right or not; at the end of the task it is recorded. None outside a
    task. A wrong move carries the review: the rest of the solution, the idea, why the move is worse."""
    task = p.get("task")
    if not task or p["task_step"] >= len(task["steps"]):
        return None
    step = p["task_step"]
    g = tasks.grade(task, step, board, mv, loss=engine.loss)
    p["task_moves"].append(mv.uci())
    p["task_ms"] += ms
    events.log("decision", session=p["id"], item=task["item"], task_kind=task["kind"], step=step, move=mv.uci(),
               ok=g["ok"], done=g["done"], ms=ms)
    if g["ok"] and not g["done"]:
        p["forced_reply"] = task["steps"][step]["reply"]
        p["task_step"] += 1
        return {"ok": True, "done": False, "step": step}
    tasks.record(task["item"], p["id"], p["series_no"], task["kind"], g["ok"], p["task_moves"], p["task_ms"],
                 task["rating"], p["task_repeat"])
    p["task"] = None
    if not p["task_repeat"]:
        p["drating"] = tasks.current()
    out = {"ok": g["ok"], "done": True, "step": step, "kind": task["kind"], "name": tasks.NAME[task["kind"]],
           "title": task.get("title", ""), "rating": task["rating"]}
    if g["ok"]:
        return out
    store.schedule_again(task["item"])  # the task comes back tomorrow
    out.update(_review(task, step, board, mv))
    return out


def _review(task: dict, step: int, board: chess.Board, mv: chess.Move) -> dict:
    """What a wrong move gets: the solution from here on (for the board), the idea, why your move is worse — Claude's
    words prepared for the likeliest moves, else the engine's line (or the tablebase's verdict) in words."""
    line = tasks.line_from(task, step, board)
    why = (task.get("wrong") or {}).get(f"{step}:{mv.uci()}", "")
    st = task["steps"][step]
    best = chess.Move.from_uci(st["best"])
    if not why and st.get("tb"):
        after = board.copy(stack=False)
        after.push(mv)
        try:
            cat = lichess_api.tablebase(after.fen()).get("category", "")
            got = {"win": "проигрыш", "maybe-win": "проигрыш", "loss": "выигрыш", "maybe-loss": "выигрыш"}.get(cat, "ничья")
            want = "выигрыш" if st["tb"] == "win" else "ничья"
            why = f"По таблицам эндшпиля после {board.san(mv)} — {got}, а после {board.san(best)} — {want}."
        except lichess_api.Unavailable:
            why = ""
    me = board.turn
    after = board.copy(stack=False)
    after.push(mv)
    now, pv = engine.line(after, me)  # his answer to your move: your line on the board, and words if none are prepared
    if not why:
        best_cp, _ = engine.line(board, me)
        # a threat parried but the move still wrong for what it costs otherwise: the words say so, or a parry marked
        # wrong looks like a missed threat
        parried = task["kind"] == "threat" and not threats.required(threats.own_move(board, mv))
        why = explain.why_worse(board, mv, pv, now, best_cp, board.san(best), parried=parried)
    # a line is drawn whole; a one-step task only its move (the engine's continuation is text: arrows of it are noise).
    # Both lines — the right one and yours with his best answers — can be stepped through on the board.
    return {"line": line, "draw": len(line) if len(task["steps"]) > 1 else 1, "why": why, "idea": task.get("idea", ""),
            "before": board.fen(), "best_san": board.san(best), "your_line": tasks.steps_of(board, [mv, *pv[:6]]),
            "your": {"from": mv.uci()[:2], "to": mv.uci()[2:4], "san": board.san(mv)}}


def commit(pid: str, uci: str, arrows: list[dict], ms: int) -> dict:
    """The user's own move with his arrows of what the opponent can do after it: checked, then played."""
    p = PLAYS[pid]
    if p["phase"] != "plan":
        raise ValueError("сейчас не твой ход")
    board: chess.Board = p["board"]
    mv = chess.Move.from_uci(uci)
    if mv not in board.legal_moves:
        mv = chess.Move.from_uci(uci + "q")  # a pawn reaching the last rank becomes a queen
        if mv not in board.legal_moves:
            raise ValueError("такого хода нет")
    arrows = [{"from": str(a.get("from")), "to": str(a.get("to"))} for a in arrows[:30] if isinstance(a, dict)]
    key = threats.own_move(board, mv)
    ref = engine.refutation(board, mv)
    before_fen, san = board.fen(), board.san(mv)
    dec = _decide(p, board, mv, ms)  # the task's check of the move itself, before it is played
    took = threats.VALUE[board.piece_type_at(mv.to_square)] if board.piece_at(mv.to_square) else 0  # what your move takes
    board.push(mv)
    # his checks that win and his forced mates in 2–3 after your move, as the engine sees them — also when your move
    # is the best there is
    lines = engine.forcing(board, credit=took, quiet=True)  # what you have just taken is not his to «win» back
    if lines:
        key["threats"] = threats.merge_tactics(key["threats"], threats.tactic_items(board, lines, key["threats"], "he"))
    if ref:
        reply, loss = ref
        taken = {t["target"] for t in key["threats"]} | {t["best"] for t in key["threats"] if t.get("tactic")}
        if reply.uci() not in taken and not (board.is_capture(reply) and chess.square_name(reply.to_square) in taken):
            item = threats.reply_item(board, reply, loss, taken)  # a tactic the capture rule does not see
            item["line"] = threats.recapture_line(board, reply, engine.best_move)  # a «fair» trade that is not
            key["attacks"].append(item)
    result = threats.check_own(key, arrows, board)
    needed = len([t for t in key["threats"] if not t.get("optional")]) + len(key["attacks"])
    side = "w" if p["user"] == chess.WHITE else "b"
    store.add_item("ownmove", before_fen, mv.uci(), side, {**key, "threats": key["threats"]}, ref=pid,
                   ply=len(board.move_stack))
    row = store.q("SELECT id FROM items WHERE key = ?", (f"ownmove|{before_fen}|{mv.uci()}",))
    if row:
        store.record(row[0]["id"], pid, ms, needed, result, arrows, series=p["series_no"])
    if not result["ok"] and p.get("turn_item"):
        store.schedule_again(p["turn_item"])  # the position of this turn comes back tomorrow
    sr = p["series"]
    p["checks"] += 1
    sr["own"] += 1
    sr["checks"] += 1  # a turn done: counted here, so a turn that answers a check (no step 1) counts too
    sr["misses"] += not (result["ok"] and (dec is None or dec["ok"]))
    p["answers"].append({"ok": result["ok"], "threats": needed,
                         "found": len(result["found"]), "extra": len(result["extra"]), "ms": ms, "own": True})
    events.log("own_check", session=pid, san=san, uci=mv.uci(), ok=result["ok"], found=result["found"],
               missed=result["missed"], extra=result["extra"], arrows=arrows, ms=ms, series=p["series_no"])
    extra: dict = {"own": {"result": result, "threats": key["threats"], "attacks": key["attacks"], "move": mv.uci(),
                           "san": san, "before": before_fen}}
    if dec is not None:
        extra["decision"] = dec
    lost = not board.is_game_over() and _lost(p)  # your move lost the game: the series ends here
    if board.is_game_over() or lost or sr["checks"] >= sr["length"]:  # no move of his after this one
        p["phase"] = "next"
        extra["series_end"] = _end_series(p, game_over=board.is_game_over(), lost=lost)
        extra["last"] = False  # the sitting goes on until the user leaves
    else:
        p["phase"] = "bot"
    return _view(p, extra)


def next_series(pid: str) -> dict:
    """«Дальше»: the bot's answer to the user's move, or the next series, or the summary."""
    p = PLAYS[pid]
    if p["phase"] == "bot":
        extra = _bot_turn(p)
        if p["phase"] == "next":
            extra["last"] = False
        return _view(p, extra)
    if p["phase"] != "next":
        raise ValueError("серия ещё идёт")
    return _new_series(p)  # the next one at once: the sitting ends when the user leaves


def finish(pid: str) -> dict:
    p = PLAYS[pid]
    if "summary" in p:
        return p["summary"]
    ans = p["answers"]
    done = p["series_done"]
    summary = {
        "checks": len(ans), "ok": sum(1 for a in ans if a["ok"]), "series_end": done[-1] if done else None,
        "series": len(done), "clean": sum(1 for s in done if s["clean"]),
        "level": p["level"], "best": best(),
        "median_ms": int(sorted(a["ms"] for a in ans)[len(ans) // 2]) if ans else 0,
        "complete": len(ans) >= CHECKS,
    }
    p["summary"] = summary
    store.run("UPDATE sessions SET ended=?, summary=? WHERE id=?", (store.now_ms(), json.dumps(summary), pid))
    events.log("session_end", session=pid, summary=summary)
    return summary


def leave(pid: str) -> dict:
    p = PLAYS.get(pid)
    if p is None or "summary" in p:
        return {"left": False}
    events.log("session_exit", session=pid, index=p["checks"], size=CHECKS, answered=p["checks"])
    return {"left": True, "summary": finish(pid)}
