from unittest.mock import Mock
import time

import pytest

from pyspeed import core


def test_bytes_to_mbps_uses_decimal_megabits():
    assert core.bytes_to_mbps(1_000_000, 1) == 8.0


def test_bytes_to_mbps_returns_zero_for_non_positive_duration():
    assert core.bytes_to_mbps(1_000_000, 0) == 0.0


@pytest.mark.parametrize(
    "factory",
    [
        lambda: core.measure_ping(samples=0),
        lambda: list(core.measure_download(total_bytes=0)),
        lambda: list(core.measure_upload(total_bytes=0)),
    ],
)
def test_measurements_reject_invalid_sizes(factory):
    with pytest.raises(ValueError):
        factory()


def test_measure_ping_ignores_failed_http_samples(monkeypatch):
    failed = Mock()
    failed.raise_for_status.side_effect = core.requests.HTTPError("503")
    successful = Mock()
    session = Mock()
    session.get.side_effect = [failed, successful]
    monkeypatch.setattr(core, "_session", lambda: session)

    result = core.measure_ping(samples=2)

    assert len(result.samples_ms) == 1
    assert session.get.call_count == 2


def test_measure_download_raises_for_http_error(monkeypatch):
    response = Mock()
    response.__enter__ = Mock(return_value=response)
    response.__exit__ = Mock(return_value=False)
    response.raise_for_status.side_effect = core.requests.HTTPError("503")
    session = Mock()
    session.get.return_value = response
    monkeypatch.setattr(core, "_session", lambda: session)

    with pytest.raises(core.requests.HTTPError):
        list(core.measure_download(total_bytes=1))


def test_measure_download_rejects_incomplete_response(monkeypatch):
    response = Mock()
    response.__enter__ = Mock(return_value=response)
    response.__exit__ = Mock(return_value=False)
    response.iter_content.return_value = [b"short"]
    session = Mock()
    session.get.return_value = response
    monkeypatch.setattr(core, "_session", lambda: session)

    with pytest.raises(ConnectionError, match="Download incomplete"):
        list(core.measure_download(total_bytes=10))


def test_measure_upload_uses_fixed_length_stream(monkeypatch):
    response = Mock()
    session = Mock()

    def post(_url, data, **_kwargs):
        uploaded = 0
        while chunk := data.read(4):
            uploaded += len(chunk)
        assert uploaded == 10
        return response

    session.post.side_effect = post
    monkeypatch.setattr(core, "_session", lambda: session)

    samples = list(core.measure_upload(total_bytes=10))

    assert samples[-1][1] == 10


def test_session_stats_tracks_totals_averages_and_peaks():
    stats = core.SessionStats()
    stats.record_transfer("download", 1_000_000)
    stats.record_transfer("upload", 500_000)

    snapshot = stats.snapshot()

    assert snapshot.downloaded_bytes == 1_000_000
    assert snapshot.uploaded_bytes == 500_000
    assert snapshot.average_download_mbps >= 0
    assert snapshot.average_upload_mbps >= 0
    assert snapshot.peak_download_mbps >= 0
    assert snapshot.peak_upload_mbps >= 0


def test_network_runner_starts_workers_and_stops_them():
    stats = core.SessionStats()
    runner = core.NetworkRunner(stats, 1, 1, timeout=1)
    started = []

    def worker():
        started.append(True)
        runner.stop_event.wait()

    runner._download_worker = worker
    runner._upload_worker = worker
    runner._ping_worker = worker
    runner.start()

    deadline = time.monotonic() + 1
    while len(started) < 3 and time.monotonic() < deadline:
        time.sleep(0.01)
    runner.stop()
    runner.join(timeout=1)

    assert len(started) == 3
    assert all(not thread.is_alive() for thread in runner._threads)


def test_network_runner_retries_after_temporary_failure():
    stats = core.SessionStats()
    runner = core.NetworkRunner(stats, 1, 1, timeout=1)
    attempts = []

    def attempt():
        attempts.append(True)
        if len(attempts) == 1:
            raise core.requests.ConnectionError("temporary")
        runner.stop_event.set()

    runner._run_with_retry("download", attempt)

    assert len(attempts) == 2
    assert "download retry" in (stats.snapshot().last_error or "")
