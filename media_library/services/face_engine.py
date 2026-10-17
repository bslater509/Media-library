"""Face detection + embedding engine (OpenCV YuNet + SFace).

The only inference runtime this project needs: ``opencv-contrib-python-headless``
supplies both the detector (``cv2.FaceDetectorYN``) and the recogniser
(``cv2.FaceRecognizerSF``), so there is no torch/onnxruntime dependency. Both are
imported **lazily** and only when the feature is enabled, so an install without
OpenCV still starts and simply reports the engine as unavailable.

Two ONNX files are used and are not part of the wheel; they are downloaded once
into ``FACE_MODEL_DIR`` (default ``DATA_DIR/models``) and verified against a
pinned SHA-256 + size before being accepted, so a truncated or substituted
download can never be loaded:

* ``face_detection_yunet_2023mar.onnx`` — YuNet face detector (~230 KB).
* ``face_recognition_sface_2021dec.onnx`` — SFace 128-d face embeddings (~37 MB).

Detection runs on the 480px-wide scan images the Library already keeps on local
disk (cached thumbnails for images, cached candidate frames for videos), so the
worker never pulls a source file over the (possibly FUSE) mount.

Configuration is read from ``media_library.core.config`` at call time rather than
bound at import, so the test suite's sandboxed directories are honoured.
"""

import hashlib
import logging
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from media_library.core import config

logger = logging.getLogger(__name__)

YUNET_FILENAME = "face_detection_yunet_2023mar.onnx"
SFACE_FILENAME = "face_recognition_sface_2021dec.onnx"

# opencv_zoo release artefacts, pinned by content hash and size.
_MODEL_SPECS: dict[str, dict[str, Any]] = {
    YUNET_FILENAME: {
        "url": (
            "https://github.com/opencv/opencv_zoo/raw/main/models/"
            "face_detection_yunet/face_detection_yunet_2023mar.onnx"
        ),
        "sha256": "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4",
        "bytes": 232589,
    },
    SFACE_FILENAME: {
        "url": (
            "https://github.com/opencv/opencv_zoo/raw/main/models/"
            "face_recognition_sface/face_recognition_sface_2021dec.onnx"
        ),
        "sha256": "0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79",
        "bytes": 38696353,
    },
}

#: SFace embedding length (float32 values per face).
EMBEDDING_DIM = 128
DOWNLOAD_TIMEOUT_S = 120.0

#: Set by the test suite (or an embedder) to stand in for the OpenCV engine.
_ENGINE_OVERRIDE: Any = None
_ENGINE: Any = None

# Verification memo: the recogniser is 37 MB, and the status API reports model
# readiness on every poll, so the SHA-256 is only recomputed when a file's own
# size/mtime changed since it was last verified.
_VERIFY_CACHE: dict[str, dict[str, Any]] = {}


def get_model_dir() -> Path:
    """Return (creating it if needed) the directory holding the ONNX models."""
    configured = getattr(config, "FACE_MODEL_DIR", "") or ""
    base = (
        Path(configured).resolve() if configured else Path(config.DATA_DIR) / "models"
    )
    base.mkdir(parents=True, exist_ok=True)
    return base


def model_paths() -> dict[str, Path]:
    """Absolute paths the engine expects for both models."""
    model_dir = get_model_dir()
    return {
        "detector": model_dir / YUNET_FILENAME,
        "recognizer": model_dir / SFACE_FILENAME,
    }


def _verify(path: Path, spec: dict[str, Any]) -> bool:
    """True when ``path`` matches the pinned size and SHA-256 (memoized)."""
    key = str(path)
    try:
        stat = path.stat()
    except OSError:
        _VERIFY_CACHE.pop(key, None)
        return False
    if stat.st_size != spec["bytes"]:
        _VERIFY_CACHE[key] = {"size": stat.st_size, "mtime": stat.st_mtime, "ok": False}
        return False

    cached = _VERIFY_CACHE.get(key)
    if (
        cached
        and cached.get("size") == stat.st_size
        and cached.get("mtime") == stat.st_mtime
    ):
        return bool(cached.get("ok"))

    digest = hashlib.sha256()
    try:
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError:
        return False
    ok = digest.hexdigest() == spec["sha256"]
    _VERIFY_CACHE[key] = {"size": stat.st_size, "mtime": stat.st_mtime, "ok": ok}
    return ok


