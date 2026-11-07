"""Entrypoint: ``python app.py [--host HOST] [--port PORT]``.

The application itself lives in :mod:`media_library.app`; this wrapper exists so
the systemd unit, ``start.sh`` and a bare ``python app.py`` all point at one
familiar script.
"""

from media_library.app import main

if __name__ == "__main__":
    main()
