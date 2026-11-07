"""Live fragmented-MP4 transcoding to a pipe.

Shared by the sorter and the library viewer so a non-web container plays
without waiting for a full re-encode. The browser can start as soon as the moov
atom is written; seeking restarts the stream at ``ss``.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator


def build_command(path: str, ss: float = 0.0) -> list[str]:
    command = ["ffmpeg", "-hide_banner", "-loglevel", "error"]
    if ss and ss > 0:
        command.extend(["-ss", str(ss)])
    command.extend(
        [
            "-i",
            path,
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-tune",
            "zerolatency",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-ac",
            "2",
            "-movflags",
            "frag_keyframe+empty_moov+default_base_moof",
            "-f",
            "mp4",
            "pipe:1",
        ]
    )
    return command


async def relay(process: asyncio.subprocess.Process) -> AsyncIterator[bytes]:
    """Yield ffmpeg's stdout and always terminate the process."""
    try:
        assert process.stdout is not None
        while True:
            chunk = await process.stdout.read(64 * 1024)
            if not chunk:
                break
            yield chunk
    finally:
        try:
            process.terminate()
            await process.wait()
        except (ProcessLookupError, OSError):
            pass
