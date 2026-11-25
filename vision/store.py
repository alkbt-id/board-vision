"""vision.db (SQLite): the positions, every answer, the repeat schedule and the sessions.

items      one drill position: the position before the opponent's move (`fen`), his move (`move`, UCI), the
           user's side (`side`: "w"/"b"), the answer key (`answer`, JSON from vision/threats.py), `n_threats`,
           where it comes from (`source`: own / db / puzzle, `ref`: game or puzzle id, `ply`), `motif` (a Lichess
           puzzle theme), `rating`; `quiet_ok`: for a position without threats, 1 = Stockfish agrees nothing is
           threatened, 0 = it sees a hidden threat (never shown), NULL = not checked yet
attempts   one answer: arrows, what was found / missed / extra, time
schedule   repeats of positions answered wrongly: step on the ladder and when due
sessions   one sitting: the planned positions and when it started / ended
meta       small key-value state (last download times)
tasks      a series start prepared as a task for the move (vision/tasks.py): its kind (threat, tactic, quiet, trap,
           strategy, endgame), the moves that count and the line, the rating on the game scale, Claude's explanation;
           `status`: engine (Stockfish and Maia done, Claude not yet) / ready / rejected (Claude or the engine said no)
decisions  one try at a task: right or not, the moves, the time, the task's rating then (for the «Решения» rating)
skips      candidates looked at for a kind of task and not fit for it (they may still fit another kind)
web_cache  answers of the Lichess API (puzzles, games, tablebase) already fetched (vision/sources/lichess_api.py)
"""

import json
import sqlite3
import threading
import time

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
  id INTEGER PRIMARY KEY, key TEXT UNIQUE NOT NULL, source TEXT NOT NULL, ref TEXT, ply INTEGER,
  fen TEXT NOT NULL, move TEXT NOT NULL, side TEXT NOT NULL, answer TEXT NOT NULL, n_threats INTEGER NOT NULL,
  motif TEXT, rating INTEGER, quiet_ok INTEGER, added INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS items_pick ON items (source, n_threats, quiet_ok);
