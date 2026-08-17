"""Typefully v2 client.

Only the four things this project needs: resolve the social set, upload media,
create a scheduled draft, and read back X analytics. Publishing through Typefully
rather than the X API keeps posting inside a flat monthly fee (X charges per write,
and ~13x more for posts containing a URL) and gives us analytics for free.

Media upload is a three-step dance: ask for a presigned S3 URL, PUT the bytes with
no extra headers, then poll until the media is processed.
"""

from __future__ import annotations

import datetime as dt
import logging
import time
from pathlib import Path

import httpx

from ..config import settings
from ..models import STREAM_CARE, STREAM_ML, STREAM_OWN

log = logging.getLogger(__name__)

MEDIA_POLL_INTERVAL_S = 2.0
MEDIA_TIMEOUT_S = 90.0


class TypefullyError(RuntimeError):
    pass


def social_set_for(stream: str) -> str:
    """The X account a stream posts to.

    Each stream is pinned to an account it may never leave: the healthcare promos belong
    to the Alverna account and the ML posts to the personal one, and a draft whose account
    is not configured is refused rather than sent to whichever account happens to be first
    on the Typefully workspace.
    """
    care = settings.typefully_care_social_set_id or settings.typefully_social_set_id
    configured = {
        STREAM_CARE: care,
        STREAM_ML: settings.typefully_ml_social_set_id,
        # Material you hand in yourself goes out under your own name, like the ML posts.
        STREAM_OWN: settings.typefully_ml_social_set_id,
    }.get(stream)
    if not configured:
        raise TypefullyError(
            f"no Typefully social set configured for the {stream!r} stream; set "
            f"XSWARM_TYPEFULLY_{'CARE' if stream == STREAM_CARE else 'ML'}_SOCIAL_SET_ID"
        )
    return configured


def configured_social_sets() -> list[str]:
    """Every account this install posts to, deduplicated, in stream order."""
    found: list[str] = []
    for stream in (STREAM_CARE, STREAM_ML):
        try:
            social_set = social_set_for(stream)
        except TypefullyError:
            continue
        if social_set not in found:
            found.append(social_set)
    return found


class TypefullyClient:
    def __init__(
        self,
        api_key: str | None = None,
        social_set_id: str | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        self.api_key = api_key or settings.typefully_api_key
        if not self.api_key:
            raise TypefullyError("XSWARM_TYPEFULLY_API_KEY is not set")
        self._social_set_id = social_set_id or settings.typefully_social_set_id
        self._client = client or httpx.Client(base_url=settings.typefully_base_url, timeout=60)
        self._client.headers["Authorization"] = f"Bearer {self.api_key}"

    def _request(self, method: str, path: str, **kwargs) -> dict:
        response = self._client.request(method, path, **kwargs)
        if response.status_code >= 400:
            raise TypefullyError(
                f"{method} {path} -> {response.status_code}: {response.text[:300]}"
            )
        return response.json() if response.content else {}

    @property
    def social_set_id(self) -> str:
        """Never guessed: picking the first account on the workspace is how an ML post
        ends up on the healthcare account."""
        if not self._social_set_id:
            raise TypefullyError("no Typefully social set given; pass social_set_for(<stream>)")
        return self._social_set_id

    def upload_media(self, path: Path) -> str:
        created = self._request(
            "POST",
            f"/social-sets/{self.social_set_id}/media/upload",
            json={"file_name": path.name},
        )
        media_id = str(created["media_id"])
        # The presigned URL carries its own signature; adding headers invalidates it.
        with httpx.Client(timeout=120) as raw:
            put = raw.put(created["upload_url"], content=path.read_bytes())
        if put.status_code not in (200, 204):
            raise TypefullyError(f"media upload failed: {put.status_code}")
        self._await_media(media_id)
        return media_id

    def _await_media(self, media_id: str) -> None:
        deadline = time.monotonic() + MEDIA_TIMEOUT_S
        while time.monotonic() < deadline:
            status = self._request(
                "GET", f"/social-sets/{self.social_set_id}/media/{media_id}"
            ).get("status")
            if status == "ready":
                return
            if status == "failed":
                raise TypefullyError(f"media {media_id} failed processing")
            time.sleep(MEDIA_POLL_INTERVAL_S)
        raise TypefullyError(f"media {media_id} not ready after {MEDIA_TIMEOUT_S:.0f}s")

    def _draft_payload(
        self,
        posts: list[str],
        *,
        media_ids: list[str] | None = None,
        publish_at: dt.datetime | None = None,
        plan_only: bool = False,
        title: str = "",
    ) -> dict[str, object]:
        first: dict[str, object] = {"text": posts[0]}
        if media_ids:
            first["media_ids"] = media_ids
        payload: dict[str, object] = {
            "platforms": {
                "x": {"enabled": True, "posts": [first, *({"text": p} for p in posts[1:])]}
            }
        }
        if title:
            payload["draft_title"] = title
        if publish_at:
            key = "plan_at" if plan_only else "publish_at"
            payload[key] = publish_at.isoformat()
        return payload

    def create_draft(
        self,
        posts: list[str],
        *,
        media_ids: list[str] | None = None,
        publish_at: dt.datetime | None = None,
        plan_only: bool = False,
        title: str = "",
    ) -> dict:
        """`posts` is a thread: index 0 is the post, the rest are replies.

        `plan_only` puts the draft on the queue at its date but never auto-publishes —
        that is the human-in-the-loop mode we start in.
        """
        payload = self._draft_payload(
            posts, media_ids=media_ids, publish_at=publish_at, plan_only=plan_only, title=title
        )
        return self._request("POST", f"/social-sets/{self.social_set_id}/drafts", json=payload)

    def update_draft(
        self,
        draft_id: str,
        posts: list[str],
        *,
        media_ids: list[str] | None = None,
        publish_at: dt.datetime | None = None,
        plan_only: bool = False,
        title: str = "",
    ) -> dict:
        """Rewrite a draft already sitting on the queue, keeping its slot and its id.

        Editing beats delete-and-recreate: the post keeps its place in the queue and its
        provider id, so the metrics we later pull still line up with the same `Draft`.
        """
        payload = self._draft_payload(
            posts, media_ids=media_ids, publish_at=publish_at, plan_only=plan_only, title=title
        )
        return self._request(
            "PATCH", f"/social-sets/{self.social_set_id}/drafts/{draft_id}", json=payload
        )

    def analytics_posts(
        self, start_date: dt.date, end_date: dt.date, *, limit: int = 100
    ) -> list[dict]:
        results: list[dict] = []
        offset = 0
        while True:
            payload = self._request(
                "GET",
                f"/social-sets/{self.social_set_id}/analytics/x/posts",
                params={
                    "start_date": start_date.isoformat(),
                    "end_date": end_date.isoformat(),
                    "limit": limit,
                    "offset": offset,
                },
            )
            page = payload.get("results", [])
            results.extend(page)
            if not payload.get("next") or not page:
                return results
            offset += len(page)

    def close(self) -> None:
        self._client.close()
