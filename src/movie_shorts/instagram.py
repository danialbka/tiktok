from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import time

import httpx


GRAPH_BASE_URL = "https://graph.facebook.com"
RUPLOAD_BASE_URL = "https://rupload.facebook.com/ig-api-upload"


@dataclass(slots=True)
class InstagramPublishResult:
    creation_id: str
    media_id: str
    permalink: str | None
    status_code: str | None
    status: str | None


class InstagramPublisher:
    def __init__(
        self,
        access_token: str,
        instagram_user_id: str,
        graph_api_version: str = "v24.0",
        timeout_seconds: float = 60.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.access_token = access_token
        self.instagram_user_id = instagram_user_id
        self.graph_api_version = graph_api_version.lstrip("v")
        self._client = httpx.Client(timeout=timeout_seconds, follow_redirects=True, transport=transport)

    def close(self) -> None:
        self._client.close()

    def get_account(self) -> dict:
        params = {
            "fields": "id,username",
            "access_token": self.access_token,
        }
        response = self._client.get(self._graph_url(self.instagram_user_id), params=params)
        if response.is_success:
            return response.json()

        payload = response.json()
        message = str(payload.get("error", {}).get("message") or "")
        if "username field is deprecated" not in message:
            response.raise_for_status()

        fallback = self._client.get(
            self._graph_url(self.instagram_user_id),
            params={
                "fields": "id",
                "access_token": self.access_token,
            },
        )
        fallback.raise_for_status()
        return fallback.json()

    def create_reel_container_from_url(
        self,
        video_url: str,
        caption: str = "",
        share_to_feed: bool = True,
        thumb_offset_ms: int | None = None,
    ) -> str:
        payload = self._media_payload(caption=caption, share_to_feed=share_to_feed, thumb_offset_ms=thumb_offset_ms)
        payload["video_url"] = video_url
        response = self._client.post(self._graph_url(f"{self.instagram_user_id}/media"), data=payload)
        response.raise_for_status()
        return response.json()["id"]

    def create_reel_container_resumable(
        self,
        caption: str = "",
        share_to_feed: bool = True,
        thumb_offset_ms: int | None = None,
    ) -> tuple[str, str]:
        payload = self._media_payload(caption=caption, share_to_feed=share_to_feed, thumb_offset_ms=thumb_offset_ms)
        payload["upload_type"] = "resumable"
        response = self._client.post(self._graph_url(f"{self.instagram_user_id}/media"), data=payload)
        response.raise_for_status()
        data = response.json()
        creation_id = data["id"]
        upload_url = data.get("uri") or f"{RUPLOAD_BASE_URL}/v{self.graph_api_version}/{creation_id}"
        return creation_id, upload_url

    def upload_video(self, upload_url: str, video_path: Path) -> None:
        file_size = video_path.stat().st_size
        with video_path.open("rb") as handle:
            response = self._client.post(
                upload_url,
                headers={
                    "Authorization": f"OAuth {self.access_token}",
                    "offset": "0",
                    "file_size": str(file_size),
                },
                content=handle.read(),
            )
        response.raise_for_status()

    def get_container_status(self, creation_id: str) -> dict:
        response = self._client.get(
            self._graph_url(creation_id),
            params={
                "fields": "id,status_code,status",
                "access_token": self.access_token,
            },
        )
        response.raise_for_status()
        return response.json()

    def wait_for_container(
        self,
        creation_id: str,
        timeout_seconds: float = 900.0,
        poll_interval_seconds: float = 10.0,
    ) -> dict:
        deadline = time.time() + timeout_seconds
        last_payload: dict | None = None
        while time.time() < deadline:
            payload = self.get_container_status(creation_id)
            last_payload = payload
            status_code = str(payload.get("status_code") or "").upper()
            if status_code in {"FINISHED", "PUBLISHED"}:
                return payload
            if status_code in {"ERROR", "EXPIRED"}:
                raise RuntimeError(f"Instagram container {creation_id} failed with status {status_code}: {payload.get('status') or 'unknown'}")
            time.sleep(poll_interval_seconds)
        raise RuntimeError(
            f"Timed out waiting for Instagram container {creation_id}. Last status: {(last_payload or {}).get('status_code', 'unknown')}"
        )

    def publish_container(self, creation_id: str) -> str:
        response = self._client.post(
            self._graph_url(f"{self.instagram_user_id}/media_publish"),
            data={
                "creation_id": creation_id,
                "access_token": self.access_token,
            },
        )
        response.raise_for_status()
        return response.json()["id"]

    def get_media(self, media_id: str) -> dict:
        response = self._client.get(
            self._graph_url(media_id),
            params={
                "fields": "id,media_product_type,media_type,permalink,status_code,status",
                "access_token": self.access_token,
            },
        )
        response.raise_for_status()
        return response.json()

    def publish_reel_from_path(
        self,
        video_path: Path,
        caption: str = "",
        share_to_feed: bool = True,
        thumb_offset_ms: int | None = None,
        timeout_seconds: float = 900.0,
        poll_interval_seconds: float = 10.0,
    ) -> InstagramPublishResult:
        creation_id, upload_url = self.create_reel_container_resumable(
            caption=caption,
            share_to_feed=share_to_feed,
            thumb_offset_ms=thumb_offset_ms,
        )
        self.upload_video(upload_url, video_path)
        container_status = self.wait_for_container(
            creation_id,
            timeout_seconds=timeout_seconds,
            poll_interval_seconds=poll_interval_seconds,
        )
        media_id = self.publish_container(creation_id)
        media = self.get_media(media_id)
        return InstagramPublishResult(
            creation_id=creation_id,
            media_id=media_id,
            permalink=media.get("permalink"),
            status_code=container_status.get("status_code"),
            status=container_status.get("status"),
        )

    def publish_reel_from_url(
        self,
        video_url: str,
        caption: str = "",
        share_to_feed: bool = True,
        thumb_offset_ms: int | None = None,
        timeout_seconds: float = 900.0,
        poll_interval_seconds: float = 10.0,
    ) -> InstagramPublishResult:
        creation_id = self.create_reel_container_from_url(
            video_url=video_url,
            caption=caption,
            share_to_feed=share_to_feed,
            thumb_offset_ms=thumb_offset_ms,
        )
        container_status = self.wait_for_container(
            creation_id,
            timeout_seconds=timeout_seconds,
            poll_interval_seconds=poll_interval_seconds,
        )
        media_id = self.publish_container(creation_id)
        media = self.get_media(media_id)
        return InstagramPublishResult(
            creation_id=creation_id,
            media_id=media_id,
            permalink=media.get("permalink"),
            status_code=container_status.get("status_code"),
            status=container_status.get("status"),
        )

    @staticmethod
    def resolve_rendered_video_path(artifact_dir: Path, job_id: int, variant: int = 1, render_mode: str = "crop") -> Path:
        if variant < 1:
            raise ValueError("Variant numbers start at 1.")

        job_dir = artifact_dir / str(job_id)
        if render_mode == "crop":
            variants_dir = job_dir / "variants"
            primary_path = job_dir / "short.mp4"
        else:
            suffix = render_mode.lower()
            variants_dir = job_dir / f"variants_{suffix}"
            primary_path = job_dir / f"short_{suffix}.mp4"

        variant_path = variants_dir / f"short_{variant:02d}.mp4"
        if variant == 1 and primary_path.exists():
            return primary_path
        return variant_path

    def _graph_url(self, path: str) -> str:
        return f"{GRAPH_BASE_URL}/v{self.graph_api_version}/{path.lstrip('/')}"

    def _media_payload(self, caption: str, share_to_feed: bool, thumb_offset_ms: int | None) -> dict[str, str]:
        payload = {
            "media_type": "REELS",
            "caption": caption,
            "share_to_feed": "true" if share_to_feed else "false",
            "access_token": self.access_token,
        }
        if thumb_offset_ms is not None:
            payload["thumb_offset"] = str(thumb_offset_ms)
        return payload