def download_models(force: bool = False) -> dict[str, Any]:
    """Fetch any missing model file, verifying size + SHA-256 before use.

    Returns ``{"success": bool, "downloaded": [...], "error": str, "model_dir": str}``.
    A partial or tampered download is discarded (the temp file is removed) so the
    next attempt starts clean.
    """
    model_dir = get_model_dir()
    downloaded: list[str] = []
    for name, spec in _MODEL_SPECS.items():
        target = model_dir / name
        if not force and _verify(target, spec):
            continue
        temp = target.with_name(f".tmp_{name}_{os.getpid()}")
        try:
            logger.info(f"[FaceEngine] Downloading {name} into {model_dir}...")
            request = urllib.request.Request(
                spec["url"], headers={"User-Agent": "media-library"}
            )
            with (
                urllib.request.urlopen(request, timeout=DOWNLOAD_TIMEOUT_S) as resp,
                open(temp, "wb") as fh,
            ):
                while True:
                    chunk = resp.read(1024 * 256)
                    if not chunk:
                        break
                    fh.write(chunk)
                fh.flush()
                os.fsync(fh.fileno())
            if not _verify(temp, spec):
                try:
                    temp.unlink()
                except OSError:
                    pass
                return {
                    "success": False,
                    "downloaded": downloaded,
                    "model_dir": str(model_dir),
                    "error": f"Downloaded {name} failed its size/SHA-256 check",
                }
            os.replace(temp, target)
            downloaded.append(name)
        except (urllib.error.URLError, OSError, TimeoutError) as e:
            try:
                temp.unlink(missing_ok=True)
            except OSError:
                pass
            return {
                "success": False,
                "downloaded": downloaded,
                "model_dir": str(model_dir),
                "error": f"Could not download {name}: {e}",
            }
        finally:
            try:
                temp.unlink(missing_ok=True)
            except OSError:
                pass
    return {
        "success": True,
        "downloaded": downloaded,
        "model_dir": str(model_dir),
        "error": "",
    }


def models_ready() -> bool:
    """True when both model files are present and match their pinned hashes."""
    paths = model_paths()
    return _verify(paths["detector"], _MODEL_SPECS[YUNET_FILENAME]) and _verify(
        paths["recognizer"], _MODEL_SPECS[SFACE_FILENAME]
    )


def _load_cv2():
    """Import OpenCV, or return None when it is not installed."""
    try:
        import cv2  # deliberately lazy: OpenCV is an optional dependency

        return cv2
    except Exception:  # pragma: no cover - depends on the install
        return None


def opencv_available() -> bool:
    """True when the optional OpenCV dependency can be imported."""
    return _load_cv2() is not None


def engine_status() -> dict[str, Any]:
    """Small status body explaining whether faces can be scanned right now."""
    if _ENGINE_OVERRIDE is not None:
        return {"available": True, "reason": "", "ready": True, "model_dir": ""}
    if _load_cv2() is None:
        return {
            "available": False,
            "ready": False,
            "reason": "OpenCV is not installed (pip install opencv-contrib-python-headless)",
            "model_dir": str(get_model_dir()),
        }
    paths = model_paths()
    if not models_ready():
        key_for = {YUNET_FILENAME: "detector", SFACE_FILENAME: "recognizer"}
        missing = [
            name
            for name, spec in _MODEL_SPECS.items()
            if not _verify(paths[key_for[name]], spec)
        ]
        return {
            "available": False,
            "ready": False,
            "reason": f"Face models not downloaded ({', '.join(sorted(missing))})",
            "model_dir": str(get_model_dir()),
        }
    return {
        "available": True,
        "ready": True,
        "reason": "",
        "model_dir": str(get_model_dir()),
    }


