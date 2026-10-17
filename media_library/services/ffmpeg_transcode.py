"""Live FFmpeg transcoding command construction and stdout relay."""

import asyncio
import logging
from collections.abc import AsyncIterator

logger = logging.getLogger(__name__)


def build_live_transcode_command(input_path: str, ss: float = 0.0) -> list[str]:
    """argv for on-the-fly fragmented-MP4 transcoding to pipe:1."""
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
    ]
    if ss and ss > 0:
        cmd.extend(["-ss", str(ss)])
    cmd.extend(
        [
            "-i",
            input_path,
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
    return cmd


async def relay_process_stdout(
    process: asyncio.subprocess.Process,
) -> AsyncIterator[bytes]:
    """Yield stdout in 64 KiB chunks and always terminate the process."""
    try:
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
            # Process already exited (or pipe gone) on stream end — cleanup
            # is best-effort and must never mask the streamed response.
            pass
