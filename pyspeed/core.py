"""Concurrent network measurement and thread-safe session statistics."""

from __future__ import annotations

import os
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Literal

import requests

logger = logging.getLogger("pyspeed.network")

BASE_URL = "https://speed.cloudflare.com"
DOWN_URL = f"{BASE_URL}/__down"
UP_URL = f"{BASE_URL}/__up"
CHUNK_SIZE = 64 * 1024
DEFAULT_DOWNLOAD_BYTES = 100_000_000
DEFAULT_UPLOAD_BYTES = 25_000_000
PING_SAMPLES = 8
RETRY_DELAY = 1.0
MAX_RETRY_DELAY = 15.0

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Referer": f"{BASE_URL}/",
    "Origin": BASE_URL,
}


def _session() -> requests.Session:
    session = requests.Session()
    session.headers.update(_HEADERS)
    return session


@dataclass
class PingResult:
    latency_ms: float
    jitter_ms: float
    samples_ms: list[float]
    packet_loss_percent: float = 0.0


@dataclass(frozen=True)
class StatsSnapshot:
    elapsed_seconds: float
    current_download_mbps: float
    current_upload_mbps: float
    average_download_mbps: float
    average_upload_mbps: float
    peak_download_mbps: float
    peak_upload_mbps: float
    downloaded_bytes: int
    uploaded_bytes: int
    ping_ms: float | None
    jitter_ms: float | None
    packet_loss_percent: float
    download_history: tuple[float, ...] = ()
    upload_history: tuple[float, ...] = ()
    last_error: str | None = None


class SessionStats:
    """Lock-protected counters and rolling throughput samples."""

    def __init__(self, history_limit: int = 64):
        self._lock = threading.Lock()
        self._started = time.monotonic()
        self._history_limit = history_limit
        self._downloaded = 0
        self._uploaded = 0
        self._download_current = 0.0
        self._upload_current = 0.0
        self._download_peak = 0.0
        self._upload_peak = 0.0
        self._download_history: list[float] = []
        self._upload_history: list[float] = []
        self._last_transfer: dict[str, tuple[int, float]] = {}
        self._ping: PingResult | None = None
        self._ping_attempts = 0
        self._ping_failures = 0
        self._last_error: str | None = None

    def record_transfer(self, direction: Literal["download", "upload"], amount: int) -> None:
        now = time.monotonic()
        with self._lock:
            if direction == "download":
                self._downloaded += amount
                total = self._downloaded
                history = self._download_history
            else:
                self._uploaded += amount
                total = self._uploaded
                history = self._upload_history
            previous_total, previous_time = self._last_transfer.get(direction, (0, now))
            elapsed = now - previous_time
            speed = bytes_to_mbps(total - previous_total, elapsed)
            self._last_transfer[direction] = (total, now)
            history.append(speed)
            del history[:-self._history_limit]
            if direction == "download":
                self._download_current = speed
                self._download_peak = max(self._download_peak, speed)
            else:
                self._upload_current = speed
                self._upload_peak = max(self._upload_peak, speed)

    def record_ping(self, result: PingResult | None, success: bool) -> None:
        with self._lock:
            self._ping_attempts += 1
            if not success:
                self._ping_failures += 1
            if result is not None:
                self._ping = result

    def record_error(self, message: str) -> None:
        with self._lock:
            self._last_error = message

    def snapshot(self) -> StatsSnapshot:
        now = time.monotonic()
        with self._lock:
            elapsed = max(now - self._started, 0.0)
            loss = (self._ping_failures / self._ping_attempts * 100) if self._ping_attempts else 0.0
            return StatsSnapshot(
                elapsed_seconds=elapsed,
                current_download_mbps=self._download_current,
                current_upload_mbps=self._upload_current,
                average_download_mbps=bytes_to_mbps(self._downloaded, elapsed),
                average_upload_mbps=bytes_to_mbps(self._uploaded, elapsed),
                peak_download_mbps=self._download_peak,
                peak_upload_mbps=self._upload_peak,
                downloaded_bytes=self._downloaded,
                uploaded_bytes=self._uploaded,
                ping_ms=self._ping.latency_ms if self._ping else None,
                jitter_ms=self._ping.jitter_ms if self._ping else None,
                packet_loss_percent=loss,
                download_history=tuple(self._download_history),
                upload_history=tuple(self._upload_history),
                last_error=self._last_error,
            )