class FaceEngine:
    """Thin wrapper over a YuNet detector + SFace recogniser pair."""

    def __init__(self, detector_path: str | Path, recognizer_path: str | Path):
        cv2 = _load_cv2()
        if cv2 is None:
            raise RuntimeError("OpenCV is not installed")
        self._cv2 = cv2
        self._detector = cv2.FaceDetectorYN_create(
            str(detector_path), "", (320, 320), 0.7, 0.3, 5000
        )
        self._recognizer = cv2.FaceRecognizerSF_create(str(recognizer_path), "")

    def detect(self, image: Any, max_faces: int | None = None) -> list[dict[str, Any]]:
        """Detect faces in a BGR uint8 image and return boxes + embeddings.

        Each entry is ``{"x", "y", "w", "h", "score", "embedding"}`` where the box
        is in pixels on the scan image and ``embedding`` is an L2-normalised
        ``numpy.float32`` array of :data:`EMBEDDING_DIM` values.
        """
        import numpy as np

        if image is None or getattr(image, "size", 0) == 0:
            return []
        height, width = image.shape[:2]
        if height < 8 or width < 8:
            return []

        self._detector.setInputSize((width, height))
        try:
            _ret, raw = self._detector.detect(image)
        except Exception as e:  # a decode/detector failure must never kill the sweep
            logger.error(f"[FaceEngine] Detection failed: {e}")
            return []
        if raw is None or len(raw) == 0:
            return []

        confidence = float(getattr(config, "FACE_DETECTION_CONFIDENCE", 0.7))
        min_px = int(getattr(config, "FACE_MIN_FACE_PX", 48))
        limit = int(max_faces or getattr(config, "FACE_MAX_PER_FRAME", 20))

        faces: list[dict[str, Any]] = []
        for row in raw:
            try:
                score = float(row[-1])
                x, y, w, h = (
                    float(row[0]),
                    float(row[1]),
                    float(row[2]),
                    float(row[3]),
                )
            except (TypeError, ValueError, IndexError):
                continue
            if score < confidence or w < min_px or h < min_px:
                continue
            embedding = None
            try:
                aligned = self._recognizer.alignCrop(image, row)
                feature = np.asarray(
                    self._recognizer.feature(aligned), dtype="float32"
                ).reshape(-1)
                norm = float(np.linalg.norm(feature))
                if norm > 0:
                    embedding = feature / norm
            except Exception as e:
                logger.error(f"[FaceEngine] Could not embed a detected face: {e}")
            if embedding is None or embedding.shape[0] != EMBEDDING_DIM:
                continue
            faces.append(
                {
                    "x": int(max(0, round(x))),
                    "y": int(max(0, round(y))),
                    "w": int(max(1, round(w))),
                    "h": int(max(1, round(h))),
                    "score": score,
                    "embedding": embedding,
                }
            )

        # A crowd shot can yield many faces; keep the largest (closest to camera).
        faces.sort(key=lambda f: f["w"] * f["h"], reverse=True)
        return faces[:limit]


def get_engine() -> FaceEngine | None:
    """Return the shared engine, building it once per process.

    Returns None when OpenCV is missing or the models have not been downloaded.
    """
    global _ENGINE
    if _ENGINE_OVERRIDE is not None:
        return _ENGINE_OVERRIDE
    if _ENGINE is not None:
        return _ENGINE
    if _load_cv2() is None or not models_ready():
        return None
    paths = model_paths()
    try:
        _ENGINE = FaceEngine(paths["detector"], paths["recognizer"])
    except Exception as e:
        logger.error(f"[FaceEngine] Could not load the face models: {e}")
        _ENGINE = None
    return _ENGINE


def set_engine(engine: Any) -> None:
    """Install an engine (the test suite's deterministic fake, or a real one)."""
    global _ENGINE_OVERRIDE
    _ENGINE_OVERRIDE = engine


def reset_engine() -> None:
    """Forget the cached engine (used by tests and after a model download)."""
    global _ENGINE, _ENGINE_OVERRIDE
    _ENGINE = None
    _ENGINE_OVERRIDE = None
