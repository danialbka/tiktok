from __future__ import annotations

from dataclasses import dataclass
from html import unescape
from pathlib import Path
import re

from bs4 import BeautifulSoup
import httpx

from .models import ScriptContextSource


WHITESPACE_RE = re.compile(r"\s+")
TITLE_SPLIT_RE = re.compile(r"[^a-z0-9]+")


@dataclass(slots=True)
class ScriptBundle:
    sources: list[ScriptContextSource]


class ScriptContextFetcher:
    def __init__(self, timeout_seconds: float = 30.0) -> None:
        self._client = httpx.Client(
            timeout=timeout_seconds,
            headers={"User-Agent": "movie-shorts/0.1 (+script-context)"},
            follow_redirects=True,
        )

    def close(self) -> None:
        self._client.close()

    def fetch(self, title: str, year: int | None, artifact_dir: Path) -> list[ScriptContextSource]:
        artifact_dir.mkdir(parents=True, exist_ok=True)
        sources: list[ScriptContextSource] = []

        script_slug_source = self._fetch_scriptslug(title, year, artifact_dir)
        if script_slug_source:
            sources.append(script_slug_source)

        imsdb_source = self._fetch_imsdb(title, artifact_dir)
        if imsdb_source:
            sources.append(imsdb_source)

        return sources

    def _fetch_scriptslug(self, title: str, year: int | None, artifact_dir: Path) -> ScriptContextSource | None:
        if not year:
            return None

        slug = _slugify_title(title)
        candidate_urls = [
            f"https://www.scriptslug.com/script/{slug}-{year}",
            f"https://www.scriptslug.com/script/{slug}-{year - 1}",
            f"https://www.scriptslug.com/script/{slug}-{year + 1}",
        ]
        for url in candidate_urls:
            response = self._client.get(url)
            if response.status_code != 200:
                continue
            parsed = parse_scriptslug_html(response.text, url)
            if not parsed:
                continue
            if parsed.title and title.lower() not in parsed.title.lower():
                continue
            text_path = artifact_dir / "scriptslug_context.txt"
            context_lines = [part for part in [parsed.title, parsed.writer, parsed.summary, parsed.asset_url] if part]
            text_path.write_text("\n\n".join(context_lines), encoding="utf-8")
            parsed.script_text_path = str(text_path)
            return parsed
        return None

    def _fetch_imsdb(self, title: str, artifact_dir: Path) -> ScriptContextSource | None:
        response = self._client.post("https://imsdb.com/search.php", data={"search_query": title, "submit": "Go!"})
        response.raise_for_status()
        match_url = parse_imsdb_search_result(response.text, title)
        if not match_url:
            return None

        detail_response = self._client.get(match_url)
        detail_response.raise_for_status()
        script_page_url = parse_imsdb_detail_page(detail_response.text, match_url)
        if not script_page_url:
            return None

        script_response = self._client.get(script_page_url)
        script_response.raise_for_status()
        script_text = extract_imsdb_script_text(script_response.text)
        if not script_text:
            return None

        text_path = artifact_dir / "imsdb_script.txt"
        text_path.write_text(script_text, encoding="utf-8")
        return ScriptContextSource(
            provider="imsdb",
            title=title,
            url=script_page_url,
            summary=script_text[:1200].strip(),
            script_text_path=str(text_path),
            source_kind="script_text",
        )


def parse_scriptslug_html(html: str, page_url: str) -> ScriptContextSource | None:
    soup = BeautifulSoup(html, "html.parser")
    title_node = soup.find("meta", attrs={"property": "og:title"})
    description_node = soup.find("meta", attrs={"name": "description"}) or soup.find("meta", attrs={"property": "og:description"})
    title = title_node["content"].strip() if title_node and title_node.get("content") else None
    description = _clean_text(description_node["content"]) if description_node and description_node.get("content") else None

    writer = None
    text = soup.get_text("\n", strip=True)
    writer_match = re.search(r"Screenplay by\s+([^\n]+)", text, re.IGNORECASE)
    if writer_match:
        writer = _clean_text(writer_match.group(1))

    year = None
    if title:
        year_match = re.search(r"\((\d{4})\)", title)
        if year_match:
            year = int(year_match.group(1))

    pdf_node = soup.find(id="pdfViewer")
    pdf_url = pdf_node.get("data-pdf-url") if pdf_node else None
    if not any([title, description, pdf_url]):
        return None

    summary_parts = [part for part in [description, f"PDF: {pdf_url}" if pdf_url else None] if part]
    return ScriptContextSource(
        provider="scriptslug",
        title=title or "Unknown title",
        url=page_url,
        summary="\n".join(summary_parts) if summary_parts else None,
        writer=writer,
        year=year,
        asset_url=pdf_url,
        source_kind="metadata",
    )


def parse_imsdb_search_result(html: str, requested_title: str) -> str | None:
    soup = BeautifulSoup(html, "html.parser")
    anchors = soup.find_all("a", href=True)
    normalized_target = _normalize_title(requested_title)
    best_url: str | None = None
    best_score = -1
    for anchor in anchors:
        href = anchor["href"]
        if not href.startswith("/Movie Scripts/"):
            continue
        candidate_title = _clean_text(anchor.get_text(" ", strip=True))
        normalized_candidate = _normalize_title(candidate_title)
        score = _title_score(normalized_target, normalized_candidate)
        if score > best_score:
            best_score = score
            best_url = f"https://imsdb.com{href}"
    return best_url if best_score > 0 else None


def parse_imsdb_detail_page(html: str, page_url: str) -> str | None:
    soup = BeautifulSoup(html, "html.parser")
    anchors = soup.find_all("a", href=True)
    for anchor in anchors:
        href = anchor["href"]
        if href.startswith("/scripts/"):
            return f"https://imsdb.com{href}"
    return None


def extract_imsdb_script_text(html: str) -> str | None:
    soup = BeautifulSoup(html, "html.parser")
    container = soup.find("td", class_="scrtext")
    if not container:
        return None
    raw_text = container.get_text("\n", strip=True)
    cleaned = _clean_text(raw_text)
    if len(cleaned) < 100:
        return None
    return cleaned


def _slugify_title(title: str) -> str:
    parts = [part for part in TITLE_SPLIT_RE.split(title.lower()) if part]
    return "-".join(parts)


def _clean_text(text: str | None) -> str:
    if not text:
        return ""
    return WHITESPACE_RE.sub(" ", unescape(text)).strip()


def _normalize_title(title: str) -> str:
    words = [word for word in TITLE_SPLIT_RE.split(title.lower()) if word]
    filtered = [word for word in words if word not in {"the", "a", "an", "script"}]
    return " ".join(filtered)


def _title_score(target: str, candidate: str) -> int:
    if target == candidate:
        return 100
    if target in candidate or candidate in target:
        return 70
    target_words = set(target.split())
    candidate_words = set(candidate.split())
    return len(target_words & candidate_words)
