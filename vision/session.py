"""A sitting: 15 positions, mixed — repeats of positions answered wrongly, positions with a threat and quiet ones.

The share of positions with a threat starts at a half and goes down as the user answers well (a real game has
far more quiet moves); the window gets no hint of which kind a position is or where it comes from until the
answer is checked.
"""

import json
import random
import statistics
import uuid

import chess

from . import events, store, threats

SIZE = 15
REPEAT_SHARE = 0.3
THREAT_SOURCES = {"own": 0.3, "db": 0.4, "puzzle": 0.3}
QUIET_SOURCES = {"own": 0.4, "db": 0.6}
SOURCE_NAME = {"own": "твоя партия", "db": "чужая партия", "puzzle": "задача Lichess"}
MOTIF_NAME = {
    "mateIn1": "мат в один ход", "mateIn2": "мат в два хода", "fork": "вилка", "skewer": "сквозной удар",
    "discoveredAttack": "вскрытое нападение", "pin": "связка", "hangingPiece": "незащищённая фигура",
    "trappedPiece": "пойманная фигура", "xRayAttack": "рентген", "attraction": "завлечение",
    "deflection": "отвлечение", "interference": "перекрытие", "intermezzo": "промежуточный ход",
    "doubleCheck": "двойной шах",
}

SESSIONS: dict[str, dict] = {}


def threat_share() -> float:
    """0.5 at first; 0.4 after 150 answers; 0.3 after 400 answers if the last 100 threat positions were mostly found."""
    n = store.answered_count()
    if n < 150:
        return 0.5
    if n < 400:
        return 0.4
    rows = store.q("SELECT ok FROM attempts WHERE n_threats > 0 ORDER BY id DESC LIMIT 100")
    miss = sum(1 for r in rows if not r["ok"]) / max(1, len(rows))
    return 0.3 if miss < 0.15 else 0.4


def _pick(kind_sql: str, weights: dict[str, float], n: int, exclude: set[int]) -> list[int]:
    """`n` random positions of one kind, split over the sources by `weights`; new ones first, shortfalls filled
    from any source, then from positions already seen."""
    if n <= 0:
        return []
    out: list[int] = []
    seen_sql = "SELECT item FROM attempts"

    def take(sql: str, args: tuple, k: int) -> None:
        added = 0
        for r in store.q(sql, args):
            if added >= k or len(out) >= n:
                break
            if r["id"] in exclude or r["id"] in out:
                continue
            out.append(r["id"])
            added += 1

    for source, w in weights.items():
        take(f"SELECT id FROM items WHERE source=? AND {kind_sql} AND id NOT IN ({seen_sql}) ORDER BY random() LIMIT ?",
             (source, round(n * w) + 5), round(n * w))
    sources = ",".join(f"'{s}'" for s in weights)
    take(f"SELECT id FROM items WHERE source IN ({sources}) AND {kind_sql} AND id NOT IN ({seen_sql})"
         f" ORDER BY random() LIMIT ?", (n * 3,), n - len(out))
    take(f"SELECT id FROM items WHERE source IN ({sources}) AND {kind_sql} ORDER BY random() LIMIT ?",
         (n * 3,), n - len(out))
    return out[:n]


def start(size: int = SIZE) -> dict:
    due = [r["item"] for r in store.q("SELECT item FROM schedule WHERE due <= ? ORDER BY due LIMIT ?",
                                       (store.now_ms(), round(size * REPEAT_SHARE)))]
    rest = size - len(due)
    n_threat = round(rest * threat_share())
    exclude = set(due)
    picked_threat = _pick("n_threats > 0", THREAT_SOURCES, n_threat, exclude)
    exclude |= set(picked_threat)
    picked_quiet = _pick("n_threats = 0 AND quiet_ok = 1", QUIET_SOURCES, rest - len(picked_threat), exclude)
    items = due + picked_threat + picked_quiet
    random.shuffle(items)
    sid = uuid.uuid4().hex[:8]
    SESSIONS[sid] = {"id": sid, "items": items, "repeat": set(due), "index": 0, "answers": {}, "started": store.now_ms()}
    store.run("INSERT INTO sessions (id, started, items) VALUES (?, ?, ?)", (sid, store.now_ms(), json.dumps(items)))
    events.log("session_start", session=sid, size=len(items), repeats=len(due), threat_items=len(picked_threat),
               quiet_items=len(picked_quiet), share=threat_share())
    return show(sid)


