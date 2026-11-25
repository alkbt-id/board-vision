"""Preparing tasks for the move in the background (vision/tasks.py): candidate → Stockfish, tablebase, Maia → Claude.

The job keeps STOCK ready tasks not tried yet, split by kind as tasks.KINDS, and tops the stock up as tasks are used
(the user's choice, 2026-11-25: a stock, not the whole pool — Claude's work goes only into tasks that will be seen).
For each candidate:
  1. its steps (vision/tasks.py) from its source: a Lichess puzzle's solution (API), the tablebase's only moves, or
     Stockfish's moves within the kind's margin (several lines, a deeper search than the live one);
  2. the kind's test (below): a candidate that fails it is set aside with the reason (status rejected);
  3. Maia 1100–1900: how often each level plays a right move → the task's rating (tasks.rating_of);
  4. Claude (vision/claude.py): is it a good task of its kind (it may say no), a short title, the idea of the
     solution, and why each of the likeliest wrong moves (Maia 1300's) fails — from the lines given to it only.
Kinds and their tests:
  threat    a pool position with his threat (the capture rule); not lost with the best move
  tactic    a Lichess puzzle (solution from the API), or a game position with a chance where the engine's line wins
            ≥2 pawns or mates and the first move is the only good one
  quiet     a quiet pool position; Maia 1300's likeliest move does not spoil it
  trap      in a quiet pool position Maia 1300's likeliest move (p ≥ TEMPT) loses ≥ TRAP_LOSS points of winning
            chances to a tactic while the best move keeps the balance; or a Lichess puzzle turned round: the move
            before its mistake is his, the mistake would be yours
  strategy  a neilgd position (github.com/neilgd/chess-position-analysis-results, checked by strong players); its
            move is Stockfish's best or within STRAT_DOUBT points (else the answer is doubtful)
  endgame   neilgd's endgame theme (as strategy), a Lichess puzzle with an endgame theme (as tactic), or a pool
            position with ≤7 pieces where 1–2 moves keep the tablebase result (then the only moves after it too)
Without Stockfish, lc0 or Claude the job waits and tries again later; the drill goes on with the other positions.
"""

import csv
import io
import json
import random
import threading
import time

import chess
import chess.engine
import chess.pgn

from .. import claude, config, events, explain, maia, store, tasks, threats
from . import lichess_api, net, side_name

STOCK = 150  # config.toml tasks_stock changes it (a test server: a few)
CHECK_EVERY = 300  # seconds between looks at the stock
PAUSE_ON_ERROR = 1800  # after Claude or the API failed
LIMIT = chess.engine.Limit(nodes=1_000_000)
SCREEN = chess.engine.Limit(nodes=150_000)
MARGIN = {"threat": 10.0, "quiet": 10.0, "trap": 10.0, "strategy": 4.0, "endgame": 4.0}
TEMPT = 0.2  # Maia 1300 plays the trap move at least this often
TRAP_LOSS = 20.0  # …and it loses at least this many points of winning chances
STRAT_DOUBT = 6.0
LINE_STEPS = 3  # your moves in a tactic line from a game, at most
TB_STEPS = 4  # …in a tablebase endgame
TB_PIECES = 7
ENDGAME_THEMES = {"endgame", "rookEndgame", "pawnEndgame", "bishopEndgame", "knightEndgame", "queenEndgame",
                  "queenRookEndgame"}
NEILGD = "https://raw.githubusercontent.com/neilgd/chess-position-analysis-results/main/data/chess-positions.csv"
NEILGD_THEMES = {
    "initiative": "инициатива", "development": "развитие", "endgame": "техника эндшпиля", "space": "пространство",
    "trading": "размены", "prophylaxis": "профилактика", "coordination": "координация фигур",
    "exploitingweakness": "использование слабости", "kingsafety": "безопасность короля",
    "restriction": "ограничение фигур соперника", "fixingstructure": "фиксация пешечной структуры",
    "centrecontrol": "контроль центра", "other": "другое",
}
VALUE = {chess.PAWN: 1, chess.KNIGHT: 3, chess.BISHOP: 3, chess.ROOK: 5, chess.QUEEN: 9, chess.KING: 0}
# a tablebase category (seen from the side to move) -> the result for that side
RESULT = {"win": "win", "maybe-win": "win", "cursed-win": "draw", "draw": "draw", "blessed-loss": "draw",
          "maybe-loss": "loss", "loss": "loss"}
FLIP = {"win": "loss", "loss": "win", "draw": "draw"}

STATUS: dict = {"running": False, "text": "", "error": "", "paused_until": 0}
_engine: chess.engine.SimpleEngine | None = None


class Reject(Exception):
    """The candidate is not a good task (the reason is kept)."""


