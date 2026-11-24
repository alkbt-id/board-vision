# «Видение доски»

A drill for seeing the whole board: the opponent makes a move, and you draw an arrow "who takes → whom" on every
piece of yours that is now under attack (or answer «Угроз нет»). Positions come from your own games (Lichess,
chess.com), other people's rapid games at your level (Lichess open database) and Lichess puzzles turned around;
quiet positions are checked by Stockfish. Why this drill: `~/develop/chess/reports/Методы против зевков в шахматах.md`.

- Run: `~/Applications/Видение доски.app` (build it with `mac/build.sh` after `uv sync`).
- iPad: `https://<mac>.local:8444` — reuses «Шахматный тренер»'s certificate and key (`vision/lan.py`).
- Data: `~/Library/Application Support/BoardVision/` (`vision/config.py`).
- Event log, live: `uv run python -m vision.watch` (`vision/events.py` lists the events).
- Test server: `uv run python -m vision --home <scratch folder> --port 8748`.
- Tests: `uv run pytest`.
- Sounds (non-free, not in git): `scripts/fetch_sounds.sh`.

License GPL-3.0 (chessground is vendored).
