"""Stockfish's forcing lines for pool positions: forced mates in 2–3 and checks that win material
(vision/threats.add_tactics, vision/engine.forcing) — what the capture rule does not see.

First the positions the quiet check set aside (quiet_ok = 0: the rule found no threat, the engine found one): those
where he has such a line become positions with a threat. Then the rest, so the difficulty of a position
(vision/rating.py) counts its tactics before it is shown. A position once looked at carries "engine": true.
"""

import json

import chess

from .. import engine, store, threats
from . import say

PER_RUN = 4000  # positions per run (two engine searches each, ~0.2 s)


def pending_hidden() -> bool:
    """Are there set-aside positions not looked at yet (the app refreshes on start then)?"""
    return bool(store.q("SELECT 1 FROM items WHERE n_threats = 0 AND quiet_ok = 0"
                        " AND json_extract(answer, '$.engine') IS NULL LIMIT 1"))


def run(limit: int = PER_RUN) -> None:
    from . import STOPPING

    rows = store.q("SELECT id, fen, move FROM items WHERE source IN ('own', 'db', 'puzzle', 'missed')"
                   " AND json_extract(answer, '$.engine') IS NULL"
                   " ORDER BY (n_threats = 0 AND quiet_ok = 0) DESC, random() LIMIT ?", (limit,))
    if not rows:
        return
    found = became = 0
    for i, r in enumerate(rows, 1):
        if STOPPING.is_set():
            return
        after = chess.Board(r["fen"])
        after.push(chess.Move.from_uci(r["move"]))
        key = threats.analyse(after)
        if key is None:
            continue
        threats.add_tactics(key, after, engine.forcing)
        if not key.get("engine"):
            say("нет Stockfish: скрытые угрозы не проверены")
            return
        n = sum(1 for t in [*key["threats"], *key["chances"]] if t.get("tactic"))
        before = store.q("SELECT n_threats FROM items WHERE id=?", (r["id"],))[0]["n_threats"]
        store.run("UPDATE items SET answer=?, n_threats=? WHERE id=?",
                  (json.dumps(key, ensure_ascii=False), len(key["threats"]), r["id"]))
        found += bool(n)
        became += before == 0 and len(key["threats"]) > 0
        if i % 200 == 0:
            say(f"скрытые угрозы: проверено {i} из {len(rows)}, с матом или шахом {found}")
    say(f"скрытые угрозы: {found} позиций с форсированным матом или выигрывающим шахом, {became} стали позициями с угрозой")
