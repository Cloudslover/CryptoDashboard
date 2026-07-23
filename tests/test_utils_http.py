import types
import pytest

import utils.http as http_mod


class FakeResponse:
    def __init__(self, data, status=200):
        self._data = data
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise Exception(f"HTTP {self.status_code}")

    def json(self):
        return self._data


def test_get_json_success(monkeypatch):
    # Replace session.get with a fake that returns known JSON
    def fake_get(url, params=None, timeout=None):
        return FakeResponse({"ok": True, "url": url})

    monkeypatch.setattr(http_mod, "session", types.SimpleNamespace(get=fake_get))
    res = http_mod.get_json("https://example.com/api", params={"q":1})
    assert isinstance(res, dict)
    assert res.get("ok") is True
    assert "example.com" in res.get("url")


def test_get_json_http_error(monkeypatch):
    def fake_get(url, params=None, timeout=None):
        return FakeResponse({"error": "not found"}, status=404)

    monkeypatch.setattr(http_mod, "session", types.SimpleNamespace(get=fake_get))
    with pytest.raises(Exception):
        http_mod.get_json("https://example.com/notfound")
