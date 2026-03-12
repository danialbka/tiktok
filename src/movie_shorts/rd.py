from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import time
from urllib.parse import quote

import httpx

from .models import DownloadItem


YEAR_RE = re.compile(r"^(19\d{2}|20\d{2})$")
NOISE_TOKENS = {
    "2160p",
    "1080p",
    "720p",
    "480p",
    "4k",
    "web",
    "webrip",
    "webdl",
    "web-dl",
    "bluray",
    "brrip",
    "dvdrip",
    "hdrip",
    "x264",
    "x265",
    "h264",
    "h265",
    "hevc",
    "10bit",
    "8bit",
    "aac",
    "aac5",
    "aac51",
    "ddp5",
    "ddp51",
    "yts",
    "ytsbz",
    "proper",
    "repack",
    "extended",
    "remastered",
    "dubbed",
    "subbed",
}


@dataclass(slots=True)
class TorrentFile:
    id: int
    path: str
    bytes: int
    selected: bool

    @property
    def filename(self) -> str:
        return Path(self.path).name


@dataclass(slots=True)
class TorrentInfo:
    id: str
    filename: str
    original_filename: str
    hash: str
    status: str
    links: list[str]
    files: list[TorrentFile]

    @property
    def selected_files(self) -> list[TorrentFile]:
        return [item for item in self.files if item.selected]

    @property
    def primary_video_file(self) -> TorrentFile | None:
        for item in self.files:
            if Path(item.path).suffix.lower() in {".mkv", ".mp4", ".avi", ".mov", ".m4v", ".wmv", ".webm"}:
                return item
        return None


class RealDebridClient:
    BASE_URL = "https://api.real-debrid.com/rest/1.0"

    def __init__(self, api_key: str, timeout_seconds: float = 60.0) -> None:
        self._client = httpx.Client(
            base_url=self.BASE_URL,
            timeout=timeout_seconds,
            headers={"Authorization": f"Bearer {api_key}"},
        )

    def close(self) -> None:
        self._client.close()

    def get_user(self) -> dict:
        response = self._client.get("/user")
        response.raise_for_status()
        return response.json()

    def list_downloads(self, limit: int = 100, page: int = 1) -> list[DownloadItem]:
        response = self._client.get("/downloads", params={"limit": limit, "page": page})
        response.raise_for_status()
        items: list[DownloadItem] = []
        for payload in response.json():
            item = DownloadItem(
                id=payload["id"],
                filename=payload.get("filename") or "unknown",
                filesize=int(payload.get("filesize") or 0),
                download_url=payload["download"],
                link=payload["link"],
                mime_type=payload.get("mimeType"),
                generated_at=payload.get("generated"),
            )
            if item.is_video:
                items.append(item)
        return items

    def download_file(self, url: str, destination: Path) -> Path:
        destination.parent.mkdir(parents=True, exist_ok=True)
        with self._client.stream("GET", url, follow_redirects=True) as response:
            response.raise_for_status()
            with destination.open("wb") as handle:
                for chunk in response.iter_bytes():
                    handle.write(chunk)
        return destination

    def list_torrents(self, limit: int = 100, page: int = 1) -> list[TorrentInfo]:
        response = self._client.get("/torrents", params={"limit": limit, "page": page})
        response.raise_for_status()
        return [self._torrent_from_payload(payload) for payload in response.json()]

    def get_torrent_info(self, torrent_id: str) -> TorrentInfo:
        response = self._client.get(f"/torrents/info/{torrent_id}")
        response.raise_for_status()
        return self._torrent_from_payload(response.json())

    def find_torrent_by_link(self, link_url: str, limit: int = 100, pages: int = 5) -> TorrentInfo | None:
        for page in range(1, pages + 1):
            for torrent in self.list_torrents(limit=limit, page=page):
                if link_url in torrent.links:
                    return self.get_torrent_info(torrent.id)
        return None

    def add_magnet(self, magnet: str) -> str:
        response = self._client.post("/torrents/addMagnet", data={"magnet": magnet})
        response.raise_for_status()
        return response.json()["id"]

    def select_files(self, torrent_id: str, file_ids: list[int]) -> None:
        files = ",".join(str(item) for item in file_ids)
        response = self._client.post(f"/torrents/selectFiles/{torrent_id}", data={"files": files})
        response.raise_for_status()

    def delete_torrent(self, torrent_id: str) -> None:
        response = self._client.delete(f"/torrents/delete/{torrent_id}")
        response.raise_for_status()

    def wait_for_torrent(self, torrent_id: str, timeout_seconds: float = 30.0, poll_interval_seconds: float = 1.0) -> TorrentInfo:
        deadline = time.time() + timeout_seconds
        last_info: TorrentInfo | None = None
        while time.time() < deadline:
            info = self.get_torrent_info(torrent_id)
            last_info = info
            if info.status == "downloaded":
                return info
            if info.status in {"error", "virus", "dead"}:
                raise RuntimeError(f"Torrent {torrent_id} failed with status {info.status}.")
            time.sleep(poll_interval_seconds)
        raise RuntimeError(f"Timed out waiting for torrent {torrent_id} to finish. Last status: {last_info.status if last_info else 'unknown'}")

    def unrestrict_link(self, link: str) -> DownloadItem:
        response = self._client.post("/unrestrict/link", data={"link": link})
        response.raise_for_status()
        payload = response.json()
        return DownloadItem(
            id=payload["id"],
            filename=payload.get("filename") or "unknown",
            filesize=int(payload.get("filesize") or 0),
            download_url=payload["download"],
            link=payload["link"],
            mime_type=payload.get("mimeType"),
        )

    def build_magnet_link(self, torrent: TorrentInfo) -> str:
        return f"magnet:?xt=urn:btih:{torrent.hash}&dn={quote(torrent.filename)}"

    @staticmethod
    def infer_metadata(filename: str) -> dict[str, str | int | None]:
        stem = Path(filename).stem
        tokens = [token for token in re.split(r"[\s._()\-\[\]]+", stem) if token]

        year_index: int | None = None
        year: int | None = None
        for index, token in enumerate(tokens):
            if YEAR_RE.match(token):
                year_index = index
                year = int(token)
                break

        if year_index is not None and year_index > 0:
            title_tokens = tokens[:year_index]
        else:
            title_tokens = []
            for token in tokens:
                normalized = re.sub(r"[^a-z0-9]+", "", token.lower())
                if normalized in NOISE_TOKENS:
                    break
                title_tokens.append(token)

        if not title_tokens:
            title_tokens = tokens[:]

        title = " ".join(title_tokens).strip()
        return {"parsed_title": title, "parsed_year": year}

    @staticmethod
    def _torrent_from_payload(payload: dict) -> TorrentInfo:
        return TorrentInfo(
            id=payload["id"],
            filename=payload.get("filename") or "unknown",
            original_filename=payload.get("original_filename") or payload.get("filename") or "unknown",
            hash=payload.get("hash") or "",
            status=payload.get("status") or "unknown",
            links=list(payload.get("links") or []),
            files=[
                TorrentFile(
                    id=int(item["id"]),
                    path=item["path"],
                    bytes=int(item.get("bytes") or 0),
                    selected=bool(item.get("selected")),
                )
                for item in payload.get("files") or []
            ],
        )
