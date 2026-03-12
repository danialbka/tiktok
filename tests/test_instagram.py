from pathlib import Path
from urllib.parse import parse_qs

import httpx

from movie_shorts.config import Settings
from movie_shorts.instagram import InstagramPublisher


def test_publish_reel_from_path_uses_resumable_flow(tmp_path: Path) -> None:
    video_path = tmp_path / "short.mp4"
    video_path.write_bytes(b"demo-video")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path == "/v24.0/17841400000000000/media":
            payload = parse_qs(request.content.decode())
            assert payload["media_type"] == ["REELS"]
            assert payload["upload_type"] == ["resumable"]
            assert payload["caption"] == ["hello world"]
            return httpx.Response(
                200,
                json={
                    "id": "17890000000000000",
                    "uri": "https://rupload.facebook.com/ig-api-upload/v24.0/17890000000000000",
                },
            )

        if request.method == "POST" and request.url.host == "rupload.facebook.com":
            assert request.headers["authorization"] == "OAuth token-123"
            assert request.headers["offset"] == "0"
            assert request.headers["file_size"] == str(video_path.stat().st_size)
            assert request.content == b"demo-video"
            return httpx.Response(200, json={"success": True})

        if request.method == "GET" and request.url.path == "/v24.0/17890000000000000":
            return httpx.Response(
                200,
                json={
                    "id": "17890000000000000",
                    "status_code": "FINISHED",
                    "status": "Upload complete",
                },
            )

        if request.method == "POST" and request.url.path == "/v24.0/17841400000000000/media_publish":
            payload = parse_qs(request.content.decode())
            assert payload["creation_id"] == ["17890000000000000"]
            return httpx.Response(200, json={"id": "18070000000000000"})

        if request.method == "GET" and request.url.path == "/v24.0/18070000000000000":
            return httpx.Response(
                200,
                json={
                    "id": "18070000000000000",
                    "permalink": "https://www.instagram.com/reel/abc123/",
                    "media_type": "VIDEO",
                    "media_product_type": "REELS",
                },
            )

        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    publisher = InstagramPublisher(
        access_token="token-123",
        instagram_user_id="17841400000000000",
        graph_api_version="v24.0",
        transport=httpx.MockTransport(handler),
    )

    try:
        result = publisher.publish_reel_from_path(video_path, caption="hello world", poll_interval_seconds=0.01, timeout_seconds=1)
    finally:
        publisher.close()

    assert result.creation_id == "17890000000000000"
    assert result.media_id == "18070000000000000"
    assert result.permalink == "https://www.instagram.com/reel/abc123/"
    assert result.status_code == "FINISHED"


def test_get_account_falls_back_when_username_field_is_deprecated() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v24.0/17841400000000000":
            fields = request.url.params.get("fields")
            if fields == "id,username":
                return httpx.Response(
                    400,
                    json={
                        "error": {
                            "message": "(#12) cannot_access_user_username_field is deprecated for versions v2.0 and higherusername field is deprecated for versions v2.0 and higher"
                        }
                    },
                )
            if fields == "id":
                return httpx.Response(200, json={"id": "17841400000000000"})
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    publisher = InstagramPublisher(
        access_token="token-123",
        instagram_user_id="17841400000000000",
        graph_api_version="v24.0",
        transport=httpx.MockTransport(handler),
    )

    try:
        account = publisher.get_account()
    finally:
        publisher.close()

    assert account == {"id": "17841400000000000"}


def test_resolve_rendered_video_path_prefers_primary_crop_output(tmp_path: Path) -> None:
    artifact_dir = tmp_path / "artifacts"
    job_dir = artifact_dir / "18"
    job_dir.mkdir(parents=True)
    primary = job_dir / "short.mp4"
    primary.write_bytes(b"crop")

    resolved = InstagramPublisher.resolve_rendered_video_path(artifact_dir=artifact_dir, job_id=18, variant=1, render_mode="crop")

    assert resolved == primary


def test_resolve_rendered_video_path_uses_fit_variant_directory(tmp_path: Path) -> None:
    artifact_dir = tmp_path / "artifacts"
    variant_path = artifact_dir / "18" / "variants_fit" / "short_03.mp4"
    variant_path.parent.mkdir(parents=True)
    variant_path.write_bytes(b"fit")

    resolved = InstagramPublisher.resolve_rendered_video_path(artifact_dir=artifact_dir, job_id=18, variant=3, render_mode="fit")

    assert resolved == variant_path


def test_settings_can_load_for_instagram_without_real_debrid_key(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("REAL_DEBRID_API_KEY", raising=False)
    monkeypatch.setenv("INSTAGRAM_ACCESS_TOKEN", "token-123")
    monkeypatch.setenv("INSTAGRAM_USER_ID", "17841400000000000")

    settings = Settings.load(root=tmp_path, require_real_debrid=False)

    assert settings.real_debrid_api_key == ""
    assert settings.instagram_access_token == "token-123"
    assert settings.instagram_user_id == "17841400000000000"