def _boards(it: dict) -> tuple[chess.Board, chess.Move, chess.Board]:
    before = chess.Board(it["fen"])
    move = chess.Move.from_uci(it["move"])
    after = before.copy(stack=False)
    after.push(move)
    return before, move, after


def show(sid: str) -> dict:
    """The current position of a session, without anything that hints at the answer."""
    s = SESSIONS[sid]
    if s["index"] >= len(s["items"]):
        return {"session": sid, "done": True, "summary": finish(sid)}
    it = store.item(s["items"][s["index"]])
    assert it is not None
    before, move, after = _boards(it)
    events.log("item_shown", session=sid, index=s["index"], size=len(s["items"]), item=it["id"], source=it["source"],
               motif=it["motif"], threats=it["n_threats"], san=before.san(move), repeat=it["id"] in s["repeat"],
               fen=it["fen"], move=it["move"])
    return {
        "session": sid, "index": s["index"], "size": len(s["items"]), "item": it["id"], "done": False,
        "fen": it["fen"], "after": after.fen(), "move": it["move"], "san": before.san(move),
        "capture": before.is_capture(move), "orientation": "white" if it["side"] == "w" else "black",
    }


def answer(sid: str, index: int, arrows: list[dict], ms: int) -> dict:
    s = SESSIONS[sid]
    if index != s["index"] or index in s["answers"]:
        raise ValueError("эта позиция уже проверена")
    it = store.item(s["items"][index])
    assert it is not None
    _, move, after = _boards(it)
    key = threats.analyse(after) or {"threats": [], "defended": []}
    arrows = [{"from": str(a.get("from")), "to": str(a.get("to"))} for a in arrows[:30] if isinstance(a, dict)]
    result = threats.check(key, arrows, after)
    store.record(it["id"], sid, ms, len(key["threats"]), result, arrows)
    s["answers"][index] = {"ms": ms, "threats": len(key["threats"]), "found": len(result["found"]),
                           "extra": len(result["extra"]), "ok": result["ok"]}
    events.log("answer", session=sid, index=index, size=len(s["items"]), item=it["id"], ok=result["ok"],
               threats=len(key["threats"]), found=result["found"], missed=result["missed"], extra=result["extra"],
               arrows=arrows, ms=ms, source=it["source"], motif=it["motif"])
    ref = it["ref"] or ""
    if it["source"] in ("own", "db") and ref.startswith("https://lichess.org/"):
        ref = f"{ref}#{(it['ply'] or 0) + 1}"
    return {
        "result": result, "threats": key["threats"], "defended": key["defended"], "move": move.uci(),
        "source": SOURCE_NAME.get(it["source"], it["source"]), "motif": MOTIF_NAME.get(it["motif"] or "", ""),
        "ref": ref, "repeat": it["id"] in s["repeat"],
    }


def next_item(sid: str) -> dict:
    s = SESSIONS[sid]
    if s["index"] in s["answers"]:
        s["index"] += 1
    return show(sid)


def finish(sid: str) -> dict:
    s = SESSIONS[sid]
    if "summary" in s:
        return s["summary"]
    ans = list(s["answers"].values())
    threat = [a for a in ans if a["threats"]]
    quiet = [a for a in ans if not a["threats"]]
    summary = {
        "positions": len(ans),
        "threats": sum(a["threats"] for a in threat), "found": sum(a["found"] for a in threat),
        "threat_ok": sum(1 for a in threat if a["ok"]), "threat_positions": len(threat),
        "quiet": len(quiet), "quiet_ok": sum(1 for a in quiet if a["ok"]),
        "extra": sum(a["extra"] for a in ans),
        "median_ms": int(statistics.median(a["ms"] for a in ans)) if ans else 0,
        "size": len(s["items"]), "complete": len(ans) == len(s["items"]),
        "score": _score(_first_answers(" AND a.session = ?", (sid,))),
    }
    s["summary"] = summary
    store.run("UPDATE sessions SET ended=?, summary=? WHERE id=?", (store.now_ms(), json.dumps(summary), sid))
    events.log("session_end", session=sid, summary=summary)
    return summary


