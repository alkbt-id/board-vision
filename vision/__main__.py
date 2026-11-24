"""python -m vision — run the app's local server (the Mac window starts it by itself).

A test server next to the real app, with its own data folder:
    python -m vision --home <test folder> --port 8748
With «Шахматный тренер»'s iPad certificate in place it also serves the home network over https (vision/lan.py);
a test server does not (--no-lan is implied by --port).
"""

import argparse
import asyncio
import os


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m vision", description="Сервер «Видения доски».")
    parser.add_argument("--home", help="папка данных вместо ~/Library/Application Support/BoardVision (как BV_HOME)")
    parser.add_argument("--port", type=int, help="порт вместо 8747 (как BV_PORT); без iPad")
    args = parser.parse_args()
    if args.home:
        os.environ["BV_HOME"] = os.path.abspath(os.path.expanduser(args.home))
    if args.port:
        os.environ["BV_PORT"] = str(args.port)

    import uvicorn  # only now: vision.config reads the environment on import

    from . import lan
    from .config import PORT
    from .server import app

    async def serve() -> None:
        local = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=PORT, log_level="warning"))
        if args.port or not lan.enabled():
            return await local.serve()
        home = asyncio.create_task(lan.serve(app))
        try:
            await local.serve()
        finally:
            home.cancel()

    asyncio.run(serve())


if __name__ == "__main__":
    main()
