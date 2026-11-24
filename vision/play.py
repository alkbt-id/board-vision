"""The one training mode: series of checks, one game fragment each, getting longer as the user gets them right.

A sitting has about 15 checks. It is made of series; a series of length L is a fragment of a game with L checks:
  1. a position from the pool (vision/sources: the user's games, other people's, Lichess puzzles turned around, or
     a position due for a repeat) and the opponent's real move in it — the first check;
  2. if L > 1: the user makes his move on the board, the bot (Maia 1300, vision/bot.py) answers, check again —
     until L checks are done or the game ends. A position where the bot gives check is not a check: the user just
     answers it.
L is the user's level (meta series_length, starts at 1): a series without a miss makes it one longer (max 30), two
missed series in a row make it one shorter (min 1). So at level 1 it is the plain «one position, one move» drill.
The first position of each series follows the old mix: repeats first (up to 30% of a sitting), then positions with a
threat or quiet ones by vision/session.threat_share(); the window gets no hint of the kind or the source.

Every check is an answer in `attempts` (the first one on the pool position, later ones on positions stored with
source "series"), so all of them count in the attention rating and the repeat ladder.
"""

import json
import random
import time
import uuid

import chess

from . import bot, events, session, store, threats

CHECKS = 15
REPEAT_SHARE = 0.3
MAX_LEVEL = 30
PLAYS: dict[str, dict] = {}


def level() -> int:
    return max(1, int(store.meta_get("series_length", "1") or 1))


def best() -> int:
    return int(store.meta_get("series_best", "0") or 0)


def _side(color: chess.Color) -> str:
    return "white" if color == chess.WHITE else "black"


def _pick_start(p: dict) -> dict:
    """The first position of the next series: a due repeat (while repeats stay under 30% of the sitting), else a
    position with a threat or a quiet one by the current share."""
    if p["repeats"] < round(CHECKS * REPEAT_SHARE):
        due = store.q("SELECT item FROM schedule WHERE due <= ? ORDER BY due LIMIT 20", (store.now_ms(),))
        for r in due:
            if r["item"] not in p["used"]:
                p["repeats"] += 1
                it = store.item(r["item"])
                if it:
                    it["repeat"] = True
                    return it
    if random.random() < session.threat_share():
        ids = session._pick("n_threats > 0", session.THREAT_SOURCES, 1, p["used"])
    else:
        ids = session._pick("n_threats = 0 AND quiet_ok = 1", session.QUIET_SOURCES, 1, p["used"])
    if not ids:
        ids = session._pick("n_threats > 0", session.THREAT_SOURCES, 1, p["used"])
    it = store.item(ids[0])
    assert it is not None
    it["repeat"] = False
    return it


def _view(p: dict, extra: dict | None = None) -> dict:
    board: chess.Board = p["board"]
    sr = p["series"]
    out = {
        "play": p["id"], "phase": p["phase"], "checks": p["checks"], "budget": CHECKS, "fen": board.fen(),
        "orientation": _side(p["user"]),
        "series": {"no": p["series_no"], "length": sr["length"], "checks": sr["checks"]},
    }
    if p["phase"] == "move":
        d: dict[str, list[str]] = {}
        for m in board.legal_moves:
            d.setdefault(chess.square_name(m.from_square), []).append(chess.square_name(m.to_square))
        out["dests"] = d
    if extra:
        out.update(extra)
    return out


def _new_series(p: dict) -> dict:
    it = _pick_start(p)
    p["used"].add(it["id"])
    before = chess.Board(it["fen"])
    move = chess.Move.from_uci(it["move"])
    p["user"] = not before.turn
    board = before.copy()
    board.push(move)
    p["board"] = board
    p["series_no"] += 1
    p["series"] = {"length": p["level"], "checks": 0, "misses": 0}
    p["item"] = it["id"]  # the first check is answered on the pool position itself
    p["last"] = {"fen": it["fen"], "move": it["move"]}
    p["phase"] = "check"
    events.log("series_start", session=p["id"], no=p["series_no"], length=p["level"], item=it["id"],
               source=it["source"], motif=it["motif"], threats=it["n_threats"], repeat=it["repeat"], fen=it["fen"],
               move=it["move"], user=_side(p["user"]))
    return _view(p, {"bot": {"before": it["fen"], "move": it["move"], "san": before.san(move),
                             "capture": before.is_capture(move)}, "new_series": True})


def start() -> dict:
    pid = uuid.uuid4().hex[:8]
    p = {"id": pid, "level": level(), "checks": 0, "series_no": 0, "used": set(), "repeats": 0, "answers": [],
         "series_done": [], "started": store.now_ms()}
    PLAYS[pid] = p
    store.run("INSERT INTO sessions (id, started, items) VALUES (?, ?, ?)", (pid, store.now_ms(), "[]"))
    events.log("session_start", session=pid, size=CHECKS, level=p["level"], share=session.threat_share())
    return _new_series(p)