def leave(sid: str) -> dict:
    """The user went back to the start screen before the end: the session ends with what was answered."""
    s = SESSIONS.get(sid)
    if s is None or "summary" in s:
        return {"left": False}
    events.log("session_exit", session=sid, index=s["index"], size=len(s["items"]),
               answered=len(s["answers"]), on_answered=s["index"] in s["answers"])
    return {"left": True, "summary": finish(sid)}


def _first_answers(where: str = "", args: tuple = ()) -> list:
    """Answers to positions seen for the first time (a repeat can be remembered, so it does not count)."""
    return store.q(
        "SELECT a.session, a.ts, a.n_threats > 0 AS threat, a.ok, a.ms FROM attempts a"
        " WHERE a.id = (SELECT MIN(b.id) FROM attempts b WHERE b.item = a.item)" + where + " ORDER BY a.ts", args)


def _score(rows: list) -> dict:
    """Right answers, %, with positions with a threat and quiet ones weighing the same — so the share of quiet
    positions (which grows over time) does not move it."""
    t = [r["ok"] for r in rows if r["threat"]]
    q = [r["ok"] for r in rows if not r["threat"]]
    parts = [sum(x) / len(x) for x in (t, q) if x]
    return {"pct": round(100 * sum(parts) / len(parts)) if parts else None, "n": len(rows),
            "threat_ok": sum(t), "threat_n": len(t), "quiet_ok": sum(q), "quiet_n": len(q)}


def progress(limit: int = 40) -> dict:
    """The chart: the attention rating after each session (vision/rating.py) and the median seconds per position.
    {"start": 1500, "rating": current, "points": [{session, ts, rating, n, median_s}, ...] oldest first}."""
    from . import rating

    by: dict[str, dict] = {}
    for r in rating.replay():
        p = by.setdefault(r["session"], {"session": r["session"], "ts": r["ts"], "n": 0, "ms": []})
        p["rating"] = round(r["rating"])
        p["n"] += 1
        if r["ms"]:
            p["ms"].append(r["ms"])
    points = sorted((p for p in by.values() if p["n"] >= 3), key=lambda p: p["ts"])  # a sitting of 1–2 answers is noise
    for p in points:
        ms = p.pop("ms")
        p["median_s"] = round(statistics.median(ms) / 1000, 1) if ms else None
    return {"start": rating.START, "rating": points[-1]["rating"] if points else rating.START, "points": points[-limit:]}


def history(days: int = 30) -> list[dict]:
    """Per day: sessions, threats found of all, quiet answered right of all, median time."""
    rows = store.q(
        "SELECT date(ts/1000, 'unixepoch', 'localtime') AS day, COUNT(DISTINCT session) AS sessions,"
        " SUM(n_threats) AS threats, SUM(CASE WHEN n_threats>0 THEN found ELSE 0 END) AS found,"
        " SUM(n_threats=0) AS quiet, SUM(n_threats=0 AND ok) AS quiet_ok, COUNT(*) AS positions,"
        " SUM(extra) AS extra FROM attempts GROUP BY day ORDER BY day DESC LIMIT ?", (days,))
    out = []
    for r in rows:
        ms = [x["ms"] for x in store.q(
            "SELECT ms FROM attempts WHERE date(ts/1000, 'unixepoch', 'localtime') = ?", (r["day"],)) if x["ms"]]
        out.append({**dict(r), "median_ms": int(statistics.median(ms)) if ms else 0})
    return out
