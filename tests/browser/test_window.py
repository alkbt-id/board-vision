"""The window in Chrome against a test server (`uv run pytest -m browser`, before merging a change of the window):
the start screen's layout and a task's review on the board. The server runs on a data folder of the tests' own
(conftest), seeded here with positions of three classic games and one task, and without downloads, task preparation
or Claude (BV_OFFLINE). The page is opened with ?test, which gives window.__bv: the board's position and shapes."""

import io
import os
import socket
import subprocess
import sys
import time

import chess
import chess.pgn
import httpx
import pytest

from conftest import BACK_RANK, BACK_RANK_MOVE, BACK_RANK_STEPS, add_task
from vision import config, sources, store

pytestmark = pytest.mark.browser
sync_api = pytest.importorskip("playwright.sync_api")

GAMES = [
    "1.e4 e5 2.Nf3 d6 3.d4 Bg4 4.dxe5 Bxf3 5.Qxf3 dxe5 6.Bc4 Nf6 7.Qb3 Qe7 8.Nc3 c6 9.Bg5 b5 10.Nxb5 cxb5 "
    "11.Bxb5+ Nbd7 12.O-O-O Rd8 13.Rxd7 Rxd7 14.Rd1 Qe6 15.Bxd7+ Nxd7 16.Qb8+ Nxb8 17.Rd8#",
    "1.e4 e5 2.f4 exf4 3.Bc4 Qh4+ 4.Kf1 b5 5.Bxb5 Nf6 6.Nf3 Qh6 7.d3 Nh5 8.Nh4 Qg5 9.Nf5 c6 10.g4 Nf6 11.Rg1 cxb5 "
    "12.h4 Qg6 13.h5 Qg5 14.Qf3 Ng8 15.Bxf4 Qf6 16.Nc3 Bc5 17.Nd5 Qxb2 18.Bd6 Bxg1 19.e5 Qxa1+ 20.Ke2 Na6 "
    "21.Nxg7+ Kd8 22.Qf6+ Nxf6 23.Be7#",
    "1.e4 e5 2.Nf3 Nc6 3.Bc4 Bc5 4.b4 Bxb4 5.c3 Ba5 6.d4 exd4 7.O-O d3 8.Qb3 Qf6 9.e5 Qg6 10.Re1 Nge7 11.Ba3 b5 "
    "12.Qxb5 Rb8 13.Qa4 Bb6 14.Nbd2 Bb7 15.Ne4 Qf5 16.Bxd3 Qh5 17.Nf6+ gxf6 18.exf6 Rg8 19.Rad1 Qxf3 20.Rxe7+ Nxe7 "
    "21.Qxd7+ Kxd7 22.Bf5+ Ke8 23.Bd7+ Kf8 24.Bxe7#",
]


