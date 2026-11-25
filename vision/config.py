"""Where the user's data lives and the settings that differ between people.

Data folder ~/Library/Application Support/BoardVision (BV_HOME moves it, e.g. for a test server; BV_OFFLINE=1 makes
the server start without downloads, task preparation or Claude — the window's tests, tests/browser):
  config.toml    settings; made on the first start from «Шахматный тренер»'s settings (the same nicks)
  vision.db      positions, answers, repeats (vision/store.py)
  events.jsonl   every action of the user and the app, one JSON per line (vision/events.py)
  cache/         nothing yet; downloads are read as streams

config.toml keys (all optional):
  lichess_user, chesscom_user   whose games become positions
  stockfish                     path to Stockfish; empty = PATH or Homebrew
  rating_min, rating_max        the rating band of other people's games (both players), default 1000–1500

The iPad listener reuses «Шахматный тренер»'s certificate and key (ChessCoach/tls, ChessCoach/app/lan_key),
so a device that already opens that app opens this one too (vision/lan.py).
Kept free of heavy imports.
"""

import json
import os
import shutil
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HOME = Path(os.environ.get("BV_HOME") or "~/Library/Application Support/BoardVision").expanduser()
FILE = HOME / "config.toml"
DB = HOME / "vision.db"
EVENTS = HOME / "events.jsonl"
PORT = int(os.environ.get("BV_PORT") or 8747)
LAN_PORT = int(os.environ.get("BV_LAN_PORT") or 8444)

COACH = Path("~/Library/Application Support/ChessCoach").expanduser()
COACH_TLS = COACH / "tls"
COACH_LAN_KEY = COACH / "app" / "lan_key"


def _from_coach() -> dict:
    """The nicks «Шахматный тренер» already knows: its config.toml, else its Lichess login."""
    out = {}
    try:
        coach = tomllib.loads((COACH / "config.toml").read_text())
    except OSError, ValueError:
        coach = {}
    for key in ("lichess_user", "chesscom_user", "stockfish"):
        if value := str(coach.get(key) or "").strip():
            out[key] = value
    if "lichess_user" not in out:
        try:
            if user := json.loads((COACH / "app" / "lichess.json").read_text()).get("user"):
                out["lichess_user"] = str(user)
        except OSError, ValueError:
            pass
    return out


def ensure() -> None:
    """The data folder and config.toml (the first time filled from «Шахматный тренер»)."""
    HOME.mkdir(parents=True, exist_ok=True)
    if FILE.exists():
        return
    known = _from_coach()
    lines = ["# «Видение доски»: settings (vision/config.py explains every key)"]
    for key in ("lichess_user", "chesscom_user", "stockfish"):
        lines.append(f'{key} = "{known.get(key, "")}"')
    lines += ["rating_min = 1000", "rating_max = 1500", ""]
    FILE.write_text("\n".join(lines))


def load() -> dict:
    try:
        return tomllib.loads(FILE.read_text())
    except FileNotFoundError:
        return {}


def get(key: str, default: object = "") -> object:
    value = load().get(key)
    return default if value in (None, "") else value


def lichess_user() -> str:
    return str(get("lichess_user")).strip().lower()


def chesscom_user() -> str:
    return str(get("chesscom_user")).strip().lower()


def stockfish() -> str | None:
    if path := str(get("stockfish")).strip():
        return path
    for path in (shutil.which("stockfish"), "/opt/homebrew/bin/stockfish", "/usr/local/bin/stockfish"):
        if path and os.path.exists(path):
            return path
    return None


def rating_band() -> tuple[int, int]:
    return int(str(get("rating_min", 1000))), int(str(get("rating_max", 1500)))
