"""A series: a short game against the bot (Maia 1300) with a check after every bot move.

After each bot move the user marks what is now under attack (vision/threats.py, computed live for whatever position
the game has reached), sees the review, then makes his own move on the board. A series ends after `length` checks
(or when the game ends). A series without a miss makes the next one a move longer; with a miss the length stays.
The start: a position after the opening (moves 8–12) from the user's games or from other people's games, half and
half; the initial position only when there are no such positions yet (the user: no point starting from move 1). A position where the bot gives check is no drill: the user just answers the check.

Every check is stored like a drill position (items.source = "series") and an answer (attempts.session = the series
id), so it counts in the attention rating; the series is a row in `sessions` with summary.kind = "series".
"""

import json
import random
import time
import uuid

import chess

from . import bot, events, store, threats

START_LENGTH = 3
MAX_LENGTH = 30
MIX = (("own", 0.5), ("db", 0.5))
SERIES: dict[str, dict] = {}


def length() -> int:
    return int(store.meta_get("series_length", str(START_LENGTH)) or START_LENGTH)


def best() -> int:
    return int(store.meta_get("series_best", "0") or 0)


def _start_position(rng: random.Random) -> tuple[chess.Board, chess.Color, str]:
    """(board with the bot or the user to move, the user's colour, where it comes from)."""
    kind = rng.choices([k for k, _ in MIX], weights=[w for _, w in MIX], k=1)[0]
    for source in (kind, *(k for k, _ in MIX if k != kind)):
        rows = store.q("SELECT fen, side FROM items WHERE source = ? AND ply BETWEEN 14 AND 24 ORDER BY random() LIMIT 1",
                       (source,))
        if rows:
            board = chess.Board(rows[0]["fen"])  # the opponent (the bot) is to move here
            return board, chess.WHITE if rows[0]["side"] == "w" else chess.BLACK, source
    return chess.Board(), rng.choice([chess.WHITE, chess.BLACK]), "start"


def _view(s: dict, extra: dict | None = None) -> dict:
    board: chess.Board = s["board"]
    out = {
        "series": s["id"], "length": s["length"], "checks": s["checks"], "misses": s["misses"],
        "orientation": "white" if s["user"] == chess.WHITE else "black", "fen": board.fen(),
        "phase": s["phase"],
    }
    if s["phase"] == "move":
        out["dests"] = _dests(board)
    if extra:
        out.update(extra)
    return out


def _dests(board: chess.Board) -> dict[str, list[str]]:
    d: dict[str, list[str]] = {}
    for m in board.legal_moves:
        d.setdefault(chess.square_name(m.from_square), []).append(chess.square_name(m.to_square))
    return d


def _bot_turn(s: dict) -> dict:
    """The bot moves; returns what the window needs to show it, and sets the phase: check, move (the bot gives
    check — nothing to mark) or over."""
    board: chess.Board = s["board"]
    before = board.copy()
    t0 = time.time()
    mv = bot.move(board)
    think = int((time.time() - t0) * 1000)
    san = board.san(mv)
    capture = board.is_capture(mv)
    board.push(mv)
    s["last"] = {"fen": before.fen(), "move": mv.uci()}
    events.log("bot_move", series=s["id"], san=san, uci=mv.uci(), ms=think, fen=before.fen())
    shown: dict = {"bot": {"before": before.fen(), "move": mv.uci(), "san": san, "capture": capture}}
    if board.is_game_over():
        s["phase"] = "over"
    elif board.is_check():
        s["phase"] = "move"  # answer the check; it is not a drill position and does not count
        shown["check"] = True
    else:
        s["phase"] = "check"
        s["shown_at"] = time.time()
    return shown


def start() -> dict:
    rng = random.Random()
    board, user, source = _start_position(rng)
    sid = "s" + uuid.uuid4().hex[:7]
    s = {"id": sid, "board": board, "user": user, "length": length(), "checks": 0, "misses": 0, "phase": "move",
         "source": source, "started": store.now_ms(), "answers": []}
    SERIES[sid] = s
    store.run("INSERT INTO sessions (id, started, items) VALUES (?, ?, ?)", (sid, store.now_ms(), "[]"))
    events.log("series_start", series=sid, length=s["length"], source=source,
               user="white" if user == chess.WHITE else "black", fen=board.fen())
    extra = {}
    if board.turn != user:
        extra = _bot_turn(s)
    return _view(s, extra)