def _bot_turn(p: dict) -> dict:
    board: chess.Board = p["board"]
    before = board.copy()
    t0 = time.time()
    mv = bot.move(board)
    san, capture = board.san(mv), board.is_capture(mv)
    board.push(mv)
    p["last"] = {"fen": before.fen(), "move": mv.uci()}
    p["item"] = None
    events.log("bot_move", series=p["id"], san=san, uci=mv.uci(), ms=int((time.time() - t0) * 1000), fen=before.fen())
    shown: dict = {"bot": {"before": before.fen(), "move": mv.uci(), "san": san, "capture": capture}}
    if board.is_game_over():
        shown["series_end"] = _end_series(p, game_over=True)
        p["phase"] = "next"
    elif board.is_check():
        p["phase"] = "move"  # answer the check; not a check of the drill
        shown["check"] = True
    else:
        p["phase"] = "check"
    return shown


def _end_series(p: dict, game_over: bool = False) -> dict:
    sr = p["series"]
    clean = sr["misses"] == 0 and sr["checks"] >= sr["length"]
    fails = int(store.meta_get("series_fails", "0") or 0)
    lvl = p["level"]
    if clean:
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
           "game_over": game_over, "next_level": lvl, "level_was": p["level"]}
    p["level"] = lvl
    p["series_done"].append(out)
    events.log("series_end", series=p["id"], summary=out)
    return out


def answer(pid: str, arrows: list[dict], ms: int) -> dict:
    p = PLAYS[pid]
    if p["phase"] != "check":
        raise ValueError("сейчас не проверка")
    board: chess.Board = p["board"]
    key = threats.analyse(board) or {"threats": [], "defended": []}
    arrows = [{"from": str(a.get("from")), "to": str(a.get("to"))} for a in arrows[:30] if isinstance(a, dict)]
    result = threats.check(key, arrows, board)
    item_id = p["item"]
    if item_id is None:  # a position reached in play: store it like a pool position
        store.add_item("series", p["last"]["fen"], p["last"]["move"], "w" if p["user"] == chess.WHITE else "b", key,
                       ref=pid, ply=len(board.move_stack))
        row = store.q("SELECT id FROM items WHERE key = ?", (f"series|{p['last']['fen']}|{p['last']['move']}",))
        item_id = row[0]["id"] if row else None
    if item_id is not None:
        store.record(item_id, pid, ms, len(key["threats"]), result, arrows)
    sr = p["series"]
    p["checks"] += 1
    sr["checks"] += 1
    sr["misses"] += not result["ok"]
    p["answers"].append({"ok": result["ok"], "threats": len(key["threats"]), "found": len(result["found"]),
                         "extra": len(result["extra"]), "ms": ms})
    events.log("answer", session=pid, index=p["checks"] - 1, size=CHECKS, item=item_id, ok=result["ok"],
               threats=len(key["threats"]), found=result["found"], missed=result["missed"], extra=result["extra"],
               arrows=arrows, ms=ms, series=p["series_no"], step=sr["checks"])
    extra: dict = {"result": result, "threats": key["threats"]}
    if sr["checks"] >= sr["length"] or board.is_game_over():
        extra["series_end"] = _end_series(p, game_over=board.is_game_over())
        p["phase"] = "next"
    else:
        p["phase"] = "move"
    if p["phase"] == "next":
        extra["last"] = p["checks"] >= CHECKS
    return _view(p, extra)


def move(pid: str, uci: str, ms: int) -> dict:
    p = PLAYS[pid]
    if p["phase"] != "move":
        raise ValueError("сейчас не твой ход")
    board: chess.Board = p["board"]
    mv = chess.Move.from_uci(uci)
    if mv not in board.legal_moves:
        mv = chess.Move.from_uci(uci + "q")  # a pawn reaching the last rank becomes a queen
        if mv not in board.legal_moves:
            raise ValueError("такого хода нет")
    events.log("user_move", series=pid, san=board.san(mv), uci=mv.uci(), ms=ms)
    board.push(mv)
    if board.is_game_over():
        p["phase"] = "next"
        return _view(p, {"series_end": _end_series(p, game_over=True), "last": p["checks"] >= CHECKS})
    extra = _bot_turn(p)
    if p["phase"] == "next":
        extra["last"] = p["checks"] >= CHECKS
    return _view(p, extra)


def next_series(pid: str) -> dict:
    p = PLAYS[pid]
    if p["phase"] != "next":
        raise ValueError("серия ещё идёт")
    if p["checks"] >= CHECKS:
        return {"play": pid, "phase": "over", "summary": finish(pid)}
    return _new_series(p)


def finish(pid: str) -> dict:
    p = PLAYS[pid]
    if "summary" in p:
        return p["summary"]
    ans = p["answers"]
    done = p["series_done"]
    summary = {
        "checks": len(ans), "ok": sum(1 for a in ans if a["ok"]),
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
