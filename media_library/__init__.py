"""Media Library & Video Sorter Package.

``__version__`` is the single source of truth for the application version.
``media_library.app_factory`` (the FastAPI app metadata), the ``/api/system/status``
payload and the backup snapshot metadata all import it from here, and
``tests/test_version.py`` fails if any of them — or ``package.json`` — drifts.
"""

__version__ = "1.1.6"