# ---------- Stockfish of its own (one thread: the live engine and the bot keep theirs) ----------
def _sf() -> chess.engine.SimpleEngine:
    global _engine
    if _engine is None:
        path = config.stockfish()
        if not path:
            raise RuntimeError("нет Stockfish")
        engine = chess.engine.SimpleEngine.popen_uci(path)
        engine.configure({"Threads": 1, "Hash": 128})
        _engine = engine
    return _engine


def close() -> None:
    global _engine
    if _engine is not None:
        try:
            _engine.quit()
        except (chess.engine.EngineError, OSError):
            pass
        _engine = None
    maia.close()


def lines(board: chess.Board, n: int, limit=LIMIT) -> list[dict]:
    """Stockfish's `n` best moves: [{"move", "cp" (for the side to move, a mate ±10000), "pv"}], best first."""
    infos = _sf().analyse(board, limit, multipv=n)
    out = []
    for info in infos:
        pv = info.get("pv") or []
        if pv and "score" in info:
            out.append({"move": pv[0], "cp": info["score"].pov(board.turn).score(mate_score=10_000) or 0, "pv": pv})
    return out


def scored(board: chess.Board, move: chess.Move, limit=LIMIT) -> dict:
    """The same for one given move."""
    info = _sf().analyse(board, limit, root_moves=[move])
    return {"move": move, "cp": info["score"].pov(board.turn).score(mate_score=10_000) or 0,
            "pv": info.get("pv") or [move]}


def _material(board: chess.Board, color: chess.Color) -> int:
    return sum(VALUE[p.piece_type] * (1 if p.color == color else -1) for p in board.piece_map().values())


def wins_material(board: chess.Board, pv: list[chess.Move], plies: int = 6) -> int:
    """What the side to move in `board` has won along `pv` after the other side's replies (pawns)."""
    me, start, b, best = board.turn, _material(board, board.turn), board.copy(stack=False), 0
    for i, m in enumerate(pv[:plies]):
        if m not in b.legal_moves:
            break
        b.push(m)
        if b.is_checkmate():
            return 100
        if i % 2 == 1:
            best = max(best, _material(b, me) - start)
    return best


# ---------- the position of a candidate ----------
def _board(item: dict) -> chess.Board:
    """The position the user moves in, with the opponent's move on the stack (Maia looks at it)."""
    b = chess.Board(item["fen"])
    b.push(chess.Move.from_uci(item["move"]))
    return b


def _pieces(board: chess.Board) -> int:
    return len(board.piece_map())


def _single(board: chess.Board, kind: str, n: int = 8) -> tuple[dict, list[dict]]:
    """A one-step task by margin: (the step, the engine's lines)."""
    mp = lines(board, n)
    if not mp:
        raise Reject("нет ходов")
    best = mp[0]
    w = tasks.win_pct(best["cp"])
    accept = [x["move"].uci() for x in mp if w - tasks.win_pct(x["cp"]) <= MARGIN[kind]]
    return {"best": best["move"].uci(), "accept": accept, "reply": None, "margin": MARGIN[kind]}, mp


def make_threat(item: dict) -> dict:
    answer = item["answer"] if isinstance(item["answer"], dict) else json.loads(item["answer"])
    if not any(t.get("kind") != "check" and not t.get("optional") for t in answer.get("threats") or []):
        raise Reject("только шах: это не угроза для задачи")
    board = _board(item)
    step, mp = _single(board, "threat")
    if mp[0]["cp"] < -300:
        raise Reject("угрозу не отразить: позиция проиграна")
    return {"kind": "threat", "steps": [step], "pv": [m.uci() for m in mp[0]["pv"][:4]], "lines": mp}


def make_pool(item: dict, want: str) -> dict:
    """A quiet pool position: a trap when Maia's likeliest move loses to a tactic (whatever was wanted: traps are
    rare), else a quiet task when one is wanted."""
    board = _board(item)
    pol = maia.policy(board, 1300)
    if not pol:
        raise RuntimeError("нет Maia (lc0)")
    top = max(pol.items(), key=lambda kv: kv[1])
    m1 = chess.Move.from_uci(top[0])
    if m1 not in board.legal_moves:
        raise Reject("ход Maia не по правилам")
    quick = lines(board, 1, SCREEN)
    if not quick:
        raise Reject("нет ходов")
    tried = scored(board, m1, SCREEN)
    drop = tasks.win_pct(quick[0]["cp"]) - tasks.win_pct(tried["cp"])
    if top[1] >= TEMPT and drop >= TRAP_LOSS - 5 and abs(quick[0]["cp"]) <= 300:
        step, mp = _single(board, "trap")
        t = scored(board, m1)
        loss = tasks.win_pct(mp[0]["cp"]) - tasks.win_pct(t["cp"])
        after = board.copy()
        after.push(m1)
        if loss >= TRAP_LOSS and m1.uci() not in step["accept"] and (
                wins_material(after, t["pv"][1:]) >= 2 or abs(t["cp"]) >= 9000):
            step["refuse"] = [m1.uci()]
            return {"kind": "trap", "steps": [step], "pv": [m.uci() for m in mp[0]["pv"][:4]], "lines": mp}
    if want == "trap":
        raise Reject("ловушки нет")
    if drop > MARGIN["quiet"] or abs(quick[0]["cp"]) > 250:
        raise Reject("не тихая: вероятный ход портит позицию или перевес большой")
    step, mp = _single(board, "quiet")
    return {"kind": "quiet", "steps": [step], "pv": [m.uci() for m in mp[0]["pv"][:4]], "lines": mp}


