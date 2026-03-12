from __future__ import annotations

from dataclasses import dataclass
from html import unescape
from io import BytesIO
from pathlib import Path
import re
from urllib.parse import parse_qs, unquote, urljoin, urlparse
from xml.etree import ElementTree as ET

from bs4 import BeautifulSoup
import httpx
from pypdf import PdfReader

from .models import ScriptContextSource


WHITESPACE_RE = re.compile(r"\s+")
TITLE_SPLIT_RE = re.compile(r"[^a-z0-9]+")


@dataclass(slots=True)
class ScriptBundle:
    sources: list[ScriptContextSource]


@dataclass(slots=True)
class SimplyScriptsEntry:
    title: str
    script_url: str
    detail_text: str
    host_site: str | None
    list_kind: str


@dataclass(slots=True)
class WebSearchEntry:
    title: str
    url: str
    snippet: str
    score: int
    content_hint: str


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

        simplyscripts_source = self._fetch_simplyscripts(title, artifact_dir)
        if simplyscripts_source:
            sources.append(simplyscripts_source)

        web_source = self._fetch_web_search(title, artifact_dir)
        if web_source:
            sources.append(web_source)

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
            context_lines = [part for part in [parsed.title, parsed.writer, parsed.summary, parsed.asset_url] if part]
            metadata_path = artifact_dir / "scriptslug_context.txt"
            metadata_path.write_text("\n\n".join(context_lines), encoding="utf-8")

            full_script_text = None
            if parsed.asset_url:
                full_script_text = self._download_scriptslug_pdf_text(parsed.asset_url, artifact_dir)

            if full_script_text:
                text_path = artifact_dir / "scriptslug_script.txt"
                text_path.write_text(full_script_text, encoding="utf-8")
                parsed.script_text_path = str(text_path)
                parsed.source_kind = "script_text"
            else:
                parsed.script_text_path = str(metadata_path)
            return parsed
        return None

    def _fetch_web_search(self, title: str, artifact_dir: Path) -> ScriptContextSource | None:
        entries = self._search_web_candidates(title)
        for entry in entries[:8]:
            try:
                text = self._download_remote_script_text(entry.url, artifact_dir, "websearch_script")
            except httpx.HTTPError:
                continue
            if not text:
                continue

            text_path = artifact_dir / "websearch_script.txt"
            text_path.write_text(text, encoding="utf-8")
            summary = entry.snippet[:1200].strip() if entry.snippet else text[:1200].strip()
            return ScriptContextSource(
                provider="websearch",
                title=entry.title,
                url=entry.url,
                summary=summary or None,
                asset_url=entry.url,
                script_text_path=str(text_path),
                source_kind=entry.content_hint,
            )
        return None

    def _search_web_candidates(self, title: str) -> list[WebSearchEntry]:
        query = f"\"{title}\" movie screenplay script transcript pdf"
        for url, parser, params in (
            (
                "https://search.brave.com/search",
                parse_brave_search_candidates,
                {"q": query, "source": "web"},
            ),
            (
                "https://search.yahoo.com/search",
                parse_yahoo_search_candidates,
                {"p": query},
            ),
            (
                "https://www.bing.com/search",
                parse_bing_rss_candidates,
                {"format": "rss", "q": query},
            ),
        ):
            try:
                response = self._client.get(url, params=params, headers=_web_search_headers())
                response.raise_for_status()
            except httpx.HTTPError:
                continue
            entries = parser(response.text, title)
            if entries:
                return entries
        return []

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

    def _download_scriptslug_pdf_text(self, pdf_url: str, artifact_dir: Path) -> str | None:
        response = self._client.get(pdf_url)
        response.raise_for_status()
        pdf_bytes = response.content
        if not pdf_bytes:
            return None

        pdf_path = artifact_dir / "scriptslug_script.pdf"
        pdf_path.write_bytes(pdf_bytes)
        extracted = extract_pdf_text(pdf_bytes)
        if not extracted or len(extracted) < 200:
            return None
        return extracted

    def _fetch_simplyscripts(self, title: str, artifact_dir: Path) -> ScriptContextSource | None:
        for list_kind, page_url in (
            ("screenplay", "https://www.simplyscripts.com/movie-screenplays.html"),
            ("transcript", "https://www.simplyscripts.com/full_movie_transcripts.html"),
        ):
            response = self._client.get(page_url)
            response.raise_for_status()
            entries = parse_simplyscripts_candidates(response.text, page_url, title, list_kind=list_kind)
            for entry in entries[:6]:
                try:
                    text = self._download_remote_script_text(entry.script_url, artifact_dir, f"simplyscripts_{list_kind}")
                except httpx.HTTPError:
                    continue
                if not text:
                    continue

                text_path = artifact_dir / f"simplyscripts_{list_kind}.txt"
                text_path.write_text(text, encoding="utf-8")
                summary = entry.detail_text[:1200].strip() if entry.detail_text else text[:1200].strip()
                return ScriptContextSource(
                    provider="simplyscripts",
                    title=entry.title,
                    url=entry.script_url,
                    summary=summary or None,
                    asset_url=entry.script_url,
                    script_text_path=str(text_path),
                    source_kind="script_text" if list_kind == "screenplay" else "transcript",
                )
        return None

    def _download_remote_script_text(self, source_url: str, artifact_dir: Path, stem: str) -> str | None:
        response = self._client.get(source_url)
        response.raise_for_status()
        content = response.content
        if not content:
            return None

        path = urlparse(source_url).path.lower()
        content_type = (response.headers.get("content-type") or "").lower()
        if path.endswith(".pdf") or "pdf" in content_type:
            pdf_path = artifact_dir / f"{stem}.pdf"
            pdf_path.write_bytes(content)
            text = extract_pdf_text(content)
            return text if text and len(text) >= 200 else None

        if path.endswith(".txt") or "text/plain" in content_type:
            text = _clean_text(response.text)
            return text if len(text) >= 200 else None

        if path.endswith((".html", ".htm")) or "text/html" in content_type or not content_type:
            text = extract_html_text(response.text)
            return text if text and len(text) >= 200 else None

        return None


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


