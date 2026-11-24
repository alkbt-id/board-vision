"""Where drill positions come from, and the background job that fills the pool.

  own.py         the user's own games (Lichess API and chess.com's published data), every opponent move
  lichess_db.py  other people's rapid games at the user's level, read as a stream from the Lichess open database
  puzzles.py     Lichess puzzles turned around: the position right after the winning blow, the user on the side hit
  quiet.py       Stockfish's check of positions without threats: only truly quiet ones are shown as quiet

`refresh()` runs them in order and reports progress through `STATUS` and the event log; the window polls
/api/state and can start training as soon as there are enough positions.
"""

import random
import threading
import traceback

import chess

from .. import events, store, threats

STATUS: dict = {"running": False, "step": "", "text": "", "error": ""}
STOPPING = threading.Event()  # set when the app quits: a job cut off then (Stockfish killed) is not an error
_lock = threading.Lock()

# a session can start when the pool has this many positions of each kind
MIN_THREAT, MIN_QUIET = 20, 10


def ready() -> bool:
    c = store.counts()
    return c["threat"] >= MIN_THREAT and c["quiet"] >= MIN_QUIET


def side_name(color: chess.Color) -> str:
    return "w" if color == chess.WHITE else "b"


def add_position(board: chess.Board, move: chess.Move, source: str, ref: str, ply: int, motif: str = "",
                 rating: int | None = None, need_threat: bool = False) -> int | None:
    """Store "position before the opponent's `move` + that move" when it is a drill position (with `need_threat`,
    only when the rule finds a threat). Returns the number of threats; None when skipped (the user would be in
    check, the game is over, or no threat where one is needed)."""
    after = board.copy(stack=False)
    after.push(move)
    answer = threats.analyse(after)
    if answer is None or (need_threat and not answer["threats"]):
        return None
    store.add_item(source, board.fen(), move.uci(), side_name(after.turn), answer, ref=ref, ply=ply, motif=motif,
                   rating=rating)
    return len(answer["threats"])


def sample_game(start: chess.Board, moves: list[chess.Move], user: chess.Color, source: str, ref: str,
                first_ply: int = 2, max_threat: int | None = None, max_quiet: int | None = None,
                rng: random.Random | None = None) -> tuple[int, int]:
    """Positions after the opponent's moves in one game. With limits, a random sample: up to `max_threat`
    positions with a threat and `max_quiet` without. Returns (threat positions, quiet positions) stored."""
    board = start.copy()
    candidates = []
    for ply, move in enumerate(moves):
        if board.turn != user and ply >= first_ply:
            candidates.append((board.copy(stack=False), move, ply))
        board.push(move)
    if rng is not None:
        rng.shuffle(candidates)
    n_threat = n_quiet = 0
    for before, move, ply in candidates:
        after = before.copy(stack=False)
        after.push(move)
        answer = threats.analyse(after)
        if answer is None:
            continue
        if answer["threats"]:
            if max_threat is not None and n_threat >= max_threat:
                continue
            n_threat += 1
        else:
            if max_quiet is not None and n_quiet >= max_quiet:
                continue
            n_quiet += 1
        store.add_item(source, before.fen(), move.uci(), side_name(user), answer, ref=ref, ply=ply)
        if max_threat is not None and max_quiet is not None and n_threat >= max_threat and n_quiet >= max_quiet:
            break
    return n_threat, n_quiet


def say(text: str, step: str = "") -> None:
    STATUS["text"] = text
    if step:
        STATUS["step"] = step
    events.log("import_step", step=STATUS["step"], text=text)


def refresh() -> bool:
    """Download what is new and fill the pool; False when a refresh is already running."""
    from . import lichess_db, own, puzzles, quiet

    with _lock:
        if STATUS["running"]:
            return False
        STATUS.update(running=True, step="", text="", error="")
    events.log("import_start", step="все источники", counts=store.counts())
    try:
        for step, job in (("свои партии", own.run), ("проверка тихих", lambda: quiet.run(limit=200)),
                          ("задачи Lichess", puzzles.run), ("чужие партии", lichess_db.run),
                          ("проверка тихих", lambda: quiet.run(limit=4000))):
            STATUS["step"] = step
            if STOPPING.is_set():
                break
            try:
                job()
            except Exception as e:  # one source failing must not stop the others
                if STOPPING.is_set():
                    events.log("import_step", step=step, text="прервано: приложение закрыто")
                    break
                STATUS["error"] = f"{step}: {e}"
                events.log("import_error", step=step, text=str(e), trace=traceback.format_exc()[-1500:])
        c = store.counts()
        if STOPPING.is_set():
            return True
        events.log("import_done", counts=c,
                   text=f"свои {c['own']}, чужие {c['db']}, задачи {c['puzzle']} · с угрозой {c['threat']}, тихих {c['quiet']}")
    finally:
        STATUS.update(running=False, step="", text="")
    return True


def start_background() -> bool:
    if STATUS["running"]:
        return False
    threading.Thread(target=refresh, name="import", daemon=True).start()
    return True