def make_game_tactic(item: dict) -> dict:
    """A chance of yours in a game position: the engine's winning line, the only good move at each step."""
    board = _board(item)
    if _pieces(board) <= TB_PIECES:
        raise Reject("мало фигур: это эндшпиль для таблиц")
    mp = lines(board, 2)
    if not mp:
        raise Reject("нет ходов")
    best = mp[0]
    if wins_material(board, best["pv"]) < 2:
        raise Reject("линия не выигрывает материал")
    if len(mp) > 1 and tasks.win_pct(best["cp"]) - tasks.win_pct(mp[1]["cp"]) < 15:
        raise Reject("решение не единственное")
    steps, b = [], board.copy()
    for i in range(LINE_STEPS):
        here = lines(b, 2)
        if not here:
            break
        if i and (len(here) > 1 and tasks.win_pct(here[0]["cp"]) - tasks.win_pct(here[1]["cp"]) < 15):
            break  # from here on there is more than one way: the line is over
        mv = here[0]["move"]
        w = tasks.win_pct(here[0]["cp"])
        accept = [x["move"].uci() for x in here if w - tasks.win_pct(x["cp"]) <= 3]
        b.push(mv)
        reply = here[0]["pv"][1] if len(here[0]["pv"]) > 1 and not b.is_game_over() else None
        steps.append({"best": mv.uci(), "accept": accept, "reply": reply.uci() if reply else None, "margin": None})
        if reply is None or wins_material(board, b.move_stack[len(board.move_stack):]) >= 2:
            break  # the material is won (or it was a mate): the line is over
        b.push(reply)
    return {"kind": "tactic", "steps": steps, "pv": [m.uci() for m in best["pv"][:6]], "lines": mp}


def make_puzzle(item: dict, kind: str = "tactic") -> dict:
    """A Lichess puzzle from the solver's side: its solution from the API. As an endgame task only with an endgame
    theme (else the tactic stock would fill the endgame share and leave no room for technique)."""
    data = lichess_api.puzzle(item["ref"])
    pz = data["puzzle"]
    board = _board(item)
    if pz.get("fen") and chess.Board(pz["fen"]).board_fen() != board.board_fen():
        raise Reject("позиция задачи не совпала")
    sol = pz["solution"]
    steps, b = [], board.copy()
    for i in range(0, len(sol), 2):
        mv = chess.Move.from_uci(sol[i])
        if mv not in b.legal_moves:
            raise Reject("решение не по правилам")
        b.push(mv)
        reply = sol[i + 1] if i + 1 < len(sol) else None
        steps.append({"best": sol[i], "accept": [sol[i]], "reply": reply, "margin": None})
        if reply:
            b.push(chess.Move.from_uci(reply))
    themes = list(pz.get("themes") or [])
    if kind == "endgame" and not ENDGAME_THEMES & set(themes):
        raise Reject("не эндшпиль")
    mp = lines(board, 3, SCREEN)
    return {"kind": kind, "steps": steps, "themes": themes, "lichess_rating": pz.get("rating"), "lines": mp,
            "pv": sol[:6]}


def make_puzzle_trap(item: dict) -> tuple[int, dict]:
    """A Lichess puzzle turned round: the position before the move that came before the mistake; that move is his,
    the mistake would be yours. A new item (source puzzle_trap); returns (its id, the task)."""
    data = lichess_api.puzzle(item["ref"])
    game = chess.pgn.read_game(io.StringIO(data["game"]["pgn"]))
    if game is None:
        raise Reject("нет партии")
    moves = list(game.mainline_moves())
    if len(moves) < 12:
        raise Reject("ловушка в дебюте слишком ранняя")
    b = chess.Board()
    for m in moves[:-2]:
        b.push(m)
    before, his, mistake = b.copy(), moves[-2], moves[-1]
    b.push(his)
    if b.board_fen() != chess.Board(item["fen"]).board_fen():
        raise Reject("партия задачи не совпала")
    pol = maia.policy(b, 1300)
    if pol.get(mistake.uci(), 0) < 0.08:
        raise Reject("ошибка задачи не соблазнительна для 1300")
    step, mp = _single(b, "trap")
    if mp[0]["cp"] < -150:
        raise Reject("позиция и так плохая")
    if mistake.uci() in step["accept"]:
        raise Reject("ошибка задачи не проигрывает по движку")
    step["refuse"] = [mistake.uci()]
    answer = threats.analyse(b)
    if answer is None:
        raise Reject("шах")
    store.add_item("puzzle_trap", before.fen(), his.uci(), side_name(b.turn), answer, ref=item["ref"],
                   ply=len(moves) - 2, motif="trap", rating=item.get("rating"))
    row = store.q("SELECT id FROM items WHERE key=?", (f"puzzle_trap|{before.fen()}|{his.uci()}",))
    themes = list(data["puzzle"].get("themes") or [])
    return row[0]["id"], {"kind": "trap", "steps": [step], "themes": themes, "lines": mp,
                          "pv": [m.uci() for m in mp[0]["pv"][:4]]}


