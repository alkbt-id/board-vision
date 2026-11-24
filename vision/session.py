"""Picking pool positions, and the numbers behind the start screen.

The share of positions with a threat starts at a half and goes down as the user answers well (a real game has far
more quiet moves). The training itself (series of checks) is vision/play.py.
"""

import json
import random
import statistics
import uuid

import chess

from . import events, store, threats

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