def answer(sid: str, arrows: list[dict], ms: int) -> dict:
    s = SERIES[sid]
    if s["phase"] != "check":
        raise ValueError("сейчас не проверка")
    board: chess.Board = s["board"]
    key = threats.analyse(board) or {"threats": [], "defended": []}
    arrows = [{"from": str(a.get("from")), "to": str(a.get("to"))} for a in arrows[:30] if isinstance(a, dict)]
    result = threats.check(key, arrows, board)
    store.add_item("series", s["last"]["fen"], s["last"]["move"], "w" if s["user"] == chess.WHITE else "b", key, ref=sid,
                   ply=len(board.move_stack))
    row = store.q("SELECT id FROM items WHERE key = ?", (f"series|{s['last']['fen']}|{s['last']['move']}",))
    if row:
        store.record(row[0]["id"], sid, ms, len(key["threats"]), result, arrows)
    s["checks"] += 1
    s["misses"] += not result["ok"]
    s["answers"].append({"ok": result["ok"], "threats": len(key["threats"]), "ms": ms})
    events.log("answer", session=sid, index=s["checks"] - 1, size=s["length"], item=row[0]["id"] if row else None,
               ok=result["ok"], threats=len(key["threats"]), found=result["found"], missed=result["missed"],
               extra=result["extra"], arrows=arrows, ms=ms, source="series")
    s["phase"] = "move" if s["checks"] < s["length"] else "over"
    view = _view(s, {"result": result, "threats": key["threats"]})
    if s["phase"] == "over":
        view["summary"] = finish(sid)
    return view


def move(sid: str, uci: str, ms: int) -> dict:
    s = SERIES[sid]
    if s["phase"] != "move":
        raise ValueError("сейчас не твой ход")
    board: chess.Board = s["board"]
    mv = chess.Move.from_uci(uci)
    if mv not in board.legal_moves:
        mv = chess.Move.from_uci(uci + "q")  # a pawn reaching the last rank becomes a queen
        if mv not in board.legal_moves:
            raise ValueError("такого хода нет")
    events.log("user_move", series=sid, san=board.san(mv), uci=mv.uci(), ms=ms)
    board.push(mv)
    if board.is_game_over():
        s["phase"] = "over"
        return _view(s, {"summary": finish(sid)})
    extra = _bot_turn(s)
    view = _view(s, extra)
    if s["phase"] == "over":
        view["summary"] = finish(sid)
    return view


def finish(sid: str) -> dict:
    s = SERIES[sid]
    if "summary" in s:
        return s["summary"]
    board: chess.Board = s["board"]
    clean = s["misses"] == 0 and s["checks"] >= s["length"]
    nxt = min(MAX_LENGTH, s["length"] + 1) if clean else s["length"]
    store.meta_set("series_length", str(nxt))
    if clean and s["length"] > best():
        store.meta_set("series_best", str(s["length"]))
    summary = {"kind": "series", "length": s["length"], "checks": s["checks"], "misses": s["misses"], "clean": clean,
               "next": nxt, "best": best(), "game_over": board.is_game_over(),
               "result": board.result(claim_draw=True) if board.is_game_over() else None}
    s["summary"] = summary
    store.run("UPDATE sessions SET ended=?, summary=? WHERE id=?", (store.now_ms(), json.dumps(summary), sid))
    events.log("series_end", series=sid, summary=summary)
    return summary


def leave(sid: str) -> dict:
    s = SERIES.get(sid)
    if s is None or "summary" in s:
        return {"left": False}
    events.log("session_exit", session=sid, index=s["checks"], size=s["length"], answered=s["checks"])
    s["checks_left_early"] = True
    summary = finish(sid)
    return {"left": True, "summary": summary}
