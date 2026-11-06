"""Root entrypoint for the standalone v2 app (post-cutover).

Equivalent to ``python -m media_library_v2.standalone``; kept at the repository
root so a systemd unit can point at a familiar script, mirroring ``app.py``.
"""

from media_library_v2.standalone import main

if __name__ == "__main__":
    main()