def _tb_good(tb: dict) -> tuple[str, list[str]]:
    """(the result for the side to move, the moves that keep it, best first)."""
    root = RESULT.get(tb.get("category", ""), "")
    good = [m["uci"] for m in tb.get("moves") or [] if FLIP.get(RESULT.get(m.get("category", ""), ""), "") == root]
    return root, good


def make_tablebase(item: dict) -> dict:
    """≤7 pieces: the moves that keep the tablebase result; while that is one move only, his best defence and on."""
    board = _board(item)
    tb = lichess_api.tablebase(board.fen())
    root, good = _tb_good(tb)
    if root not in ("win", "draw"):
        raise Reject("позиция проиграна по таблицам")
    if not 1 <= len(good) <= 2 or len(tb.get("moves") or []) < 3:
        raise Reject("нет единственного хода")
    steps, b = [], board.copy()
    while True:
        st = {"best": good[0], "accept": good, "reply": None, "margin": None, "tb": root}
        steps.append(st)
        b.push(chess.Move.from_uci(good[0]))
        if b.is_game_over() or len(steps) >= TB_STEPS:
            break
        his = lichess_api.tablebase(b.fen()).get("moves") or []
        if not his:
            break
        st["reply"] = his[0]["uci"]  # best first: his longest resistance
        b.push(chess.Move.from_uci(his[0]["uci"]))
        r2, good = _tb_good(lichess_api.tablebase(b.fen()))
        if r2 != root or len(good) != 1:
            break
    return {"kind": "endgame", "steps": steps, "tb": root, "lines": lines(board, 3, SCREEN),
            "pv": [x for st in steps for x in (st["best"], st["reply"]) if x]}


def _neilgd_rows() -> list[dict]:
    path = config.HOME / "cache" / "neilgd.csv"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        with net.client() as c:
            r = c.get(NEILGD)
            r.raise_for_status()
        path.write_text(r.text)
    return list(csv.DictReader(io.StringIO(path.read_text())))


def make_neilgd(row: dict) -> tuple[int, dict]:
    """A position of neilgd's set: the opponent's move before it from the Lichess game; a new item (source neilgd)."""
    pgn = lichess_api.game_pgn(row["lichess_game_id"])
    game = chess.pgn.read_game(io.StringIO(pgn))
    if game is None:
        raise Reject("нет партии")
    moves = list(game.mainline_moves())
    n = int(row["move_number"])
    if not 1 <= n <= len(moves):
        raise Reject("ход не в партии")
    b = chess.Board()
    for m in moves[: n - 1]:
        b.push(m)
    before, his = b.copy(), moves[n - 1]
    b.push(his)
    want = chess.Board(row["fen"])
    if b.board_fen() != want.board_fen() or b.turn != want.turn:
        raise Reject("позиция не совпала с партией")
    themes = [t for t in NEILGD_THEMES if row.get(t) == "1"]
    kind = "endgame" if "endgame" in themes else "strategy"
    mv = chess.Move.from_uci(row["best_move"])
    if mv not in b.legal_moves:
        raise Reject("ход сборника не по правилам")
    mp = lines(b, 5)
    theirs = next((x for x in mp if x["move"] == mv), None) or scored(b, mv)
    w = tasks.win_pct(mp[0]["cp"])
    if w - tasks.win_pct(theirs["cp"]) > STRAT_DOUBT:
        raise Reject("Stockfish не согласен с ответом сборника")
    accept = list(dict.fromkeys([mv.uci(), *(x["move"].uci() for x in mp if w - tasks.win_pct(x["cp"]) <= MARGIN[kind])]))
    answer = threats.analyse(b)
    if answer is None:
        raise Reject("шах")
    store.add_item("neilgd", before.fen(), his.uci(), side_name(b.turn), answer, ref=row["internal_id"], ply=n - 1,
                   motif=",".join(themes))
    item = store.q("SELECT id FROM items WHERE key=?", (f"neilgd|{before.fen()}|{his.uci()}",))[0]["id"]
    step = {"best": mv.uci(), "accept": accept, "reply": None, "margin": MARGIN[kind]}
    return item, {"kind": kind, "steps": [step], "themes": [NEILGD_THEMES[t] for t in themes],
                  "neilgd_difficulty": int(row.get("difficulty") or 0), "lines": mp,
                  "pv": [m.uci() for m in theirs["pv"][:4]]}


