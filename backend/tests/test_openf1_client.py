"""
Integration tests for OpenF1 API client.
"""
import pytest
from app.openf1_client import OpenF1Client, OpenF1APIError


async def _no_wait(seconds):
    """Backoff is real in the client; the tests don't need to sit through it."""


@pytest.fixture
def client(monkeypatch):
    """A client whose HTTP layer is replaced by canned endpoint payloads."""
    instance = OpenF1Client()
    instance.requests = []
    payloads = {}

    async def fake_request(endpoint, params=None):
        instance.requests.append((endpoint, params))
        return payloads.get(endpoint, [])

    monkeypatch.setattr(instance, "_request", fake_request)
    instance.payloads = payloads
    return instance


@pytest.mark.asyncio
async def test_intervals_hit_the_intervals_endpoint(client):
    """Gaps live on /intervals - /position does not carry them."""
    await client.get_intervals(9999)

    assert client.requests == [("intervals", {"session_key": 9999})]


@pytest.mark.asyncio
async def test_latest_intervals_keeps_newest_row_per_driver(client):
    client.payloads["intervals"] = [
        {"driver_number": 44, "interval": 0.9, "date": "2026-03-05T14:30:00Z"},
        {"driver_number": 1, "interval": None, "date": "2026-03-05T14:30:00Z"},
        {"driver_number": 44, "interval": 0.4, "date": "2026-03-05T14:30:04Z"},
        # Out of order on purpose - ordering must not be assumed
        {"driver_number": 44, "interval": 0.7, "date": "2026-03-05T14:30:02Z"},
    ]

    rows = await client.get_latest_intervals(9999)
    by_driver = {r["driver_number"]: r for r in rows}

    assert len(rows) == 2
    assert by_driver[44]["interval"] == 0.4


@pytest.mark.asyncio
async def test_latest_intervals_skips_rows_without_driver_number(client):
    client.payloads["intervals"] = [
        {"interval": 0.5, "date": "2026-03-05T14:30:00Z"},
        {"driver_number": 44, "interval": 0.5, "date": "2026-03-05T14:30:00Z"},
    ]

    assert len(await client.get_latest_intervals(9999)) == 1


@pytest.mark.asyncio
async def test_latest_laps_returns_last_completed_laps_oldest_first(client):
    client.payloads["laps"] = [
        {"driver_number": 44, "lap_number": 9, "lap_duration": 91.0},
        {"driver_number": 44, "lap_number": 10, "lap_duration": 90.5},
        {"driver_number": 44, "lap_number": 11, "lap_duration": 89.9},
        # The lap in progress has no duration yet
        {"driver_number": 44, "lap_number": 12, "lap_duration": None},
        {"driver_number": 1, "lap_number": 11, "lap_duration": 90.1},
    ]

    laps = await client.get_latest_laps(9999, count=2)

    assert [lap["lap_duration"] for lap in laps[44]] == [90.5, 89.9]
    assert [lap["lap_duration"] for lap in laps[1]] == [90.1]


@pytest.mark.asyncio
async def test_latest_positions_ignores_response_ordering(client):
    client.payloads["position"] = [
        {"driver_number": 44, "position": 3, "date": "2026-03-05T14:30:05Z"},
        {"driver_number": 44, "position": 2, "date": "2026-03-05T14:30:01Z"},
    ]

    positions = await client.get_latest_positions(9999)

    assert positions == [{"driver_number": 44, "position": 3, "date": "2026-03-05T14:30:05Z"}]


@pytest.mark.asyncio
async def test_missing_data_handling(client):
    """Empty payloads flow through as empty lists, not exceptions."""
    assert await client.get_latest_intervals(9999) == []
    assert await client.get_latest_laps(9999) == {}
    assert await client.get_latest_positions(9999) == []


@pytest.mark.asyncio
async def test_openf1_client_retry_logic(monkeypatch):
    """Test that client retries on failures."""
    import httpx

    instance = OpenF1Client(sleep=_no_wait)
    attempts = []

    async def failing_get(url, params=None):
        attempts.append(url)
        raise httpx.RequestError("connection reset")

    monkeypatch.setattr(instance.client, "get", failing_get)

    with pytest.raises(OpenF1APIError):
        await instance.get_intervals(9999)

    assert len(attempts) == instance.max_retries


