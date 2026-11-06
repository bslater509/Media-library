"""Media Library v2 — a from-scratch rewrite served beside v1 at ``/v2``.

``__version__`` here is the v2 application version. It is deliberately separate
from ``media_library.__version__`` while both apps run side by side; at cutover
the v1 line is retired and the v2 version becomes the only one.
"""

__version__ = "3.0.0"
