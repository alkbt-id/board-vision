"""When the rule of what counts as a threat changes, bring the stored positions and answers up to date.

RULE_VERSION goes up with every change of vision/threats.py that changes answers. On start, if the data folder
has an older version: every position gets its answer key computed again, and every past answer is checked again
from its stored arrows — so the rating (vision/rating.py, replayed from the answers) follows the current rule.
History:
  1  captures that win material (the whole exchange) and mates in one
  2  + a pawn walking in to queen (2026-11-24: the user drew b2→b1 and got «лишняя стрелка»)
  3  + an arrow from a piece behind an attacker (a battery: Bc4 behind d5 on e6) counts (2026-11-24)
  4  + chances: what the user can win himself, marked in the same step (2026-11-24). Answers given before were not
     asked for them and are checked without them (their stored result has no "chances": true)
The engine's tactics (forced mates in 2–3, winning checks: threats.add_tactics) are not the rule's: a position keeps
the ones it has when its key is computed again, and an answer is checked with them only if it was asked for them
(its stored result has "tactics": true).
"""

import json

import chess

from . import events, store, threats

RULE_VERSION = "4"


def run() -> None:
    if store.meta_get("rule_version", "1") == RULE_VERSION:
        return
    items = changed = 0
    keys: dict[int, tuple[dict, chess.Board]] = {}
    for row in store.q("SELECT id, fen, move, n_threats, answer FROM items WHERE source != 'ownmove'"):
        after = chess.Board(row["fen"])
        after.push(chess.Move.from_uci(row["move"]))
        answer = threats.analyse(after)
        if answer is None:
            continue
        old = json.loads(row["answer"] or "{}")
        if old.get("engine"):  # keep the engine's tactics
            answer["threats"] += [t for t in old.get("threats") or [] if t.get("tactic")]
            answer["chances"] += [t for t in old.get("chances") or [] if t.get("tactic")]
            answer["engine"] = True
        n = len(answer["threats"])
        changed += n != row["n_threats"]
        store.run("UPDATE items SET answer=?, n_threats=? WHERE id=?", (json.dumps(answer, ensure_ascii=False), n, row["id"]))
        keys[row["id"]] = (answer, after)
        items += 1
    rescored = to_ok = to_wrong = 0
    for a in store.q("SELECT id, item, arrows, ok, result FROM attempts"):
        if a["item"] not in keys:
            continue
        answer, after = keys[a["item"]]
        stored = json.loads(a["result"] or "{}")
        res = threats.check(answer, json.loads(a["arrows"] or "[]"), after, with_chances=bool(stored.get("chances")),
                            with_tactics=bool(stored.get("tactics")))
        store.run("UPDATE attempts SET n_threats=?, found=?, missed=?, extra=?, ok=?, result=? WHERE id=?",
                  (res["n"], len(res["found"]), len(res["missed"]), len(res["extra"]), int(res["ok"]),
                   json.dumps(res, ensure_ascii=False), a["id"]))
        rescored += 1
        to_ok += bool(res["ok"]) and not a["ok"]
        to_wrong += (not res["ok"]) and bool(a["ok"])
    store.meta_set("rule_version", RULE_VERSION)
    from . import rating
    rating.clear_cache()
    events.log("rule_update", version=RULE_VERSION, items=items, items_changed=changed, answers=rescored,
               now_right=to_ok, now_wrong=to_wrong)
