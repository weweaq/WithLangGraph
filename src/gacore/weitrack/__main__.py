"""python -m gacore.weitrack 启动接收服务。"""
from __future__ import annotations

import argparse
from pathlib import Path

import uvicorn

from gacore.weitrack.server import create_app
from gacore.weitrack.storage import Storage


def main() -> None:
    parser = argparse.ArgumentParser(description="weiTrack ingest server")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--db", default="wei_track.db", help="SQLite 文件路径")
    args = parser.parse_args()

    storage = Storage(Path(args.db))
    app = create_app(storage)
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
