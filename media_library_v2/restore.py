"""Restore a v2 snapshot while the application service is stopped.

    python -m media_library_v2.restore --yes

Replacing the SQLite database under running writers is not safe; the web
endpoint refuses unless ``V2_ALLOW_LIVE_RESTORE=true`` (post-cutover). This CLI
is the supported recovery path and requires the service to be stopped.
"""

from __future__ import annotations

import argparse
import json
import sys

from media_library_v2 import backups


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Restore the v2 library snapshot (service must be stopped)."
    )
    parser.add_argument("--yes", action="store_true", help="skip the confirmation")
    parser.add_argument("--db", help="override the target database path")
    parser.add_argument("--thumbnails", help="override the target thumbnail directory")
    args = parser.parse_args(argv)

    if not args.yes:
        print(
            "This replaces the live database and poster cache with the snapshot.\n"
            "Stop the media-library service first, then re-run with --yes.",
            file=sys.stderr,
        )
        return 2

    try:
        result = backups.restore_snapshot(
            target_db=args.db, target_thumbnails=args.thumbnails, allow_live=True
        )
    except Exception as exc:
        print(f"Restore failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    print("Restart the service to load the restored index.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
