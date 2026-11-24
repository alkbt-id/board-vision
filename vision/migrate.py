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
  5  the check of your own move: when your move takes and the exchange there does not lose for you, his taking back
     is not a threat (2026-11-24: Bxh4 gxh4 Qxh4 was marked as «missed»). Own-move positions are checked again too,
     keeping the engine's tactics they carry
  6  the same taking back is optional: drawn it is right, left out it is no miss (2026-11-24: after a pawn capture
     on d3 the arrows of his taking back are wanted, not wrong)
  7  a capture that opens a line onto a piece of the capturing side counts what it loses there (Bh3xg2 opening the
     h-file to Rh1xh6 was marked as a missed threat)
The engine's tactics (forced mates in 2–3, winning checks: threats.add_tactics) are not the rule's: a position keeps
the ones it has when its key is computed again, and an answer is checked with them only if it was asked for them
(its stored result has "tactics": true).
"""

import json

import chess

from . import events, store, threats

RULE_VERSION = "7"


def _own_moves() -> int:
    """The checks of the user's own moves against the current rule: the capture rule's part is computed again, the
    engine's tactics («attacks») are kept as stored. Returns the answers checked again."""
    n = 0
    for row in store.q("SELECT id, fen, move, answer FROM items WHERE source = 'ownmove'"):
        before = chess.Board(row["fen"])
        move = chess.Move.from_uci(row["move"])
        if move not in before.legal_moves:
            continue
        key = threats.own_move(before, move)  # with every check: an answer not asked for them is checked without
        old = json.loads(row["answer"] or "{}")
        key["threats"] += [t for t in old.get("threats") or [] if t.get("tactic")]  # the engine's, kept
        key["attacks"] = old.get("attacks") or []
        store.run("UPDATE items SET answer=?, n_threats=? WHERE id=?",
                  (json.dumps(key, ensure_ascii=False), len(key["threats"]), row["id"]))
        after = before.copy(stack=False)
        after.push(move)
        for a in store.q("SELECT id, arrows, result FROM attempts WHERE item=?", (row["id"],)):
            asked = bool(json.loads(a["result"] or "{}").get("checks"))
            res = threats.check_own(key, json.loads(a["arrows"] or "[]"), after, with_checks=asked)
            store.run("UPDATE attempts SET n_threats=?, found=?, missed=?, extra=?, ok=?, result=? WHERE id=?",
                      (len([t for t in key["threats"] if not t.get("optional") and (asked or t.get("kind") != "check")])
                       + len(key["attacks"]),
                       len(res["found"]), len(res["missed"]),
                       len(res["extra"]), int(res["ok"]), json.dumps(res, ensure_ascii=False), a["id"]))
            n += 1
    return n


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
                            with_tactics=bool(stored.get("tactics")), with_threats=stored.get("threats_asked", True))
        store.run("UPDATE attempts SET n_threats=?, found=?, missed=?, extra=?, ok=?, result=? WHERE id=?",
                  (res["n"], len(res["found"]), len(res["missed"]), len(res["extra"]), int(res["ok"]),
                   json.dumps(res, ensure_ascii=False), a["id"]))
        rescored += 1
        to_ok += bool(res["ok"]) and not a["ok"]
        to_wrong += (not res["ok"]) and bool(a["ok"])
    rescored += _own_moves()
    store.meta_set("rule_version", RULE_VERSION)
    from . import rating
    rating.clear_cache()
    events.log("rule_update", version=RULE_VERSION, items=items, items_changed=changed, answers=rescored,
               now_right=to_ok, now_wrong=to_wrong)


def sources_v2() -> None:
    """Once: the turned-around Lichess puzzles are set aside (source
    «puzzle_old») and read again from the solver's side (vision/sources/puzzles.py); the real misses where the threat
    could not be parried — the player lost with best play — are set aside too («missed_lost»). Set aside, not deleted:
    past answers keep their positions."""
    if store.meta_get("sources_v2") == "1":
        return
    from . import engine
    store.run("UPDATE items SET source = 'puzzle_old' WHERE source = 'puzzle'")
    store.meta_set("puzzle_rows", "0")
    lost = 0
    for r in store.q("SELECT id, fen, move FROM items WHERE source = 'missed'"):
        after = chess.Board(r["fen"])
        after.push(chess.Move.from_uci(r["move"]))
        e = engine.evaluate(after, after.turn)
        if e is not None and e < -300:
            store.run("UPDATE items SET source = 'missed_lost' WHERE id = ?", (r["id"],))
            lost += 1
    store.meta_set("sources_v2", "1")
    events.log("sources_v2", puzzles="set aside, read again from the solver's side", missed_lost=lost)


def sources_v3() -> None:
    """Once: positions from the first 8 moves of
    other people's games (the open database and the real misses) are set aside as «db_early» / «missed_early» — not
    deleted, past answers keep them; new ones are taken from move 9 on (vision/sources/lichess_db.FIRST_PLY)."""
    if store.meta_get("sources_v3") == "1":
        return
    from .sources import lichess_db
    n = len(store.q("SELECT id FROM items WHERE source IN ('db', 'missed') AND ply < ?", (lichess_db.FIRST_PLY,)))
    store.run("UPDATE items SET source = source || '_early' WHERE source IN ('db', 'missed') AND ply < ?",
              (lichess_db.FIRST_PLY,))
    store.meta_set("sources_v3", "1")
    events.log("sources_v3", early_set_aside=n)
