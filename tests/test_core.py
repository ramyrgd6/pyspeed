from unittest.mock import Mock

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