class _UploadStream:
    def __init__(self, total_bytes: int, stats: SessionStats, stop_event: threading.Event):
        self.remaining = total_bytes
        self.stats = stats
        self.stop_event = stop_event

    def read(self, size: int = -1) -> bytes:
        if self.remaining <= 0 or self.stop_event.is_set():
            return b""
        size = CHUNK_SIZE if size < 0 else min(size, CHUNK_SIZE)
        size = min(size, self.remaining)
        self.remaining -= size
        chunk = os.urandom(size)
        self.stats.record_transfer("upload", len(chunk))
        return chunk


class NetworkRunner:
    """Run ping, download, and upload workers until the stop event is set."""

    def __init__(self, stats: SessionStats, download_bytes: int, upload_bytes: int,
                 timeout: float = 20.0, ping_interval: float = 1.0,
                 enable_download: bool = True, enable_upload: bool = True,
                 enable_ping: bool = True):
        _validate_transfer(download_bytes, timeout)
        _validate_transfer(upload_bytes, timeout)
        if ping_interval <= 0:
            raise ValueError("ping_interval must be greater than 0")
        self.stats = stats
        self.download_bytes = download_bytes
        self.upload_bytes = upload_bytes
        self.timeout = timeout
        self.ping_interval = ping_interval
        self.enable_download = enable_download
        self.enable_upload = enable_upload
        self.enable_ping = enable_ping
        self.stop_event = threading.Event()
        self._threads: list[threading.Thread] = []
        self._sessions: set[requests.Session] = set()
        self._sessions_lock = threading.Lock()
        self._responses: set[requests.Response] = set()
        self._responses_lock = threading.Lock()

    def start(self) -> None:
        workers = []
        if self.enable_download:
            workers.append((self._download_worker, "pyspeed-download"))
        if self.enable_upload:
            workers.append((self._upload_worker, "pyspeed-upload"))
        if self.enable_ping:
            workers.append((self._ping_worker, "pyspeed-ping"))
        self._threads = [threading.Thread(target=target, name=name, daemon=True) for target, name in workers]
        for thread in self._threads:
            thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        with self._sessions_lock:
            sessions = list(self._sessions)
        for session in sessions:
            session.close()
        with self._responses_lock:
            responses = list(self._responses)
        for response in responses:
            response.close()

    def join(self, timeout: float | None = None) -> None:
        for thread in self._threads:
            thread.join(timeout)

    def _register(self, session: requests.Session) -> None:
        with self._sessions_lock:
            self._sessions.add(session)

    def _unregister(self, session: requests.Session) -> None:
        with self._sessions_lock:
            self._sessions.discard(session)
        session.close()

    def _register_response(self, response: requests.Response) -> None:
        with self._responses_lock:
            self._responses.add(response)

    def _unregister_response(self, response: requests.Response) -> None:
        with self._responses_lock:
            self._responses.discard(response)
        response.close()

    def _download_worker(self) -> None:
        self._run_with_retry("download", self._download_attempt)

    def _download_attempt(self) -> None:
        session = _session()
        self._register(session)
        try:
            response = session.get(DOWN_URL, params={"bytes": self.download_bytes}, stream=True, timeout=self.timeout)
            self._register_response(response)
            try:
                response.raise_for_status()
                for chunk in response.iter_content(chunk_size=CHUNK_SIZE):
                    if self.stop_event.is_set():
                        return
                    if chunk:
                        self.stats.record_transfer("download", len(chunk))
            finally:
                self._unregister_response(response)
        finally:
            self._unregister(session)

    def _upload_worker(self) -> None:
        self._run_with_retry("upload", self._upload_attempt)

    def _upload_attempt(self) -> None:
        session = _session()
        self._register(session)
        try:
            stream = _UploadStream(self.upload_bytes, self.stats, self.stop_event)
            response = session.post(
                UP_URL,
                data=stream,
                headers={"Content-Length": str(self.upload_bytes)},
                timeout=self.timeout,
            )
            self._register_response(response)
            try:
                response.raise_for_status()
            finally:
                self._unregister_response(response)
        finally:
            self._unregister(session)

    def _ping_worker(self) -> None:
        session = _session()
        self._register(session)
        try:
            while not self.stop_event.is_set():
                start = time.perf_counter()
                try:
                    response = session.get(DOWN_URL, params={"bytes": 0}, timeout=self.timeout)
                    response.raise_for_status()
                    latency = (time.perf_counter() - start) * 1000
                    previous = self.stats.snapshot().ping_ms
                    jitter = abs(latency - previous) if previous is not None else 0.0
                    self.stats.record_ping(PingResult(latency, jitter, [latency]), True)
                except requests.RequestException as exc:
                    self.stats.record_ping(None, False)
                    self.stats.record_error(f"ping retry: {exc}")
                    logger.warning("ping retry: %s", exc)
                self.stop_event.wait(self.ping_interval)
        finally:
            self._unregister(session)

    def _run_with_retry(self, direction: str, attempt) -> None:
        delay = RETRY_DELAY
        while not self.stop_event.is_set():
            try:
                attempt()
                delay = RETRY_DELAY
            except (requests.RequestException, ConnectionError) as exc:
                self.stats.record_error(f"{direction} retry: {exc}")
                logger.warning("%s retry in %.1fs: %s", direction, delay, exc)
                self.stop_event.wait(delay)
                delay = min(delay * 2, MAX_RETRY_DELAY)