@pytest.mark.asyncio
async def test_a_404_is_not_retried(monkeypatch):
    """
    `starting_grid` genuinely 404s for some sessions. Asking twice more
    cannot change the answer; it just costs OpenF1 two more requests.
    """
    import httpx

    instance = OpenF1Client(sleep=_no_wait)
    attempts = []

    async def not_found(url, params=None):
        attempts.append(url)
        request = httpx.Request("GET", url)
        return httpx.Response(404, request=request)

    monkeypatch.setattr(instance.client, "get", not_found)

    with pytest.raises(OpenF1APIError, match="HTTP 404"):
        await instance.get_endpoint("starting_grid", {"session_key": 9928})

    assert len(attempts) == 1


@pytest.mark.asyncio
async def test_rate_limiting_is_retried(monkeypatch):
    """429 is a "not yet", which is what backoff is for."""
    import httpx

    instance = OpenF1Client(sleep=_no_wait)
    attempts = []

    async def too_many(url, params=None):
        attempts.append(url)
        request = httpx.Request("GET", url)
        return httpx.Response(429, request=request)

    monkeypatch.setattr(instance.client, "get", too_many)

    with pytest.raises(OpenF1APIError, match="HTTP 429"):
        await instance.get_endpoint("intervals", {"session_key": 9928})

    assert len(attempts) == instance.max_retries


@pytest.mark.asyncio
async def test_a_server_error_is_retried(monkeypatch):
    import httpx

    instance = OpenF1Client(sleep=_no_wait)
    attempts = []

    async def broken(url, params=None):
        attempts.append(url)
        request = httpx.Request("GET", url)
        return httpx.Response(503, request=request)

    monkeypatch.setattr(instance.client, "get", broken)

    with pytest.raises(OpenF1APIError, match="HTTP 503"):
        await instance.get_endpoint("laps", {"session_key": 9928})

    assert len(attempts) == instance.max_retries


@pytest.mark.asyncio
async def test_retries_back_off_instead_of_hammering(monkeypatch):
    """Retrying instantly turns one rate-limit response into three."""
    import httpx

    waits = []

    async def record(seconds):
        waits.append(seconds)

    instance = OpenF1Client(sleep=record)

    async def broken(url, params=None):
        return httpx.Response(503, request=httpx.Request("GET", url))

    monkeypatch.setattr(instance.client, "get", broken)

    with pytest.raises(OpenF1APIError):
        await instance.get_endpoint("intervals", {"session_key": 9928})

    base = instance.retry_base_s
    # A wait between each pair of attempts, doubling, and none after the last.
    assert waits == [base * 2 ** i for i in range(instance.max_retries - 1)]


@pytest.mark.asyncio
async def test_a_429_waits_as_long_as_it_is_told(monkeypatch):
    import httpx

    waits = []

    async def record(seconds):
        waits.append(seconds)

    instance = OpenF1Client(sleep=record)
    calls = []

    async def limited_then_fine(url, params=None):
        calls.append(url)
        request = httpx.Request("GET", url)
        if len(calls) == 1:
            return httpx.Response(429, headers={"Retry-After": "7"}, request=request)
        return httpx.Response(200, json=[{"ok": True}], request=request)

    monkeypatch.setattr(instance.client, "get", limited_then_fine)

    assert await instance.get_endpoint("laps", {"session_key": 9928}) == [{"ok": True}]
    assert waits == [7.0]


def test_an_absurd_retry_after_is_capped():
    import httpx
    from app.openf1_client import MAX_RETRY_AFTER_S

    instance = OpenF1Client(sleep=_no_wait)
    response = httpx.Response(429, headers={"Retry-After": "86400"})

    assert instance._retry_delay(0, response) == MAX_RETRY_AFTER_S



def test_the_http_client_is_not_built_until_it_is_needed():
    """Replay and mock never call OpenF1; they should not pay for a TLS context."""
    instance = OpenF1Client(sleep=_no_wait)

    assert instance._client is None
    assert instance.client is instance.client  # built once, then reused
