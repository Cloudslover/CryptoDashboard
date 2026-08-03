import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# Default retry budget for the shared session. Callers that hit slow/unreliable
# third-party APIs (mempool.space, Yahoo, Polymarket, RSS feeds) should pass a
# smaller `retries=` so a dead host cannot stall the dashboard for minutes.
DEFAULT_RETRIES = 3


def _create_session(retries=DEFAULT_RETRIES):
    s = requests.Session()
    r = Retry(
        total=retries,
        backoff_factor=0.5,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET", "POST"]
    )
    adapter = HTTPAdapter(max_retries=r)
    s.mount("https://", adapter)
    s.mount("http://", adapter)
    return s


session = _create_session()


def get_json(url, params=None, timeout=10, headers=None, retries=None):
    """GET a URL and return parsed JSON.

    `headers` is optional and lets callers send a custom User-Agent or other
    headers (some public APIs fingerprint default clients).
    `retries` overrides the shared session's retry budget for this call only
    (0 = single attempt, no retries).
    """
    s = session if retries is None else _create_session(retries)
    try:
        r = s.get(url, params=params, timeout=timeout, headers=headers)
        r.raise_for_status()
        return r.json()
    except Exception:
        raise


def get_text(url, params=None, timeout=10, headers=None, retries=0):
    """GET a URL and return the raw text body (for XML/RSS etc.).

    Defaults to a single attempt: feeds are refreshed on a schedule and a dead
    publisher should fail fast, not burn minutes on retries.
    """
    s = session if retries is None else _create_session(retries)
    try:
        r = s.get(url, params=params, timeout=timeout, headers=headers)
        r.raise_for_status()
        return r.text
    except Exception:
        raise
