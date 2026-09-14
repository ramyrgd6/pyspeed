"""
Core measurement logic for pyspeed.

Uses Cloudflare's public speed-test endpoints (the same ones behind
speed.cloudflare.com), which require no API key and support arbitrary
payload sizes via query params:

    GET  https://speed.cloudflare.com/__down?bytes=N   -> N random bytes
    POST https://speed.cloudflare.com/__up              -> echoes body length

Both download and upload are exposed as generators that yield incremental
(elapsed_seconds, total_bytes) samples, so the CLI can render a live
updating speed readout instead of blocking until completion.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass

import requests

BASE_URL = "https://speed.cloudflare.com"
DOWN_URL = f"{BASE_URL}/__down"
UP_URL = f"{BASE_URL}/__up"

CHUNK_SIZE = 64 * 1024  # 64 KB read/write chunks
DEFAULT_DOWNLOAD_BYTES = 100_000_000  # 100 MB
DEFAULT_UPLOAD_BYTES = 25_000_000  # 25 MB
PING_SAMPLES = 8

# requests' default User-Agent ("python-requests/x.y") gets flagged by
# Cloudflare's bot protection, especially on larger, more "expensive"
# requests — a 0-byte ping can slip through where a 100MB download can't.
# Mimicking a real browser (and setting a plausible Referer/Origin, since
# the actual speed.cloudflare.com page always sends these) avoids the 403.
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Referer": f"{BASE_URL}/",
    "Origin": BASE_URL,
}


def _session() -> requests.Session:
    """A requests.Session pre-configured with browser-like headers."""
    session = requests.Session()
    session.headers.update(_HEADERS)
    return session


@dataclass
class PingResult:
    latency_ms: float
    jitter_ms: float
    samples_ms: list[float]


def measure_ping(samples: int = PING_SAMPLES, timeout: float = 5.0) -> PingResult:
    """Measure round-trip latency using tiny (0-byte) requests."""
    if samples < 1:
        raise ValueError("samples must be at least 1")
    if timeout <= 0:
        raise ValueError("timeout must be greater than 0")

    times: list[float] = []
    session = _session()
    for _ in range(samples):
        start = time.perf_counter()
        try:
            response = session.get(DOWN_URL, params={"bytes": 0}, timeout=timeout)
            response.raise_for_status()
        except requests.RequestException:
            continue
        elapsed_ms = (time.perf_counter() - start) * 1000
        times.append(elapsed_ms)

    if not times:
        raise ConnectionError("Could not reach speed test server for ping.")

    latency_ms = sum(times) / len(times)
    if len(times) > 1:
        diffs = [abs(times[i] - times[i - 1]) for i in range(1, len(times))]
        jitter_ms = sum(diffs) / len(diffs)
    else:
        jitter_ms = 0.0

    return PingResult(latency_ms=latency_ms, jitter_ms=jitter_ms, samples_ms=times)


def measure_download(total_bytes: int = DEFAULT_DOWNLOAD_BYTES, timeout: float = 20.0):
    """
    Generator yielding (elapsed_seconds, bytes_so_far) as data streams in.
    Final yield represents the completed transfer.
    """
    _validate_transfer(total_bytes, timeout)
    start = time.perf_counter()
    downloaded = 0
    session = _session()
    with session.get(
        DOWN_URL, params={"bytes": total_bytes}, stream=True, timeout=timeout
    ) as response:
        response.raise_for_status()
        for chunk in response.iter_content(chunk_size=CHUNK_SIZE):
            if not chunk:
                continue
            downloaded += len(chunk)
            yield (time.perf_counter() - start, downloaded)
    if downloaded != total_bytes:
        raise ConnectionError(
            f"Download incomplete: received {downloaded} of {total_bytes} bytes."
        )


def _validate_transfer(total_bytes: int, timeout: float) -> None:
    if total_bytes < 1:
        raise ValueError("transfer size must be at least 1 byte")
    if timeout <= 0:
        raise ValueError("timeout must be greater than 0")


class _UploadStream:
    """File-like random data stream that preserves a fixed request length."""

    def __init__(self, total_bytes: int, progress: dict[str, int], lock):
        self.remaining = total_bytes
        self.progress = progress
        self.lock = lock

    def read(self, size: int = -1) -> bytes:
        if self.remaining <= 0:
            return b""
        if size < 0:
            size = CHUNK_SIZE
        size = min(size, CHUNK_SIZE, self.remaining)
        self.remaining -= size
        with self.lock:
            self.progress["sent"] += size
        return os.urandom(size)


def measure_upload(total_bytes: int = DEFAULT_UPLOAD_BYTES, timeout: float = 20.0):
    """
    Generator yielding (elapsed_seconds, bytes_so_far) as data is sent.

    requests doesn't give us a native progress callback for uploads, so we
    wrap the chunk generator to track bytes produced, run the request in
    a background thread, and yield progress samples from the main thread
    on a fixed interval until the upload finishes.
    """
    _validate_transfer(total_bytes, timeout)
    import threading

    progress = {"sent": 0, "done": False, "error": None}
    lock = threading.Lock()

    def worker():
        try:
            session = _session()
            stream = _UploadStream(total_bytes, progress, lock)
            response = session.post(
                UP_URL,
                data=stream,
                headers={"Content-Length": str(total_bytes)},
                timeout=timeout,
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            progress["error"] = exc
        finally:
            progress["done"] = True

    start = time.perf_counter()
    thread = threading.Thread(target=worker, daemon=True)
    thread.start()

    while True:
        time.sleep(0.1)
        with lock:
            sent = progress["sent"]
        yield (time.perf_counter() - start, sent)
        if progress["done"]:
            if progress["error"] is not None:
                raise ConnectionError(f"Upload failed: {progress['error']}")
            break

    thread.join()


def bytes_to_mbps(num_bytes: int, seconds: float) -> float:
    """Convert bytes transferred over N seconds into megabits per second."""
    if seconds <= 0:
        return 0.0
    bits = num_bytes * 8
    return (bits / seconds) / 1_000_000