def extract_pdf_text(pdf_bytes: bytes) -> str | None:
    reader = PdfReader(BytesIO(pdf_bytes))
    chunks: list[str] = []
    for page in reader.pages:
        page_text = page.extract_text() or ""
        cleaned = _clean_text(page_text)
        if cleaned:
            chunks.append(cleaned)
    combined = "\n\n".join(chunks).strip()
    return combined or None


def extract_html_text(html: str) -> str | None:
    soup = BeautifulSoup(html, "html.parser")
    for node in soup(["script", "style", "noscript"]):
        node.decompose()
    text = _clean_text(soup.get_text("\n", strip=True))
    return text or None


def parse_simplyscripts_index(html: str, page_url: str, requested_title: str, list_kind: str) -> SimplyScriptsEntry | None:
    entries = parse_simplyscripts_candidates(html, page_url, requested_title, list_kind=list_kind)
    return entries[0] if entries else None


def parse_bing_rss_candidates(xml_text: str, requested_title: str) -> list[WebSearchEntry]:
    normalized_target = _normalize_title(requested_title)
    candidates: list[WebSearchEntry] = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []

    for item in root.findall(".//item"):
        title = _clean_text(item.findtext("title"))
        link = _clean_text(item.findtext("link"))
        snippet = _clean_text(item.findtext("description"))
        if not title or not link:
            continue

        score, content_hint = _score_web_search_entry(normalized_target, title, link, snippet)
        if score <= 0:
            continue

        candidates.append(
            WebSearchEntry(
                title=title,
                url=link,
                snippet=snippet,
                score=score,
                content_hint=content_hint,
            )
        )

    candidates.sort(key=lambda entry: (entry.score, len(entry.snippet)), reverse=True)
    return candidates


def parse_brave_search_candidates(html: str, requested_title: str) -> list[WebSearchEntry]:
    soup = BeautifulSoup(html, "html.parser")
    normalized_target = _normalize_title(requested_title)
    candidates: list[WebSearchEntry] = []

    for anchor in soup.select('a[data-testid="result-title-a"]'):
        title = _clean_text(anchor.get_text(" ", strip=True))
        link = _clean_text(anchor.get("href"))
        if not title or not link:
            continue

        container = anchor.find_parent(["div", "article", "section"])
        snippet = _clean_text(container.get_text(" ", strip=True)) if container else ""
        score, content_hint = _score_web_search_entry(normalized_target, title, link, snippet)
        if score <= 0:
            continue

        candidates.append(
            WebSearchEntry(
                title=title,
                url=link,
                snippet=snippet,
                score=score,
                content_hint=content_hint,
            )
        )

    candidates.sort(key=lambda entry: (entry.score, len(entry.snippet)), reverse=True)
    return candidates