def _validate_transfer(total_bytes: int, timeout: float) -> None:
    if total_bytes < 1:
        raise ValueError("transfer size must be at least 1 byte")
    if timeout <= 0:
        raise ValueError("timeout must be greater than 0")


def measure_ping(samples: int = PING_SAMPLES, timeout: float = 5.0) -> PingResult:
    if samples < 1:
        raise ValueError("samples must be at least 1")
    if timeout <= 0:
        raise ValueError("timeout must be greater than 0")
    times: list[float] = []
    session = _session()
    try:
        for _ in range(samples):
            start = time.perf_counter()
            try:
                response = session.get(DOWN_URL, params={"bytes": 0}, timeout=timeout)
                response.raise_for_status()
            except requests.RequestException:
                continue
            times.append((time.perf_counter() - start) * 1000)
    finally:
        session.close()
    if not times:
        raise ConnectionError("Could not reach speed test server for ping.")
    diffs = [abs(times[index] - times[index - 1]) for index in range(1, len(times))]
    return PingResult(sum(times) / len(times), sum(diffs) / len(diffs) if diffs else 0.0, times)


def measure_download(total_bytes: int = DEFAULT_DOWNLOAD_BYTES, timeout: float = 20.0):
    """Compatibility generator for one finite download transfer."""
    _validate_transfer(total_bytes, timeout)
    start = time.perf_counter()
    downloaded = 0
    session = _session()
    try:
        with session.get(DOWN_URL, params={"bytes": total_bytes}, stream=True, timeout=timeout) as response:
            response.raise_for_status()
            for chunk in response.iter_content(chunk_size=CHUNK_SIZE):
                if chunk:
                    downloaded += len(chunk)
                    yield time.perf_counter() - start, downloaded
    finally:
        session.close()
    if downloaded != total_bytes:
        raise ConnectionError(f"Download incomplete: received {downloaded} of {total_bytes} bytes.")


def measure_upload(total_bytes: int = DEFAULT_UPLOAD_BYTES, timeout: float = 20.0):
    """Compatibility generator for one finite upload transfer."""
    _validate_transfer(total_bytes, timeout)
    stats = SessionStats()
    stop_event = threading.Event()
    result: dict[str, Exception | None] = {"error": None}

    def worker() -> None:
        session = _session()
        try:
            response = session.post(
                UP_URL,
                data=_UploadStream(total_bytes, stats, stop_event),
                headers={"Content-Length": str(total_bytes)},
                timeout=timeout,
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            result["error"] = exc
        finally:
            session.close()
            stop_event.set()

    start = time.perf_counter()
    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    while not stop_event.wait(0.1):
        yield time.perf_counter() - start, stats.snapshot().uploaded_bytes
    thread.join()
    yield time.perf_counter() - start, stats.snapshot().uploaded_bytes
    if result["error"] is not None:
        raise ConnectionError(f"Upload failed: {result['error']}")


def bytes_to_mbps(num_bytes: int, seconds: float) -> float:
    if seconds <= 0:
        return 0.0
    return (num_bytes * 8 / seconds) / 1_000_000
