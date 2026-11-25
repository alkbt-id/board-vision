# «Видение доски»

A drill for seeing the whole board: the opponent makes a move, and you draw an arrow "who takes → whom" on every
piece of yours that is now under attack (or answer «Угроз нет»). Positions come from your own games (Lichess,
chess.com), other people's rapid games at your level (Lichess open database) and Lichess puzzles turned around;
quiet positions are checked by Stockfish. Why this drill: `~/develop/chess/reports/Методы против зевков в шахматах.md`.

Tasks for the move (`vision/tasks.py`): a series starts on one of six kinds of positions — his threat, a tactic (the
whole line counts), a quiet position, a trap, a strategic decision, an endgame — without saying which. Your move is the
one try; a wrong one shows the solution on the board and why, next to it. The explanations are prepared in the
background by Claude through your Claude Code login (`vision/sources/prepare.py`, a stock of 150 tasks); each task is
rated on the scale of game ratings with Maia 1100–1900, and picked a little below your «Решения» rating, which starts
at your Lichess rapid rating.

- Run: `~/Applications/Видение доски.app` (build it with `mac/build.sh` after `uv sync`).
- iPad: `https://<mac>.local:8444` — reuses «Шахматный тренер»'s certificate and key (`vision/lan.py`).
- Data: `~/Library/Application Support/BoardVision/` (`vision/config.py`).
- Event log, live: `uv run python -m vision.watch` (`vision/events.py` lists the events).
- Test server: `uv run python -m vision --home <scratch folder> --port 8748`.
- Tests: `uv run pytest`.
- Sounds (non-free, not in git): `scripts/fetch_sounds.sh`.

Data used: Lichess puzzles and games (CC0, database.lichess.org), the Lichess tablebase API, Maia nets (CSSLab, GPL-3.0,
downloaded to the data folder), and the positional set by neilgd and the players who checked it — WIM/FM Liwia Jarocka,
WGM Michalina Rudzińska, WGM Margarita Voyska (MIT, https://github.com/neilgd/chess-position-analysis-results).

License GPL-3.0 (chessground is vendored).
