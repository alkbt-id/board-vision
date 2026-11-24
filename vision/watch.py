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
WHY = {"defended": "фигура защищена", "not_attacked": "не под ударом", "wrong_attacker": "бьёт не эта фигура",
       "not_winnable": "её не выиграть"}


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
            if "level" in e:
                return f"{t} ━━ сессия {e.get('session')} · ~{e.get('size')} проверок · длина серии {e.get('level')}"
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
            slid = f" (палец ушёл на {e['lifted_on']})" if e.get("lifted_on") else ""
            return f"{t}     ↗ {e.get('from')}→{e.get('to')} через {sec(e.get('ms'))}{slid}"
        case "arrow_del":
            return f"{t}     ✕ стёрта {e.get('from')}→{e.get('to')} ({sec(e.get('ms'))})"
        case "arrows_clear":
            return f"{t}     ✕ стёрты все стрелки"
        case "check_click":
            return None
        case "answer":
            n = e.get("threats", 0) + e.get("chances", 0)
            drawn = len(e.get("arrows") or [])
            label = "«Ничего нет»" if not drawn else f"«Готово» (стрелок {drawn})"
            head = f"{label} через {sec(e.get('ms'))} → " + ("✔" if e.get("ok") else "✘")
            what = f" (угроз {e.get('threats', 0)}, шансов {e['chances']})" if e.get("chances") else ""
            parts = [f"найдено {len(e.get('found', []))}/{n}{what}"] if n else ["тихая позиция"]
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
        case "rule_update":
            return (f"{t} ⚙ правило угроз v{e.get('version')}: позиций пересчитано {e.get('items')} (изменилось "
                    f"{e.get('items_changed')}), ответов перепроверено {e.get('answers')}: стали верными "
                    f"{e.get('now_right')}, стали неверными {e.get('now_wrong')}")
        case "series_start":
            src = str(e.get("source"))
            motif = f" ({MOTIF_NAME.get(str(e['motif']), e['motif'])})" if e.get("motif") else ""
            rep = " · повтор" if e.get("repeat") else ""
            n = e.get("length", 1)
            return (f"{t} ── серия {e.get('no')}: {n} {'ход' if n == 1 else 'хода' if n < 5 else 'ходов'} · "
                    f"{SOURCE.get(src, src)}{rep} · угроз {e.get('threats')}{motif}")
        case "bot_move":
            return f"{t}   бот: {e.get('san')}"
        case "own_check":
            head = "✔" if e.get("ok") else "✘"
            parts = []
            if e.get("missed"):
                parts.append("пропущено " + ", ".join(map(str, e["missed"])))
            if e.get("extra"):
                parts.append("лишние " + ", ".join(f"{x['from']}→{x['to']}" for x in e["extra"]))
            return f"{t}   ты: {e.get('san')} — проверка своего хода через {sec(e.get('ms'))} → {head} {' · '.join(parts)}"
        case "user_move":
            return f"{t}   ты: {e.get('san')} (думал {sec(e.get('ms'))})"
        case "series_end":
            s = e.get("summary") or {}
            res = "без промахов" if s.get("clean") else f"промахов {s.get('misses')}"
            over = " · партия окончена" if s.get("game_over") else " · позиция проиграна" if s.get("lost") else ""
            return f"{t}   серия: {s.get('checks')}/{s.get('length')}, {res}{over} → длина {s.get('next_level')}"
        case "session_exit":
            return f"{t} ━━ вышел на главный экран на позиции {e.get('index', 0) + 1}/{e.get('size')} (отвечено {e.get('answered')})"
        case "leave_click":
            return None
        case "session_end":
            s = e.get("summary") or {}
            if "series" in s:
                return (f"{t} ━━ итог: верно {s.get('ok')} из {s.get('checks')}, серий без промахов {s.get('clean')} из "
                        f"{s.get('series')}, длина серии {s.get('level')}, в среднем {sec(s.get('median_ms'))}")
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
