"""
Media Library & Video Sorter Application Entrypoint.

This module acts as the application entrypoint and CLI runner, delegating
to the modularized `media_library` package architecture.
"""

import os
import argparse
import uvicorn

from media_library.app_factory import create_app
from media_library.core.config import (
    BASE_DIR,
    DATA_DIR,
    DEFAULT_DESTINATION_DIR,
    DB_PATH,
    PORT,
    HOST,
)
from media_library.core.state import state, format_size
from media_library.services.media_service import get_lan_ip
from media_library.db.connection import get_db
from media_library.db.schema import init_backup_db

# Create default FastAPI application instance for ASGI servers (uvicorn, gunicorn)
app = create_app()


def main():
    parser = argparse.ArgumentParser(description="Web-based Media Library & Video Sorter")
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("PORT", PORT)),
        help="Port to listen on (default: 8888)",
    )
    parser.add_argument(
        "--host",
        type=str,
        default=os.environ.get("HOST", HOST),
        help="Host interface (default: 0.0.0.0)",
    )
    parser.add_argument(
        "--source",
        type=str,
        default=os.environ.get("INCOMING_DIR", ""),
        help="Initial source directory",
    )
    parser.add_argument(
        "--dest",
        type=str,
        default=DEFAULT_DESTINATION_DIR,
        help="Destination directory (default: configured LIBRARY_DIR)",
    )
    args = parser.parse_args()

    if args.source:
        state["source_dir"] = os.path.abspath(args.source)
    if args.dest:
        state["destination_dir"] = os.path.abspath(args.dest)

    lan_ip = get_lan_ip()
    print("\n" + "=" * 65)
    print("  📁 Media Library & Video Sorter Starting")
    print(f"  Media Library:   http://{lan_ip}:{args.port}")
    print(f"  Incoming Sorter: http://{lan_ip}:{args.port}/sorter")
    print(f"  Local Machine:   http://localhost:{args.port}")
    print(f"  Destination Dir: {state['destination_dir']}")
    if state["source_dir"]:
        print(f"  Source Dir:      {state['source_dir']}")
    print(f"  Data Dir:        {DATA_DIR}")
    print(f"  Database:        {DB_PATH}")
    print("=" * 65 + "\n")

    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
