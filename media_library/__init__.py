"""Media Library & Video Sorter.

``__version__`` is the single source of truth for the release version: the
FastAPI metadata, the health and info payloads and the snapshot metadata all
read it, and ``tests/test_version.py`` fails if anything else restates it.

``import media_library`` has no side effects. The app is assembled by
:func:`media_library.app.create_app`, and ``python app.py`` /
``python -m media_library.app`` run it.
"""

__version__ = "3.1.0"
