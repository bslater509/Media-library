"""Optional image-format decoders, registered once per process.

The client exists for iOS media picking, so HEIC/HEIF images legally land in
the library (uploads, DNG sidecars alike). Pillow cannot decode them without
``pillow_heif``'s opener, which is listed in requirements.txt but was never
wired up — those photos ended up without posters, without probed dimensions
and un-convertible to WebP. Registering the opener makes ``Image.open``
transparently decode HEIC/HEIF everywhere in the app.
"""

from __future__ import annotations

_registered = False


def register_heif() -> None:
    """Register the HEIF/HEIC opener once; a missing dependency is tolerated."""
    global _registered
    if _registered:
        return
    try:
        from pillow_heif import register_heif_opener

        register_heif_opener()
    except ImportError:
        # pillow-heif is not installed: HEIC files keep failing the same way
        # they always did, as undecodable images.
        pass
    _registered = True
