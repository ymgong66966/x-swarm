"""Somewhere an image can be looked at from a different machine.

Every visual is drawn onto the disk of whoever drew it, which for a scheduled run is a
runner that is deleted minutes later. Local review still reads `Asset.path`; the hosted
review UI and anyone reading the database later need a URL, so an upload to a public
Supabase bucket is mirrored onto `Asset.url` whenever the credentials are configured.
"""

from __future__ import annotations

import logging
import mimetypes
from pathlib import Path

import httpx

from .config import settings
from .models import Asset

log = logging.getLogger(__name__)

TIMEOUT = 60.0


def configured() -> bool:
    return bool(settings.supabase_url and settings.supabase_service_key)


def _headers() -> dict[str, str]:
    return {
        "authorization": f"Bearer {settings.supabase_service_key}",
        "apikey": settings.supabase_service_key,
    }


def _base() -> str:
    return settings.supabase_url.rstrip("/")


def ensure_bucket(client: httpx.Client) -> None:
    """Create the bucket if this is the first upload. A bucket that already exists comes
    back as a 400/409 naming conflict, which is the outcome we wanted anyway."""
    response = client.post(
        f"{_base()}/storage/v1/bucket",
        headers=_headers(),
        json={"name": settings.supabase_bucket, "id": settings.supabase_bucket, "public": True},
    )
    if response.status_code >= 400 and "exist" not in response.text.lower():
        response.raise_for_status()


def public_url(name: str) -> str:
    return f"{_base()}/storage/v1/object/public/{settings.supabase_bucket}/{name}"


def upload(path: Path, name: str) -> str:
    """Copy one file into the bucket and return the URL it can be read from."""
    content_type = mimetypes.guess_type(name)[0] or "application/octet-stream"
    with httpx.Client(timeout=TIMEOUT) as client:
        ensure_bucket(client)
        response = client.post(
            f"{_base()}/storage/v1/object/{settings.supabase_bucket}/{name}",
            headers={
                **_headers(),
                "content-type": content_type,
                # Re-rendering a draft reuses the filename, and the new picture is the one
                # the reviewer is looking at.
                "x-upsert": "true",
            },
            content=path.read_bytes(),
        )
        response.raise_for_status()
    return public_url(name)


def store(path: Path | str, name: str) -> str:
    """Upload if we can, and never let that failure cost the run its image.

    Returns the URL, or an empty string when storage is unconfigured, the file is gone, or
    Supabase refused it — the caller keeps its local path either way.
    """
    if not configured():
        return ""
    path = Path(path)
    if not path.is_file():
        return ""
    try:
        return upload(path, name)
    except Exception as error:  # a picture on one disk beats no picture at all
        log.warning("%s stayed local: %s", path.name, error)
        return ""


def publish(asset: Asset) -> str:
    """Give an asset a URL a browser somewhere else can open."""
    if asset.url:
        return asset.url
    name = f"draft-{asset.draft_id}/{Path(asset.path).name}"
    asset.url = store(asset.path, name)
    return asset.url
