"""Client for the Bundesagentur für Arbeit job search API (verified in spike S1).

Search: GET /pc/v6/jobs (postings in `ergebnisliste`, total in `maxErgebnisse`)
Detail: GET /pc/v4/jobdetails/<base64(referenznummer)>
"""

import base64
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from typing import Any

BASE_URL = "https://rest.arbeitsagentur.de/jobboerse/jobsuche-service"
API_KEY = "jobboerse-jobsuche"  # public client ID documented by bundesAPI/jobsuche-api, not a secret
RETRYABLE_STATUS = {429, 500, 502, 503, 504}


class BAApiError(Exception):
    """The API failed after all retries or returned a non-retryable error."""


class BANotFound(BAApiError):
    """HTTP 404 – e.g. a posting removed between search and detail call."""


class BAClient:
    def __init__(
        self,
        user_agent: str,
        min_interval_s: float = 1.1,
        max_attempts: int = 4,
        timeout_s: float = 30.0,
        opener: Callable[..., Any] = urllib.request.urlopen,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.user_agent = user_agent
        self.min_interval_s = min_interval_s
        self.max_attempts = max_attempts
        self.timeout_s = timeout_s
        self._open = opener
        self._sleep = sleep
        self._clock = clock
        self._last_start: float | None = None
        self.requests_made = 0

    def search(self, term: str, page: int, size: int = 100) -> dict:
        return self._get("/pc/v6/jobs", {"was": term, "angebotsart": 1, "size": size, "page": page})

    def detail(self, refnr: str) -> dict:
        encoded = base64.b64encode(refnr.encode()).decode()
        return self._get(f"/pc/v4/jobdetails/{encoded}")

    def _throttle(self) -> None:
        # Spacing is measured between request *starts*, so a ~1 s response does not double the cost per call.
        if self._last_start is not None:
            wait = self.min_interval_s - (self._clock() - self._last_start)
            if wait > 0:
                self._sleep(wait)
        self._last_start = self._clock()

    def _get(self, path: str, params: dict | None = None) -> dict:
        url = BASE_URL + path + ("?" + urllib.parse.urlencode(params) if params else "")
        request = urllib.request.Request(
            url, headers={"X-API-Key": API_KEY, "User-Agent": self.user_agent, "Accept": "application/json"}
        )
        last_error: Exception | None = None
        for attempt in range(1, self.max_attempts + 1):
            self._throttle()
            self.requests_made += 1
            try:
                with self._open(request, timeout=self.timeout_s) as resp:
                    return json.loads(resp.read())
            except urllib.error.HTTPError as e:
                if e.code == 404:
                    raise BANotFound(f"404 for {path}") from e
                if e.code not in RETRYABLE_STATUS:
                    raise BAApiError(f"HTTP {e.code} for {path}") from e
                last_error = e
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
                last_error = e
            if attempt < self.max_attempts:
                self._sleep(2 ** (attempt - 1))  # 1, 2, 4 s
        raise BAApiError(f"{path} failed after {self.max_attempts} attempts: {last_error}")