# ---------- Maia: the rating; the likeliest wrong moves ----------
def _step_boards(board: chess.Board, steps: list[dict]) -> list[chess.Board]:
    out, b = [], board.copy()
    for st in steps:
        out.append(b.copy())
        b.push(chess.Move.from_uci(st["best"]))
        if st.get("reply"):
            b.push(chess.Move.from_uci(st["reply"]))
    return out


def rate(board: chess.Board, task: dict) -> tuple[int, dict]:
    """(the task's rating, {level: probability that a player of that level plays every step right}). At a step with
    a margin the moves players of some level really play (p ≥ 4%) are looked at too: one within the margin counts
    (and joins `accept`), so a quiet position where many moves are fine is not rated as hard."""
    boards = _step_boards(board, task["steps"])
    if not maia.policy(boards[0], 1300):
        raise RuntimeError("нет Maia (lc0)")
    levels = maia.available()
    pols = [{lv: maia.policy(b, lv) for lv in levels} for b in boards]
    for b, st, pol in zip(boards, task["steps"], pols):
        if st.get("margin") is None:
            continue
        best_cp = scored(b, chess.Move.from_uci(st["best"]), SCREEN)["cp"]
        seen = {u for by in pol.values() for u, p in by.items() if p >= 0.04}
        for u in sorted(seen - set(st["accept"]) - set(st.get("refuse") or [])):
            m = chess.Move.from_uci(u)
            if m in b.legal_moves and tasks.win_pct(best_cp) - tasks.win_pct(scored(b, m, SCREEN)["cp"]) <= st["margin"]:
                st["accept"].append(u)
    p_by: dict[int, float] = {}
    for level in levels:
        p = 1.0
        for st, pol in zip(task["steps"], pols):
            ok = set(st["accept"]) - set(st.get("refuse") or [])
            p *= sum(v for u, v in pol[level].items() if u in ok)
        p_by[level] = round(p, 4)
    return tasks.rating_of(p_by), p_by


def tempting(board: chess.Board, task: dict) -> list[dict]:
    """Maia 1300's likeliest moves that do not count: up to 3 at the first step, 1 at each later one — each with
    Stockfish's answer to it (for Claude, and to recognise the move later)."""
    out = []
    for i, (b, st) in enumerate(zip(_step_boards(board, task["steps"]), task["steps"])):
        pol = maia.policy(b, 1300)
        best_cp = scored(b, chess.Move.from_uci(st["best"]), SCREEN)["cp"]
        cands = sorted(((u, p) for u, p in pol.items() if p >= 0.05 and u not in st["accept"]), key=lambda x: -x[1])
        for u, p in cands[: 3 if i == 0 else 1]:
            m = chess.Move.from_uci(u)
            if m not in b.legal_moves:
                continue
            s = scored(b, m, SCREEN)
            loss = tasks.win_pct(best_cp) - tasks.win_pct(s["cp"])
            if st.get("margin") is not None and loss <= st["margin"] and u not in (st.get("refuse") or []):
                continue  # it counts live: not a wrong move
            after = b.copy(stack=False)
            after.push(m)
            out.append({"step": i, "uci": u, "san": b.san(m), "p": round(p, 3), "cp": s["cp"], "loss": round(loss, 1),
                        "line": explain.san_line(after, s["pv"][1:7])})
    return out


# ---------- Claude ----------
SYSTEM = """Ты — шахматный тренер. Ученик — взрослый, рейтинг около 1300 в рапиде на Lichess. Ты готовишь короткие объяснения к тренировочной задаче: их покажут, только если ученик ошибётся.
Правила:
- Пиши по-русски, на «ты», простыми словами, без воды и без похвалы.
- Ходы пиши в алгебраической нотации, как в данных (Nf3, exd5, O-O); фигуры в тексте называй словами: король, ферзь, ладья, слон, конь, пешка.
- Опирайся только на варианты и оценки Stockfish (и таблиц эндшпиля), которые даны. Не придумывай своих вариантов и не утверждай того, чего в них нет. Оценки — в пешках с точки зрения ученика.
- Проверяй каждое утверждение по позиции: какая фигура на каком поле, что она бьёт, что защищает. Термины — точные: «связка/связана» только про связку; «держит/контролирует поле», а не «занимает», если фигура на нём не стоит; «соединённые ладьи», «открытая линия», «слабое поле», «форпост».
- Коротко: idea — не больше 3 предложений и 300 знаков; каждое wrong — одно предложение до 200 знаков. Главное — почему ход работает (или почему ошибка проигрывает), а не пересказ всего варианта.
- Если задача неудачная — верный ответ спорный, решение не объяснить словами человеку 1300, позиция не того вида, что заявлен, — поставь keep=false и коротко напиши почему."""

