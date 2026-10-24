import base64
import io
import json
import urllib.error

import pytest

from ingest.ba_client import BAApiError, BAClient, BANotFound


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def clock(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def http_error(code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError("https://x", code, "err", {}, None)


def make_client(responses, clock=None, call_seconds=0.0):
    """responses: list of dicts (returned as JSON) or exceptions (raised), consumed in order."""
    clock = clock or FakeClock()
    requests = []

    def opener(request, timeout):
        requests.append(request)
        clock.now += call_seconds
        item = responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return FakeResponse(json.dumps(item).encode())

    client = BAClient("test-agent", opener=opener, sleep=clock.sleep, clock=clock.clock)
    return client, requests, clock


def test_search_builds_v6_request_with_headers():
    client, requests, _ = make_client([{"ergebnisliste": [], "maxErgebnisse": 0}])
    client.search("Data Engineer", page=2)
    url = requests[0].full_url
    assert "/pc/v6/jobs?" in url
    for part in ("was=Data+Engineer", "angebotsart=1", "size=100", "page=2"):
        assert part in url
    assert requests[0].get_header("X-api-key") == "jobboerse-jobsuche"
    assert requests[0].get_header("User-agent") == "test-agent"


def test_detail_base64_encodes_reference():
    client, requests, _ = make_client([{"referenznummer": "10001-1002716922-S"}])
    client.detail("10001-1002716922-S")
    expected = base64.b64encode(b"10001-1002716922-S").decode()
    assert requests[0].full_url.endswith(f"/pc/v4/jobdetails/{expected}")
    assert expected == "MTAwMDEtMTAwMjcxNjkyMi1T"  # example from the community docs


def test_throttle_is_measured_between_request_starts():
    # Each call takes 0.8 s; with a 1.1 s start-to-start interval only 0.3 s of waiting is needed.
    client, _, clock = make_client([{}, {}], call_seconds=0.8)
    client.search("x", 1)
    client.search("x", 2)
    assert clock.sleeps == [pytest.approx(0.3)]


def test_retries_retryable_status_then_succeeds():
    client, requests, clock = make_client([http_error(503), http_error(429), {"ok": True}])
    assert client.search("x", 1) == {"ok": True}
    assert len(requests) == 3
    assert 1 in clock.sleeps  # exponential backoff
    assert 2 in clock.sleeps


def test_gives_up_after_max_attempts():
    client, requests, _ = make_client([http_error(500)] * 4)
    with pytest.raises(BAApiError):
        client.search("x", 1)
    assert len(requests) == 4


def test_404_raises_not_found_without_retry():
    client, requests, _ = make_client([http_error(404)])
    with pytest.raises(BANotFound):
        client.detail("gone")
    assert len(requests) == 1


def test_403_is_not_retried():
    client, requests, _ = make_client([http_error(403)])
    with pytest.raises(BAApiError):
        client.search("x", 1)
    assert len(requests) == 1


def test_network_error_is_retried():
    client, requests, _ = make_client([urllib.error.URLError("reset"), {"ok": 1}])
    assert client.search("x", 1) == {"ok": 1}
    assert len(requests) == 2
