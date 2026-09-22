"""remote_config — git-aware URL normalization + guarded fetch for import."""

from __future__ import annotations

import pytest

from mdl_reverse.remote_config import (
    ImportError_,
    fetch_config_text,
    is_url,
    normalize_source,
)


def test_normalize_github_blob_to_raw():
    assert normalize_source(
        "https://github.com/acme/dbt/blob/main/rc/kimball.yaml"
    ) == "https://raw.githubusercontent.com/acme/dbt/main/rc/kimball.yaml"


def test_normalize_gitlab_blob_to_raw():
    assert normalize_source(
        "https://gitlab.com/acme/dbt/-/blob/main/rc.yaml"
    ) == "https://gitlab.com/acme/dbt/-/raw/main/rc.yaml"


def test_normalize_github_shorthand():
    assert normalize_source("github:acme/dbt/rc.yaml@v1") == (
        "https://raw.githubusercontent.com/acme/dbt/v1/rc.yaml"
    )
    # no ref -> HEAD
    assert normalize_source("github:acme/dbt/rc.yaml") == (
        "https://raw.githubusercontent.com/acme/dbt/HEAD/rc.yaml"
    )


def test_normalize_passthrough():
    raw = "https://raw.githubusercontent.com/a/b/main/x.yaml"
    assert normalize_source(raw) == raw
    assert normalize_source("./local.yaml") == "./local.yaml"


def test_is_url():
    assert is_url("https://x/y.yaml")
    assert is_url("github:o/r/x.yaml")
    assert not is_url("/local/path.yaml")
    assert not is_url("relative.yaml")


class _Resp:
    def __init__(self, text="reverse:\n  exclude: ['*_tmp']\n", ctype="text/plain", status=200):
        self._text = text
        self.headers = {"content-type": ctype}
        self.status_code = status

    @property
    def text(self):
        return self._text

    @property
    def content(self):
        return self._text.encode()

    def raise_for_status(self):
        import httpx

        if self.status_code >= 400:
            raise httpx.HTTPStatusError("err", request=None, response=self)


class _Client:
    def __init__(self, resp):
        self._resp = resp

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get(self, url):
        return self._resp


def test_fetch_ok(monkeypatch):
    import httpx

    monkeypatch.setattr(httpx, "Client", lambda **kw: _Client(_Resp()))
    text = fetch_config_text("https://x/rc.yaml")
    assert "exclude" in text


def test_fetch_rejects_http_without_flag():
    with pytest.raises(ImportError_, match="http"):
        fetch_config_text("http://x/rc.yaml")


def test_fetch_allows_http_with_flag(monkeypatch):
    import httpx

    monkeypatch.setattr(httpx, "Client", lambda **kw: _Client(_Resp()))
    assert "exclude" in fetch_config_text("http://x/rc.yaml", allow_insecure=True)


def test_fetch_rejects_html(monkeypatch):
    import httpx

    monkeypatch.setattr(
        httpx, "Client", lambda **kw: _Client(_Resp(text="<!DOCTYPE html><html>", ctype="text/html"))
    )
    with pytest.raises(ImportError_, match="HTML"):
        fetch_config_text("https://github.com/o/r/blob/main/x.yaml")


def test_fetch_sends_token(monkeypatch):
    import httpx

    seen = {}

    def _client(**kw):
        seen["headers"] = kw.get("headers")
        return _Client(_Resp())

    monkeypatch.setattr(httpx, "Client", _client)
    fetch_config_text("https://x/rc.yaml", token="abc123")
    assert seen["headers"]["Authorization"] == "Bearer abc123"