KIND_TASK = {
    "threat": "Соперник своим последним ходом создал угрозу. Ученик должен её заметить и отразить. Засчитывается любой ход, после которого шансы почти не хуже, чем после лучшего.",
    "tactic": "У ученика есть выигрышная комбинация. Засчитывается только весь вариант (ответы соперника — лучшие), любой мат тоже.",
    "quiet": "Тихая позиция: тактики нет ни у кого. Засчитывается любой ход, который не портит позицию. Объясни, что в позиции важно и какой план разумен, и почему вероятные ошибки её портят.",
    "trap": "В позиции ловушка: естественный ход, который чаще всего играют люди 1300, проигрывает из-за тактики. Ученик должен его не сыграть. Засчитывается любой ход, который не портит позицию.",
    "strategy": "Стратегическое решение: тактики нет, но один план явно лучше. Ответ проверили сильные шахматисты.",
    "endgame": "Эндшпиль: надо найти ход, который сохраняет результат.",
}


def _pieces_text(board: chess.Board) -> str:
    out = []
    for color, name in ((chess.WHITE, "Белые"), (chess.BLACK, "Чёрные")):
        ps = []
        for pt in (chess.KING, chess.QUEEN, chess.ROOK, chess.BISHOP, chess.KNIGHT):
            for sq in board.pieces(pt, color):
                ps.append(chess.piece_symbol(pt).upper() + chess.square_name(sq))
        pawns = " ".join(chess.square_name(sq) for sq in board.pieces(chess.PAWN, color))
        out.append(f"{name}: {', '.join(ps)}" + (f"; пешки {pawns}" if pawns else ""))
    return "\n".join(out)


def _prompt(item: dict, board: chess.Board, task: dict) -> str:
    before = chess.Board(item["fen"])
    his = chess.Move.from_uci(item["move"])
    me = "белые" if board.turn == chess.WHITE else "чёрные"
    sol = tasks.line_from(task, 0, board)
    lines_txt = []
    for x in task.get("lines") or []:
        mv, pv = chess.Move.from_uci(x["move"]), [chess.Move.from_uci(u) for u in x["pv"][:7]]
        lines_txt.append(f"  {board.san(mv)}: {explain.pawns(x['cp'])}; вариант {explain.san_line(board, pv)}")
    parts = [
        f"Вид задачи: {tasks.NAME[task['kind']]}. {KIND_TASK[task['kind']]}",
        f"Позиция (FEN): {board.fen()}",
        _pieces_text(board),
        f"Ученик играет за {me}. Последний ход соперника: {explain.san_line(before, [his])}.",
        "Решение: " + " ".join(s["san"] for s in sol),
    ]
    first = task["steps"][0]
    others = [u for u in first["accept"] if u != first["best"]]
    if others:
        parts.append("Тоже засчитываются: " + ", ".join(board.san(chess.Move.from_uci(u)) for u in others))
    if first.get("refuse"):
        parts.append("Ход-ловушка: " + board.san(chess.Move.from_uci(first["refuse"][0])))
    if task.get("tb"):
        parts.append(f"Таблицы эндшпиля: при точной игре у ученика {'выигрыш' if task['tb'] == 'win' else 'ничья'}; "
                     "засчитываются только ходы, которые его сохраняют.")
    if task.get("themes"):
        parts.append("Темы: " + ", ".join(task["themes"]))
    if lines_txt:
        parts.append("Stockfish, лучшие ходы (оценка для ученика):\n" + "\n".join(lines_txt))
    wrong = task.get("tempting") or []
    if wrong:
        ws = []
        for i, t in enumerate(wrong, 1):
            where = "первый ход" if t["step"] == 0 else f"{t['step'] + 1}-й ход решения"
            ws.append(f"  w{i} ({where}): {t['san']} — так играют {round(100 * t['p'])}% людей 1300; после него"
                      f" {explain.pawns(t['cp'])}, ответ соперника {t['line']}")
        parts.append("Вероятные ошибки ученика:\n" + "\n".join(ws))
    parts.append("Ответ: title — 2–5 слов, название идеи (например «Вилка конём на f7», «Пешечный прорыв b5»);"
                 " idea — 2–3 предложения: что делает верное решение и почему оно работает; wrong — для каждой"
                 " ошибки (id из списка) одно предложение, почему этот ход хуже.")
    return "\n\n".join(parts)


SCHEMA = {
    "type": "object",
    "properties": {
        "keep": {"type": "boolean"},
        "reason": {"type": "string"},
        "title": {"type": "string"},
        "idea": {"type": "string"},
        "wrong": {"type": "array", "items": {"type": "object", "properties": {"id": {"type": "string"},
                                                                               "why": {"type": "string"}},
                                              "required": ["id", "why"]}},
    },
    "required": ["keep", "reason", "title", "idea", "wrong"],
}


