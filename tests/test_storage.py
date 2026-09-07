from __future__ import annotations

import httpx
import pytest

from xswarm import storage
from xswarm.config import settings
from xswarm.models import Asset


@pytest.fixture()
def credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "supabase_url", "https://ref.supabase.co/")
    monkeypatch.setattr(settings, "supabase_service_key", "service-key")
    monkeypatch.setattr(settings, "supabase_bucket", "assets")


def _client(handler) -> object:
    transport = httpx.MockTransport(handler)

    class Client(httpx.Client):
        def __init__(self, **kwargs):
            super().__init__(transport=transport, **kwargs)

    return Client


def test_store_returns_a_public_url(tmp_path, monkeypatch, credentials):
    image = tmp_path / "draft-7-figure.png"
    image.write_bytes(b"\x89PNG stand-in")
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path.endswith("/storage/v1/bucket"):
            return httpx.Response(400, json={"message": "The resource already exists"})
        return httpx.Response(200, json={"Key": "assets/draft-7/draft-7-figure.png"})

    monkeypatch.setattr(httpx, "Client", _client(handler))
    url = storage.store(image, "draft-7/draft-7-figure.png")

    assert url == (
        "https://ref.supabase.co/storage/v1/object/public/assets/draft-7/draft-7-figure.png"
    )
    upload = seen[-1]
    assert upload.headers["authorization"] == "Bearer service-key"
    assert upload.headers["content-type"] == "image/png"
    assert upload.content == b"\x89PNG stand-in"


def test_no_credentials_means_no_upload_and_no_error(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "supabase_url", "")
    monkeypatch.setattr(settings, "supabase_service_key", "")
    image = tmp_path / "a.png"
    image.write_bytes(b"png")

    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover - never called
        raise AssertionError("uploaded without credentials")

    monkeypatch.setattr(httpx, "Client", _client(handler))
    assert storage.store(image, "a.png") == ""


def test_a_refused_upload_costs_the_run_nothing(tmp_path, monkeypatch, credentials):
    image = tmp_path / "a.png"
    image.write_bytes(b"png")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/storage/v1/bucket"):
            return httpx.Response(200, json={})
        return httpx.Response(403, json={"message": "invalid key"})

    monkeypatch.setattr(httpx, "Client", _client(handler))
    assert storage.store(image, "a.png") == ""


def test_a_missing_file_is_not_an_upload(tmp_path, monkeypatch, credentials):
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("uploaded a file that is not there")

    monkeypatch.setattr(httpx, "Client", _client(handler))
    assert storage.store(tmp_path / "gone.png", "gone.png") == ""


def test_publish_records_the_url_on_the_asset(tmp_path, monkeypatch, credentials):
    image = tmp_path / "draft-3-chart.png"
    image.write_bytes(b"png")
    asset = Asset(draft_id=3, kind="result_chart", path=str(image))

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={})

    monkeypatch.setattr(httpx, "Client", _client(handler))
    url = storage.publish(asset)

    assert url.endswith("/assets/draft-3/draft-3-chart.png")
    assert asset.url == url


def test_publish_leaves_an_already_uploaded_asset_alone(tmp_path, monkeypatch, credentials):
    asset = Asset(draft_id=3, kind="hero", path=str(tmp_path / "x.png"), url="https://kept")

    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("uploaded twice")

    monkeypatch.setattr(httpx, "Client", _client(handler))
    assert storage.publish(asset) == "https://kept"
