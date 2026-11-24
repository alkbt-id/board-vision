"""python -m vision.watch — the event log as a readable live feed (Russian), for watching a training session.

  python -m vision.watch          the last 30 events, then new ones as they come
  python -m vision.watch --all    the whole log, then new ones
  python -m vision.watch --once   print and exit (no following)
"""

import argparse
import json
import time

from . import config
from .session import MOTIF_NAME

SOURCE = {"own": "твоя партия", "db": "чужая партия", "puzzle": "задача Lichess"}
WHY = {"defended": "фигура защищена", "not_attacked": "не под ударом", "wrong_attacker": "бьёт не эта фигура"}


def sec(ms) -> str:
    return f"{(ms or 0) / 1000:.1f} с".replace(".", ",")


def line(e: dict) -> str | None:
    k, t = e.get("kind"), (e.get("t") or "")[11:19]
    at = f"{e.get('index', 0) + 1}/{e.get('size', '?')}" if "index" in e else ""
    match k:
        case "app_start":
            return f"{t} ▶ приложение запущено (порт {e.get('port')})"
        case "app_stop":
            return f"{t} ■ приложение остановлено"
        case "page_load":
            return f"{t} окно открыто ({e.get('device', '?')})"
        case "import_start":
            return f"{t} ⏳ готовлю позиции: {e.get('step', '')}"
        case "import_step":
            return f"{t} ⏳ {e.get('text', '')}"
        case "import_done":
            return f"{t} ✓ позиции готовы: {e.get('text', '')}"
        case "import_error" | "error" | "error_js":
            return f"{t} ⚠ {e.get('text') or e.get('message') or e}"
        case "session_start":
            return f"{t} ━━ сессия {e.get('session')} · {e.get('size')} позиций · с угрозой {e.get('threat_items')}"
        case "item_shown":
            motif = f" ({MOTIF_NAME.get(str(e['motif']), e['motif'])})" if e.get("motif") else ""
            rep = " · повтор" if e.get("repeat") else ""
            src = str(e.get("source"))
            return (f"{t} {at} · {SOURCE.get(src, src)}{rep} · угроз {e.get('threats')}{motif}"
                    f" · ход соперника {e.get('san')}")
        case "move_shown":
            return None
        case "arrow_add":
            return f"{t}     ↗ {e.get('from')}→{e.get('to')} через {sec(e.get('ms'))}"
        case "arrow_del":
            return f"{t}     ✕ стёрта {e.get('from')}→{e.get('to')} ({sec(e.get('ms'))})"
        case "arrows_clear":
            return f"{t}     ✕ стёрты все стрелки"
        case "check_click":
            return None
        case "answer":
            n = e.get("threats", 0)
            drawn = len(e.get("arrows") or [])
            label = "«Угроз нет»" if not drawn else f"«Проверить» (стрелок {drawn})"
            head = f"{label} через {sec(e.get('ms'))} → " + ("✔" if e.get("ok") else "✘")
            parts = [f"найдено {len(e.get('found', []))}/{n}"] if n else ["тихая позиция"]
            if e.get("missed"):
                parts.append("пропущено " + ", ".join(e["missed"]))
            if e.get("extra"):
                parts.append("лишние " + ", ".join(f"{x['from']}→{x['to']} ({WHY.get(x['why'], x['why'])})" for x in e["extra"]))
            return f"{t}     {head} " + " · ".join(parts)
        case "next_click":
            return None
        case "hidden":
            return f"{t}     ⏸ окно скрыто"
        case "visible":
            return f"{t}     ▶ окно снова видно"
        case "blur":
            return f"{t}     ⏸ окно неактивно (переключился в другое место)"
        case "focus":
            return f"{t}     ▶ окно снова активно"
        case "idle":
            return f"{t}     … бездействие {sec(e.get('ms'))}"
        case "active":
            return None
        case "session_exit":
            return f"{t} ━━ вышел на главный экран на позиции {e.get('index', 0) + 1}/{e.get('size')} (отвечено {e.get('answered')})"
        case "leave_click":
            return None
        case "session_end":
            s = e.get("summary") or {}
            if not s.get("positions"):
                return f"{t} ━━ итог: ни одной позиции не отвечено"
            part = "" if s.get("complete", True) else f" (из {s.get('positions')} отвеченных)"
            return (f"{t} ━━ итог{part}: угроз найдено {s.get('found', 0)} из {s.get('threats', 0)}, тихих верно "
                    f"{s.get('quiet_ok', 0)} из {s.get('quiet', 0)}, лишних стрелок {s.get('extra', 0)}, "
                    f"в среднем {sec(s.get('median_ms'))} на позицию")
    return f"{t} {k}"


def show(raw: str) -> None:
    try:
        event = json.loads(raw)
    except ValueError:
        return
    if (text := line(event)) is not None:
        print(text, flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m vision.watch", description="Журнал «Видения доски» вживую.")
    parser.add_argument("--all", action="store_true", help="весь журнал с начала")
    parser.add_argument("--once", action="store_true", help="напечатать и выйти")
    args = parser.parse_args()
    path = config.EVENTS
    while not path.exists():
        time.sleep(0.5)
    with path.open(encoding="utf-8") as f:
        lines = f.readlines()
        for raw in lines if args.all else lines[-30:]:
            show(raw)
        if args.once:
            return
        while True:
            raw = f.readline()
            if raw:
                show(raw)
            else:
                time.sleep(0.3)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