CREATE TABLE IF NOT EXISTS attempts (
  id INTEGER PRIMARY KEY, item INTEGER NOT NULL, session TEXT NOT NULL, ts INTEGER NOT NULL, ms INTEGER,
  n_threats INTEGER, found INTEGER, missed INTEGER, extra INTEGER, ok INTEGER, arrows TEXT, result TEXT
);
CREATE INDEX IF NOT EXISTS attempts_item ON attempts (item);
CREATE TABLE IF NOT EXISTS schedule (item INTEGER PRIMARY KEY, step INTEGER NOT NULL, due INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS sessions (
  id TEXT PRIMARY KEY, started INTEGER NOT NULL, ended INTEGER, items TEXT NOT NULL, summary TEXT
);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS tasks (
  item INTEGER PRIMARY KEY, kind TEXT NOT NULL, status TEXT NOT NULL, rating INTEGER, origin TEXT,
  data TEXT NOT NULL, note TEXT, added INTEGER NOT NULL, prepared INTEGER
);
CREATE INDEX IF NOT EXISTS tasks_pick ON tasks (status, kind);
CREATE TABLE IF NOT EXISTS decisions (
  id INTEGER PRIMARY KEY, item INTEGER NOT NULL, session TEXT, series INTEGER, ts INTEGER NOT NULL, kind TEXT,
  ok INTEGER NOT NULL, moves TEXT, ms INTEGER, rating INTEGER, repeat INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS decisions_item ON decisions (item);
CREATE TABLE IF NOT EXISTS web_cache (key TEXT PRIMARY KEY, body TEXT NOT NULL, ts INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS skips (kind TEXT NOT NULL, ref TEXT NOT NULL, reason TEXT, PRIMARY KEY (kind, ref));
"""
DAY = 86_400_000
LADDER = [1, 3, 7, 14, 30]  # days until the next repeat of a position answered wrongly, step by step

_lock = threading.RLock()
_db: sqlite3.Connection | None = None


def db() -> sqlite3.Connection:
    global _db
    with _lock:
        if _db is None:
            config.HOME.mkdir(parents=True, exist_ok=True)
            _db = sqlite3.connect(config.DB, check_same_thread=False, isolation_level=None)
            _db.row_factory = sqlite3.Row
            _db.execute("PRAGMA journal_mode=WAL")
            _db.executescript(SCHEMA)
            try:  # the series of a sitting an answer belongs to (2026-11-24: the chart has a point per series)
                _db.execute("ALTER TABLE attempts ADD COLUMN series INTEGER")
            except sqlite3.OperationalError:
                pass  # there already
        return _db


def q(sql: str, args: tuple | list = ()) -> list[sqlite3.Row]:
    with _lock:
        return db().execute(sql, args).fetchall()


def run(sql: str, args: tuple | list = ()) -> int:
    with _lock:
        cur = db().execute(sql, args)
        return cur.lastrowid or 0


def now_ms() -> int:
    return int(time.time() * 1000)


def add_item(source: str, fen: str, move: str, side: str, answer: dict, ref: str = "", ply: int = 0,
             motif: str = "", rating: int | None = None) -> bool:
    """A new position; False when it is already there (same source, position and move)."""
    key = f"{source}|{fen}|{move}"
    with _lock:
        cur = db().execute(
            "INSERT OR IGNORE INTO items (key, source, ref, ply, fen, move, side, answer, n_threats, motif, rating, added)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (key, source, ref, ply, fen, move, side, json.dumps(answer, ensure_ascii=False),
             sum(1 for t in answer["threats"] if not t.get("optional")),  # the marks to make
             motif or None, rating, now_ms()))
        return cur.rowcount > 0


def item(item_id: int) -> dict | None:
    rows = q("SELECT * FROM items WHERE id=?", (item_id,))
    if not rows:
        return None
    out = dict(rows[0])
    out["answer"] = json.loads(out["answer"])
    return out


def counts() -> dict:
    rows = q("SELECT source, n_threats > 0 AS threat, quiet_ok, COUNT(*) AS n FROM items GROUP BY 1, 2, 3")
    out = {"own": 0, "db": 0, "puzzle": 0, "threat": 0, "quiet": 0, "quiet_unchecked": 0, "total": 0}
    for r in rows:
        out[r["source"]] = out.get(r["source"], 0) + r["n"]
        out["total"] += r["n"]
        if r["threat"]:
            out["threat"] += r["n"]
        elif r["quiet_ok"] == 1:
            out["quiet"] += r["n"]
        elif r["quiet_ok"] is None:
            out["quiet_unchecked"] += r["n"]
    return out


def meta_get(key: str, default: str = "") -> str:
    rows = q("SELECT value FROM meta WHERE key=?", (key,))
    return rows[0]["value"] if rows else default


def meta_set(key: str, value: str) -> None:
    run("INSERT INTO meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))


def schedule_again(item_id: int) -> None:
    """The position comes back tomorrow and climbs the repeat ladder from the start (a miss after the user's own move
    in it: the miss is recorded on the move, the repeat is of the position)."""
    run("INSERT INTO schedule (item, step, due) VALUES (?, 0, ?)"
        " ON CONFLICT(item) DO UPDATE SET step=0, due=excluded.due", (item_id, now_ms() + LADDER[0] * DAY))


def record(item_id: int, session: str, ms: int, n_threats: int, result: dict, arrows: list,
           series: int | None = None) -> None:
    """An answer, and the repeat ladder: a missed threat (or a false alarm on a quiet position) brings the
    position back tomorrow; a right answer on a scheduled position moves it up the ladder, off after the last step."""
    ok = bool(result["ok"])
    run("INSERT INTO attempts (item, session, ts, ms, n_threats, found, missed, extra, ok, arrows, result, series)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (item_id, session, now_ms(), ms, n_threats, len(result["found"]), len(result["missed"]), len(result["extra"]),
         int(ok), json.dumps(arrows), json.dumps(result, ensure_ascii=False), series))
    rows = q("SELECT step FROM schedule WHERE item=?", (item_id,))
    if not ok:
        run("INSERT INTO schedule (item, step, due) VALUES (?, 0, ?)"
            " ON CONFLICT(item) DO UPDATE SET step=0, due=excluded.due", (item_id, now_ms() + LADDER[0] * DAY))
    elif rows:
        step = rows[0]["step"] + 1
        if step >= len(LADDER):
            run("DELETE FROM schedule WHERE item=?", (item_id,))
        else:
            run("UPDATE schedule SET step=?, due=? WHERE item=?", (step, now_ms() + LADDER[step] * DAY, item_id))


def answered_count() -> int:
    return q("SELECT COUNT(*) AS n FROM attempts")[0]["n"]