def parse_yahoo_search_candidates(html: str, requested_title: str) -> list[WebSearchEntry]:
    soup = BeautifulSoup(html, "html.parser")
    normalized_target = _normalize_title(requested_title)
    candidates: list[WebSearchEntry] = []

    for anchor in soup.select("div#web div.compTitle a"):
        title = _clean_text(anchor.get_text(" ", strip=True))
        link = _unwrap_yahoo_redirect(_clean_text(anchor.get("href")))
        if not title or not link:
            continue

        container = anchor.find_parent(["div", "li"])
        snippet = ""
        if container:
            snippet_node = container.find(["p", "div"], class_=re.compile(r"compText|snippet", re.IGNORECASE))
            if snippet_node:
                snippet = _clean_text(snippet_node.get_text(" ", strip=True))
        score, content_hint = _score_web_search_entry(normalized_target, title, link, snippet)
        if score <= 0:
            continue

        candidates.append(
            WebSearchEntry(
                title=title,
                url=link,
                snippet=snippet,
                score=score,
                content_hint=content_hint,
            )
        )

    candidates.sort(key=lambda entry: (entry.score, len(entry.snippet)), reverse=True)
    return candidates


def parse_simplyscripts_candidates(html: str, page_url: str, requested_title: str, list_kind: str) -> list[SimplyScriptsEntry]:
    soup = BeautifulSoup(html, "html.parser")
    normalized_target = _normalize_title(requested_title)
    scored_entries: list[tuple[int, SimplyScriptsEntry]] = []

    for paragraph in soup.find_all("p"):
        anchors = paragraph.find_all("a", href=True)
        if not anchors:
            continue

        title_anchor = anchors[0]
        title = _clean_text(title_anchor.get_text(" ", strip=True))
        normalized_candidate = _normalize_title(title)
        score = _title_score(normalized_target, normalized_candidate)
        if score <= 0:
            continue

        host_anchor = anchors[-1] if len(anchors) > 1 else None
        detail_text = _clean_text(paragraph.get_text(" ", strip=True))
        entry = SimplyScriptsEntry(
            title=title,
            script_url=urljoin(page_url, title_anchor["href"]),
            detail_text=detail_text,
            host_site=_clean_text(host_anchor.get_text(" ", strip=True)) if host_anchor and host_anchor is not title_anchor else None,
            list_kind=list_kind,
        )
        scored_entries.append((score, entry))

    scored_entries.sort(key=lambda item: (item[0], len(_normalize_title(item[1].title))), reverse=True)
    return [entry for _, entry in scored_entries]


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
    target_words = target.split()
    candidate_words = candidate.split()

    if target == candidate:
        return 100

    if len(candidate_words) == 1 and candidate_words[0] != target_words[0] and len(candidate_words[0]) <= 3:
        return 0

    if len(target_words) == 1:
        target_word = target_words[0]
        if len(target_word) <= 3:
            return 0
        if target_word in candidate_words:
            return 80 if len(candidate_words) == 1 else 60

    if target in candidate or candidate in target:
        return 70
    overlap = len(set(target_words) & set(candidate_words))
    if len(target_words) > 1 and overlap < 2:
        return 0
    return overlap


def _unwrap_yahoo_redirect(url: str) -> str:
    if "r.search.yahoo.com" not in url:
        return url
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    ru = query.get("RU")
    if ru:
        return unquote(ru[0])

    path = parsed.path
    marker = "/RU="
    if marker in path:
        encoded = path.split(marker, 1)[1].split("/RK=", 1)[0]
        return unquote(encoded)
    return url


def _web_search_headers() -> dict[str, str]:
    return {
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "en-US,en;q=0.9",
    }


def _score_web_search_entry(target: str, title: str, link: str, snippet: str) -> tuple[int, str]:
    normalized_title = _normalize_title(title)
    normalized_link = _normalize_title(urlparse(link).path.replace("/", " "))
    combined = _clean_text(f"{title} {snippet} {urlparse(link).netloc} {urlparse(link).path}")
    normalized_combined = _normalize_title(combined)

    title_score = _title_score(target, normalized_title)
    combined_score = _title_score(target, normalized_combined)
    if max(title_score, combined_score) <= 0:
        return (0, "script_text")

    lower_blob = combined.lower()
    if any(term in lower_blob for term in ("maid service", "cleaning service", "house cleaning", "housekeeping service")):
        return (0, "script_text")

    keyword_bonus = 0
    content_hint = "script_text"
    if "screenplay" in lower_blob:
        keyword_bonus += 40
    if "script" in lower_blob:
        keyword_bonus += 35
    if "transcript" in lower_blob:
        keyword_bonus += 30
        content_hint = "transcript"
    if ".pdf" in lower_blob or link.lower().endswith(".pdf"):
        keyword_bonus += 25

    domain_bonus = 0
    hostname = urlparse(link).netloc.lower()
    if any(hostname.endswith(domain) for domain in ("scriptslug.com", "imsdb.com", "simplyscripts.com", "dailyscript.com", "8flix.com")):
        domain_bonus += 15

    path_score = _title_score(target, normalized_link)
    total = max(title_score, combined_score) + path_score + keyword_bonus + domain_bonus
    return (total if keyword_bonus >= 25 else 0, content_hint)