def explain_task(item: dict, board: chess.Board, task: dict) -> dict:
    out = claude.ask(SYSTEM, _prompt(item, board, task), SCHEMA)
    wrong = {}
    for w in out.get("wrong") or []:
        try:
            t = (task.get("tempting") or [])[int(str(w["id"]).lstrip("w")) - 1]
        except (ValueError, IndexError):
            continue
        wrong[f"{t['step']}:{t['uci']}"] = str(w["why"]).strip()
    return {"keep": bool(out.get("keep")), "reason": str(out.get("reason") or ""), "title": str(out.get("title") or ""),
            "idea": str(out.get("idea") or ""), "wrong": wrong}


# ---------- the job ----------
def _rows(sql: str, n: int = 30, kind: str = "") -> list[dict]:
    """Pool positions for a task: never tried, no task yet, not set aside for this kind."""
    return [dict(r) for r in store.q(
        f"SELECT * FROM items WHERE {sql} AND id NOT IN (SELECT item FROM tasks) AND id NOT IN (SELECT item FROM attempts)"
        " AND CAST(id AS TEXT) NOT IN (SELECT ref FROM skips WHERE kind = ?) ORDER BY random() LIMIT ?", (kind, n))]


def skip(kind: str, ref: str, reason: str) -> None:
    store.run("INSERT OR IGNORE INTO skips (kind, ref, reason) VALUES (?,?,?)", (kind, ref, reason))


def make_puzzle_endgame(item: dict) -> dict:
    return make_puzzle(item, "endgame")


def make_quiet(item: dict) -> dict:
    return make_pool(item, "quiet")


def make_trap(item: dict) -> dict:
    return make_pool(item, "trap")


POOL = "source IN ('own', 'db')"
QUIET = f"{POOL} AND n_threats = 0 AND quiet_ok = 1 AND json_array_length(answer, '$.chances') = 0"


def candidates(kind: str):
    """(item row or None, maker) pairs to try for `kind`, a few at a time."""
    if kind == "threat":
        return [(r, make_threat) for r in _rows("source IN ('own', 'db', 'missed') AND n_threats > 0", 10, kind)]
    if kind == "tactic":
        out = [(r, make_puzzle) for r in _rows("source = 'puzzle'", 6, kind)]
        out += [(r, make_game_tactic) for r in _rows(f"{POOL} AND json_array_length(answer, '$.chances') > 0", 6, kind)]
        random.shuffle(out)
        return out
    if kind == "quiet":
        return [(r, make_quiet) for r in _rows(QUIET, 10, kind)]
    if kind == "trap":
        out = [(r, make_trap) for r in _rows(QUIET, 40, kind)]
        out += [(r, make_puzzle_trap) for r in _rows("source = 'puzzle'", 4, kind)]
        random.shuffle(out)
        return out
    if kind == "strategy" or kind == "endgame":
        used = {r["ref"] for r in store.q("SELECT ref FROM items WHERE source = 'neilgd'")}
        used |= {r["ref"] for r in store.q("SELECT ref FROM skips WHERE kind = 'neilgd'")}
        rows = [r for r in _neilgd_rows() if r["internal_id"] not in used
                and (r.get("endgame") == "1") == (kind == "endgame") and int(r.get("difficulty") or 9) <= 3]
        random.shuffle(rows)
        out: list = [(r, make_neilgd) for r in rows[:6]]
        if kind == "endgame":
            few = [r for r in _rows(POOL, 400, kind) if _pieces(_board(r)) <= TB_PIECES][:6]
            out += [(r, make_tablebase) for r in few]
            out += [(r, make_puzzle_endgame) for r in _rows("source = 'puzzle'", 200, kind)
                    if len(chess.Board(r["fen"]).piece_map()) <= 14][:3]
            random.shuffle(out)
        return out
    return []


def prepare_one(row: dict, maker, want: str) -> str:
    """Make and rate one candidate, and explain it when it is not too hard for now (else it waits as «later»);
    returns the kind of the task made ready, or ""."""
    try:
        if maker in (make_neilgd, make_puzzle_trap):
            item_id, task = maker(row)
        else:
            task, item_id = maker(row), row["id"]
    except Reject as e:
        if maker is make_neilgd:
            skip("neilgd", row["internal_id"], str(e))
        else:
            skip(want, str(row["id"]), str(e))  # it may still fit another kind
        events.log("task_rejected", task_kind=want, item=row.get("id"), ref=row.get("internal_id"), reason=str(e))
        return ""
    if maker is make_puzzle_trap:
        tasks.save(row["id"], "tactic", "rejected", {}, note="взята ловушкой")  # not shown from both sides
    item = store.item(item_id)
    assert item is not None
    board = _board(item)
    task["rating"], task["maia"] = rate(board, task)
    task["tempting"] = tempting(board, task)
    task["origin"] = {make_neilgd: "neilgd", make_puzzle: "puzzle", make_puzzle_endgame: "puzzle",
                      make_puzzle_trap: "puzzle_trap",
                      make_tablebase: "tablebase"}.get(maker, item["source"])
    task["lines"] = [{"move": x["move"].uci(), "cp": x["cp"], "pv": [m.uci() for m in x["pv"][:8]]}
                     for x in task.get("lines") or []]
    if task["rating"] > tasks.current() + tasks.WINDOW:
        tasks.save(item_id, task["kind"], "later", task, task["rating"], task["origin"], note="пока трудная")
        events.log("task_later", task_kind=task["kind"], item=item_id, rating=task["rating"])
        return ""
    return finish(item_id, task)


