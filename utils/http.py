import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# Shared requests session with retries configured
def _create_session():
    s = requests.Session()
    retries = Retry(
        total=3,
        backoff_factor=0.5,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET", "POST"]
    )
    adapter = HTTPAdapter(max_retries=retries)
    s.mount("https://", adapter)
    s.mount("http://", adapter)
    return s

session = _create_session()


def get_json(url, params=None, timeout=10, headers=None):
    """GET a URL and return parsed JSON.

    `headers` is optional and lets callers send a custom User-Agent or other
    headers (some public APIs fingerprint default clients).
    """
    try:
        r = session.get(url, params=params, timeout=timeout, headers=headers)
        r.raise_for_status()
        return r.json()
    except Exception:
        raise
