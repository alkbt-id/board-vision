"""Picking pool positions, and the numbers behind the start screen.

A series starts on a position with a threat in 60% of cases (the user, 2026-11-24: «может пора уже более острые
добавлять?» — quiet ones were 95% right); of those positions, the one is taken whose difficulty (vision/rating.py)
is nearest to the user's rating plus a little, so they get sharper as he gets better.
The training itself (series of checks) is vision/play.py.
"""

import json
import random
import statistics
import uuid

import chess

from . import events, store, threats

THREAT_SOURCES = {"own": 0.25, "db": 0.1, "missed": 0.3, "puzzle": 0.35}
QUIET_SOURCES = {"own": 0.4, "db": 0.6}
SOURCE_NAME = {"own": "твоя партия", "db": "чужая партия", "puzzle": "задача Lichess", "missed": "зевок в партии"}
SHARE = 0.6  # series that start on a position with something to find
# …his threat there, or a chance of yours (a Lichess puzzle from the solver's side has its blow to find)
SHARP = "(n_threats > 0 OR source = 'puzzle' OR json_array_length(answer, '$.chances') > 0)"
ABOVE = 25  # the difficulty aimed at: the rating plus this (a right answer a little less than half the time)
CANDIDATES = 40  # new positions of a source compared by difficulty for one pick
MOTIF_NAME = {
    "mateIn1": "мат в один ход", "mateIn2": "мат в два хода", "fork": "вилка", "skewer": "сквозной удар",
    "discoveredAttack": "вскрытое нападение", "pin": "связка", "hangingPiece": "незащищённая фигура",
    "trappedPiece": "пойманная фигура", "xRayAttack": "рентген", "attraction": "завлечение",
    "deflection": "отвлечение", "interference": "перекрытие", "intermezzo": "промежуточный ход",
    "doubleCheck": "двойной шах",
    "missed": "не увидел угрозу", "mate": "не увидел мат",  # vision/sources/missed.py
}


def threat_share() -> float:
    return SHARE


def pick_near(kind_sql: str, weights: dict[str, float], exclude: set[int], rating: float,
              points: dict[str, float]) -> int | None:
    """One new position of `kind_sql`: a source drawn by `weights`, then of CANDIDATES random new ones of it, one of
    the three whose difficulty is nearest to `rating` + ABOVE. None when that source has nothing new."""
    from . import rating as rating_mod

    sources = list(weights)
    source = random.choices(sources, [weights[s] for s in sources])[0]
    rows = [dict(r) for r in store.q(
        f"SELECT id, fen, move, answer, source FROM items WHERE source=? AND {kind_sql}"
        " AND id NOT IN (SELECT item FROM attempts) ORDER BY random() LIMIT ?", (source, CANDIDATES))
        if r["id"] not in exclude]
    if not rows:
        return None
    target = rating + ABOVE
    rows.sort(key=lambda r: abs(rating_mod.difficulty(r, points) - target))
    return random.choice(rows[:3])["id"]


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


def progress(limit: int | None = None) -> dict:
    """The chart: the attention rating after each played-out position — a series (the user, 2026-11-24: «просто
    пересчитывай после каждой позиции»); answers from before series were stored count per session.
    {"start": 1500, "rating": current, "points": [{session, ts, rating, n, median_s}, ...] oldest first}."""
    from . import rating

    by: dict[str, dict] = {}
    for r in rating.replay():
        key = f"{r['session']}/{r['series']}" if r.get("series") is not None else r["session"]
        p = by.setdefault(key, {"session": key, "ts": r["ts"], "n": 0, "ms": [], "min": 1 if "/" in key else 3})
        p["rating"] = round(r["rating"])
        p["n"] += 1
        if r["ms"]:
            p["ms"].append(r["ms"])
    points = sorted((p for p in by.values() if p["n"] >= p["min"]), key=lambda p: p["ts"])  # an old sitting of 1–2 answers is noise
    for p in points:
        p.pop("min")
        ms = p.pop("ms")
        p["median_s"] = round(statistics.median(ms) / 1000, 1) if ms else None
    # every point: the trend over weeks is what tells whether the method works (a side note, 2026-11-24)
    return {"start": rating.START, "rating": points[-1]["rating"] if points else rating.START,
            "points": points[-limit:] if limit else points}


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