def finish(item_id: int, task: dict) -> str:
    """Claude's part: keep or not, the title, the idea, why the likeliest wrong moves fail; the task is then ready."""
    item = store.item(item_id)
    assert item is not None
    board = _board(item)
    tasks.save(item_id, task["kind"], "engine", task, task["rating"], task["origin"])
    words = explain_task(item, board, task)  # claude.ClaudeError goes up: the job pauses
    task.update(title=words["title"], idea=words["idea"], wrong=words["wrong"])
    if not words["keep"]:
        tasks.save(item_id, task["kind"], "rejected", task, task["rating"], task["origin"],
                   note=f"Claude: {words['reason']}")
        events.log("task_rejected", task_kind=task["kind"], item=item_id, reason=f"Claude: {words['reason']}")
        return ""
    tasks.save(item_id, task["kind"], "ready", task, task["rating"], task["origin"])
    events.log("task_ready", task_kind=task["kind"], item=item_id, rating=task["rating"], title=task["title"],
               origin=task["origin"])
    return task["kind"]


def promote() -> bool:
    """A task set aside as too hard (or waiting for Claude) that fits the rating now: explained and made ready."""
    rows = store.q("SELECT item FROM tasks WHERE status IN ('later', 'engine') AND rating <= ?"
                   " ORDER BY status = 'engine' DESC, rating DESC LIMIT 1", (tasks.current() + tasks.WINDOW,))
    if not rows:
        return False
    t = tasks.load(rows[0]["item"])
    assert t is not None
    data = {k: v for k, v in t.items() if k not in ("item", "status", "note", "added", "prepared")}
    return bool(finish(rows[0]["item"], data))


def stock_size() -> int:
    return int(str(config.get("tasks_stock", STOCK)))


def _start_rating() -> None:
    if store.meta_get("decisions_start", ""):
        return
    user = config.lichess_user()
    r = lichess_api.rapid_rating(user) if user else None
    if r:
        store.meta_set("decisions_start", str(r))


def run() -> None:
    """Top the stock up once; returns when it is full, or when something is missing (then STATUS says what)."""
    from . import STOPPING

    _start_rating()
    tries = 0
    while not STOPPING.is_set():
        have = tasks.ready_counts()
        stock = stock_size()
        need = {k: max(1, round(stock * s)) - have.get(k, 0) for k, s in tasks.KINDS.items()}
        kind = max(need, key=lambda k: need[k])
        if need[kind] <= 0:
            STATUS["text"] = ""
            return
        STATUS["text"] = f"готовлю задачи: {sum(have.values())} из {stock}"
        if promote():
            continue
        got = False
        for row, maker in candidates(kind):
            if STOPPING.is_set():
                return
            if prepare_one(row, maker, kind):
                got = True
                break
        tries = 0 if got else tries + 1
        if tries >= 5:  # nothing of this kind found five rounds in a row: try again later
            STATUS["text"] = ""
            events.log("prepare_short", task_kind=kind, have=have)
            return


def loop() -> None:
    from . import STOPPING

    STATUS["running"] = True
    try:
        while not STOPPING.is_set():
            if time.time() >= STATUS["paused_until"]:
                try:
                    run()
                    STATUS["error"] = ""
                except claude.ClaudeError as e:
                    STATUS.update(error=f"Claude: {e}", paused_until=time.time() + PAUSE_ON_ERROR, text="")
                    events.log("prepare_error", text=f"Claude: {e}")
                except (lichess_api.Unavailable, RuntimeError, chess.engine.EngineError, OSError) as e:
                    STATUS.update(error=str(e), paused_until=time.time() + PAUSE_ON_ERROR, text="")
                    events.log("prepare_error", text=str(e))
                    close()
            STOPPING.wait(CHECK_EVERY)
    finally:
        STATUS["running"] = False
        close()


def start_background() -> None:
    if not STATUS["running"]:
        threading.Thread(target=loop, name="prepare", daemon=True).start()


def status() -> dict:
    have = tasks.ready_counts()
    return {"ready": sum(have.values()), "stock": stock_size(), "by_kind": have, "text": STATUS["text"],
            "error": STATUS["error"]}


if __name__ == "__main__":  # python -m vision.sources.prepare: one round now, for a look
    import sys

    want = sys.argv[1] if len(sys.argv) > 1 else "tactic"
    for row, maker in candidates(want)[:3]:
        print(want, row.get("id") or row.get("internal_id"), "->", prepare_one(row, maker, want) or "отложена")
    print(json.dumps(status(), ensure_ascii=False))
    close()