def _seed() -> None:
    for t in ("items", "attempts", "schedule", "sessions", "meta", "tasks", "decisions", "skips"):
        store.run(f"DELETE FROM {t}")
    for i, text in enumerate(GAMES):
        moves = list(chess.pgn.read_game(io.StringIO(text)).mainline_moves())
        for user in (chess.WHITE, chess.BLACK):
            sources.sample_game(chess.Board(), moves, user, "own", f"game{i}")
    store.run("UPDATE items SET quiet_ok = 1 WHERE n_threats = 0")
    store.meta_set("decisions_start", "1300")
    store.meta_set("last_refresh", str(int(time.time() * 1000)))
    store.meta_set("rule_version", "8")
    add_task(BACK_RANK, BACK_RANK_MOVE, BACK_RANK_STEPS, rating=1200)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def server():
    if not config.stockfish():
        pytest.skip("нет Stockfish")
    _seed()
    port = _free_port()
    env = {**os.environ, "BV_OFFLINE": "1"}
    proc = subprocess.Popen([sys.executable, "-m", "vision", "--home", str(config.HOME), "--port", str(port)], env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    url = f"http://127.0.0.1:{port}"
    for _ in range(100):
        try:
            if httpx.get(f"{url}/api/ping", timeout=1).status_code == 200:
                break
        except httpx.HTTPError:
            time.sleep(0.1)
    else:
        proc.kill()
        pytest.fail("тестовый сервер не ответил")
    assert httpx.get(f"{url}/api/state").json()["ready"], "мало позиций для старта"
    yield url
    proc.terminate()
    proc.wait(10)


@pytest.fixture(scope="module")
def browser():
    with sync_api.sync_playwright() as pw:
        try:
            br = pw.chromium.launch(channel="chrome", headless=True)
        except Exception as e:  # noqa: BLE001
            pytest.skip(f"нет Chrome: {e}")
        yield br
        br.close()


def _boxes(page, selectors):
    return {s: page.locator(s).first.bounding_box() for s in selectors}


def _overlap(a, b) -> bool:
    return not (a["x"] + a["width"] <= b["x"] or b["x"] + b["width"] <= a["x"]
                or a["y"] + a["height"] <= b["y"] or b["y"] + b["height"] <= a["y"])


@pytest.mark.parametrize("size", [(1280, 860), (820, 1180), (1000, 1400)])
def test_start_screen_nothing_overlaps(server, browser, size):
    page = browser.new_page(viewport={"width": size[0], "height": size[1]})
    page.goto(f"{server}/?test")
    page.wait_for_selector("#ch-dec svg")
    sel = ["#go", "#level", "#ch-rating", "#ch-dec", "#ch-time", "details.rules summary", "#board-wrap"]
    boxes = _boxes(page, sel)
    for s, b in boxes.items():
        assert b and b["width"] > 0 and b["x"] >= 0 and b["x"] + b["width"] <= size[0] + 1, s
    names = list(boxes)
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            assert not _overlap(boxes[a], boxes[b]), f"{a} налезает на {b} ({size})"
    assert page.locator(".hero, .heroes").count() == 0  # no big numbers: the charts show them
    page.close()


def _square_xy(page, sq: int, white: bool):
    box = page.locator("cg-board").bounding_box()
    f, r = chess.square_file(sq), chess.square_rank(sq)
    if not white:
        f, r = 7 - f, 7 - r
    size = box["width"] / 8
    return box["x"] + (f + 0.5) * size, box["y"] + (7 - r + 0.5) * size


def _drag(page, uci: str, white: bool = True):
    m = chess.Move.from_uci(uci)
    x1, y1 = _square_xy(page, m.from_square, white)
    x2, y2 = _square_xy(page, m.to_square, white)
    page.mouse.move(x1, y1)
    page.mouse.down()
    page.mouse.move((x1 + x2) / 2, (y1 + y2) / 2, steps=4)
    page.mouse.move(x2, y2, steps=4)
    page.mouse.up()


def _strip(page) -> str:
    return page.inner_text("#bm-text")


def test_a_wrong_move_in_a_task_is_shown_on_the_board(server, browser):
    page = browser.new_page(viewport={"width": 1280, "height": 860})
    page.goto(f"{server}/?test")
    page.wait_for_selector("#go:not([disabled])")
    page.click("#go")
    page.wait_for_function("() => window.__bv && window.__bv.phase() === 'draw'")
    task = chess.Board(BACK_RANK)
    task.push_uci(BACK_RANK_MOVE)
    assert page.evaluate("window.__bv.fen()") == task.board_fen()
    page.click("#bm-btn")  # «Ничего нет»
    page.wait_for_function("() => ['plan', 'review'].includes(window.__bv.phase())")
    if page.evaluate("window.__bv.phase()") == "review":  # step 1 had something to mark: its review first
        page.click("#bm-btn")
    page.wait_for_function("() => window.__bv.phase() === 'plan'")
    _drag(page, "g2g3")
    page.click("#bm-btn")  # «Хожу»
    page.wait_for_function("() => document.querySelector('#explain') && !document.querySelector('#explain').hidden")
    assert _strip(page).startswith("Тактика — надо было Re8+")  # «надо было», never «верно»
    text = page.inner_text("#explain")
    assert "Надо было:" in text and "Твой ход:" in text
    shapes = page.evaluate("window.__bv.shapes()")
    assert [(s["orig"], s.get("dest")) for s in shapes["user"]] == [("g2", "g3")]  # your move, grey ✕
    auto = [(s["orig"], s.get("dest")) for s in shapes["auto"]]
    assert ("e2", "e8") in auto  # the solution, numbered
    yours = page.locator('#explain .exmv[data-line="yours"]')
    assert yours.count() >= 2  # your move and his answer
    his = page.evaluate("""() => [...document.querySelectorAll('#explain .exmv[data-line="yours"]')][1].className""")
    assert "he" in his
    assert len(auto) >= 4  # his answer is drawn too (red, with a sign), not only the solution
    # step through the right line on the board, and back to the start
    page.click('#explain .exmv[data-line="best"][data-i="0"]')
    b = task.copy()
    b.push_uci("e2e8")
    assert page.evaluate("window.__bv.fen()") == b.board_fen()
    page.keyboard.press("ArrowRight")
    b.push_uci("c8e8")
    assert page.evaluate("window.__bv.fen()") == b.board_fen()
    page.click('#explain .exname[data-line="best"]')
    assert page.evaluate("window.__bv.fen()") == task.board_fen()
    assert len(page.evaluate("window.__bv.shapes()")["user"]) == 1
    page.click("#bm-btn")  # «Дальше»: your move is played, the review goes away
    page.wait_for_function("() => document.querySelector('#explain').hidden")
    page.close()
