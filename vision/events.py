"""The event log: every action of the user and the app, one JSON object per line in events.jsonl.

Each line is written and flushed at once, so the training can be watched live (`python -m vision.watch`, or
`tail -f`) and analysed later. Every event has
  t     local time, ISO with milliseconds
  ts    epoch milliseconds
  kind  what happened (the list below), plus the fields of that kind
Server kinds: app_start, app_stop, rule_update (answers re-checked under a new threat rule), import_start, import_step, import_done, import_error, session_start,
item_shown, answer, own_check (the check of the user's own move), session_exit (left early for the start screen), session_end, error;
tasks for the move (vision/tasks.py): decision (a move of yours in a task), task_ready, task_later, task_rejected,
prepare_short, prepare_error (vision/sources/prepare.py).
Window kinds (POST /api/log, only those in CLIENT_KINDS): move_shown, arrow_add, arrow_del, arrows_clear,
next_click, hidden, visible, blur, focus, idle, active, page_load, error_js. The press of «Проверить» / «Угроз нет»
is the server's `answer` event (its ms and arrows), so it always comes before the result.
"""

import datetime
import json
import threading
import time

from . import config

CLIENT_KINDS = {
    "move_shown", "arrow_add", "arrow_del", "arrows_clear", "check_click", "next_click", "leave_click",
    "plan_move", "plan_rethink",
    "hidden", "visible", "blur", "focus", "idle", "active", "page_load", "error_js",
}
_lock = threading.Lock()
_file = None


def log(kind: str, **data) -> dict:
    """Append one event and flush it; returns the event."""
    global _file
    now = time.time()
    event = {"t": datetime.datetime.fromtimestamp(now).isoformat(timespec="milliseconds"),
             "ts": int(now * 1000), "kind": kind, **data}
    line = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
    with _lock:
        if _file is None:
            config.HOME.mkdir(parents=True, exist_ok=True)
            _file = config.EVENTS.open("a", encoding="utf-8")
        _file.write(line + "\n")
        _file.flush()
    return event


def from_client(payload: dict) -> dict | None:
    """An event sent by the window: only known kinds, small values only (no page can flood the log)."""
    kind = payload.get("kind")
    if kind not in CLIENT_KINDS:
        return None
    data = {}
    for key, value in payload.items():
        if key == "kind" or not isinstance(key, str) or len(key) > 32:
            continue
        if isinstance(value, (int, float, bool)) or value is None:
            data[key] = value
        elif isinstance(value, str):
            data[key] = value[:300]
        elif isinstance(value, list) and len(value) <= 40:
            data[key] = [v if isinstance(v, (int, float, str, bool)) else json.dumps(v)[:80] for v in value]
        elif isinstance(value, dict) and len(value) <= 10:
            data[key] = {str(k)[:32]: (v if isinstance(v, (int, float, str, bool)) else str(v)[:80]) for k, v in value.items()}
    return log(kind, src="window", **data)
