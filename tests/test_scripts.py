from pathlib import Path

from movie_shorts.scripts import (
    ScriptContextFetcher,
    parse_brave_search_candidates,
    parse_bing_rss_candidates,
    parse_yahoo_search_candidates,
    extract_html_text,
    extract_pdf_text,
    extract_imsdb_script_text,
    parse_simplyscripts_index,
    parse_imsdb_detail_page,
    parse_imsdb_search_result,
    parse_scriptslug_html,
)


def test_parse_scriptslug_html_extracts_summary_and_pdf() -> None:
    html = """
    <html>
      <head>
        <meta property="og:title" content="Us (2019) - Full Screenplay" />
        <meta name="description" content="Screenplay by Jordan Peele. A family is terrorized at their beach house." />
      </head>
      <body>
        <div id="pdfViewer" data-pdf-url="https://assets.scriptslug.com/live/pdf/scripts/us-2019.pdf"></div>
        <div>Screenplay by Jordan Peele</div>
      </body>
    </html>
    """
    source = parse_scriptslug_html(html, "https://www.scriptslug.com/script/us-2019")

    assert source is not None
    assert source.provider == "scriptslug"
    assert source.writer == "Jordan Peele"
    assert "assets.scriptslug.com" in (source.asset_url or "")


def test_parse_imsdb_search_result_prefers_exact_title() -> None:
    html = """
    <html><body>
      <a href="/Movie Scripts/A Serious Man Script.html">A Serious Man</a>
      <a href="/Movie Scripts/Us Script.html">Us</a>
      <a href="/Movie Scripts/All of Us Strangers Script.html">All of Us Strangers</a>
    </body></html>
    """
    url = parse_imsdb_search_result(html, "Us")
    assert url == "https://imsdb.com/Movie Scripts/Us Script.html"


def test_parse_imsdb_search_result_rejects_fuzzy_matches_for_short_titles() -> None:
    html = """
    <html><body>
      <a href="/Movie Scripts/Usual Suspects Script.html">The Usual Suspects</a>
      <a href="/Movie Scripts/All of Us Strangers Script.html">All of Us Strangers</a>
      <a href="/Movie Scripts/Focus Script.html">Focus</a>
    </body></html>
    """

    url = parse_imsdb_search_result(html, "Us")

    assert url is None


def test_parse_imsdb_search_result_rejects_single_word_overlap_for_multiword_titles() -> None:
    html = """
    <html><body>
      <a href="/Movie Scripts/Bringing Out the Dead Script.html">Bringing Out the Dead</a>
      <a href="/Movie Scripts/Out of Sight Script.html">Out of Sight</a>
    </body></html>
    """

    url = parse_imsdb_search_result(html, "Knives Out")

    assert url is None


def test_parse_bing_rss_candidates_prefers_relevant_script_results() -> None:
    xml = """
    <rss><channel>
      <item>
        <title>The Housemaid transcript</title>
        <link>https://scripts.example.com/the-housemaid-transcript.html</link>
        <description>Movie transcript and screenplay notes for The Housemaid.</description>
      </item>
      <item>
        <title>The Housemaid | Trusted Maid Service</title>
        <link>https://maids.example.com/the-housemaid</link>
        <description>House cleaning and maid service for your apartment.</description>
      </item>
    </channel></rss>
    """

    entries = parse_bing_rss_candidates(xml, "The Housemaid")

    assert len(entries) == 1
    assert entries[0].url == "https://scripts.example.com/the-housemaid-transcript.html"
    assert entries[0].content_hint == "transcript"


def test_parse_brave_search_candidates_prefers_relevant_script_results() -> None:
    html = """
    <html><body>
      <div class="result">
        <a data-testid="result-title-a" href="https://scripts.example.com/the-housemaid-transcript.html">The Housemaid transcript</a>
        <div>Movie transcript and screenplay notes for The Housemaid.</div>
      </div>
      <div class="result">
        <a data-testid="result-title-a" href="https://maids.example.com/the-housemaid">The Housemaid | Trusted Maid Service</a>
        <div>House cleaning and maid service for your apartment.</div>
      </div>
    </body></html>
    """

    entries = parse_brave_search_candidates(html, "The Housemaid")

    assert len(entries) == 1
    assert entries[0].url == "https://scripts.example.com/the-housemaid-transcript.html"
    assert entries[0].content_hint == "transcript"


def test_parse_yahoo_search_candidates_prefers_relevant_script_results() -> None:
    html = """
    <html><body>
      <div id="web">
        <ol>
          <li>
            <div class="compTitle">
              <a href="https://r.search.yahoo.com/_ylt=test/RV=2/RE=123/RO=10/RU=https%3a%2f%2fscripts.example.com%2fthe-housemaid-transcript.html/RK=2/RS=x">The Housemaid transcript</a>
            </div>
            <div class="compText">Movie transcript and screenplay notes for The Housemaid.</div>
          </li>
          <li>
            <div class="compTitle">
              <a href="https://r.search.yahoo.com/_ylt=test/RV=2/RE=123/RO=10/RU=https%3a%2f%2fmaids.example.com%2fthe-housemaid/RK=2/RS=x">The Housemaid | Trusted Maid Service</a>
            </div>
            <div class="compText">House cleaning and maid service for your apartment.</div>
          </li>
        </ol>
      </div>
    </body></html>
    """

    entries = parse_yahoo_search_candidates(html, "The Housemaid")

    assert len(entries) == 1
    assert entries[0].url == "https://scripts.example.com/the-housemaid-transcript.html"
    assert entries[0].content_hint == "transcript"


def test_parse_simplyscripts_index_rejects_tiny_substring_matches() -> None:
    html = """
    <html><body>
      <p><a href="http://example.com/m.html">M</a> by Someone Host Site <a href="http://example.com">Example</a></p>
      <p><a href="http://example.com/maid.html">Maid</a> by Someone Host Site <a href="http://example.com">Example</a></p>
    </body></html>
    """

    entry = parse_simplyscripts_index(html, "https://www.simplyscripts.com/full_movie_transcripts.html", "The Housemaid", list_kind="transcript")

    assert entry is not None
    assert entry.title == "Maid"


def test_parse_imsdb_detail_and_script_text() -> None:
    detail_html = """
    <html><body><a href="/scripts/American-Psycho.html">Read "American Psycho" Script</a></body></html>
    """
    script_html = """
    <html><body><td class="scrtext"><pre>
    AMERICAN PSYCHO
    INT. RESTAURANT - NIGHT
    Patrick studies the menu.
    He leans closer.
    The waiter keeps talking.
    Patrick stares back.
    A beat passes.
    More lines follow here.
    More lines follow here.
    More lines follow here.
    More lines follow here.
    More lines follow here.
    More lines follow here.
    </pre></td></body></html>
    """

    detail_url = parse_imsdb_detail_page(detail_html, "https://imsdb.com/Movie Scripts/American Psycho Script.html")
    script_text = extract_imsdb_script_text(script_html)

    assert detail_url == "https://imsdb.com/scripts/American-Psycho.html"
    assert script_text is not None
    assert "AMERICAN PSYCHO" in script_text


def test_extract_pdf_text_uses_pdf_reader(monkeypatch) -> None:
    class FakePage:
        def __init__(self, text: str) -> None:
            self._text = text

        def extract_text(self) -> str:
            return self._text

    class FakeReader:
        def __init__(self, _stream) -> None:
            self.pages = [
                FakePage("INT. HOUSE - NIGHT\nThe family waits by the red door."),
                FakePage("The clock stops at midnight."),
            ]

    monkeypatch.setattr("movie_shorts.scripts.PdfReader", FakeReader)

    text = extract_pdf_text(b"%PDF-pretend")

    assert text is not None
    assert "INT. HOUSE - NIGHT" in text
    assert "clock stops at midnight" in text


def test_fetch_scriptslug_downloads_pdf_text(tmp_path: Path, monkeypatch) -> None:
    html = """
    <html>
      <head>
        <meta property="og:title" content="Us (2019) - Full Screenplay" />
        <meta name="description" content="Screenplay by Jordan Peele. A family is terrorized at their beach house." />
      </head>
      <body>
        <div id="pdfViewer" data-pdf-url="https://assets.scriptslug.com/live/pdf/scripts/us-2019.pdf"></div>
        <div>Screenplay by Jordan Peele</div>
      </body>
    </html>
    """

    class FakeResponse:
        def __init__(self, text: str = "", content: bytes = b"", status_code: int = 200) -> None:
            self.text = text
            self.content = content
            self.status_code = status_code

        def raise_for_status(self) -> None:
            if self.status_code >= 400:
                raise RuntimeError("bad status")

    class FakeClient:
        def get(self, url: str):
            if url.endswith("us-2019"):
                return FakeResponse(text=html)
            if url.endswith(".pdf"):
                return FakeResponse(content=b"%PDF-fake")
            return FakeResponse(status_code=404)

        def close(self) -> None:
            return None

    monkeypatch.setattr("movie_shorts.scripts.extract_pdf_text", lambda _bytes: "INT. BEACH HOUSE - NIGHT\nAdelaide watches the hallway.\n" * 20)

    fetcher = ScriptContextFetcher()
    monkeypatch.setattr(fetcher, "_client", FakeClient())

    source = fetcher._fetch_scriptslug("Us", 2019, tmp_path)

    assert source is not None
    assert source.provider == "scriptslug"
    assert source.source_kind == "script_text"
    assert source.script_text_path is not None
    script_path = Path(source.script_text_path)
    assert script_path.exists()
    assert "Adelaide watches the hallway" in script_path.read_text(encoding="utf-8")
    assert (tmp_path / "scriptslug_script.pdf").exists()


def test_fetch_web_search_downloads_html_transcript(tmp_path: Path, monkeypatch) -> None:
    xml = """
    <rss><channel>
      <item>
        <title>The Housemaid transcript</title>
        <link>https://scripts.example.com/the-housemaid-transcript.html</link>
        <description>Movie transcript and screenplay notes for The Housemaid.</description>
      </item>
      <item>
        <title>The Housemaid | Trusted Maid Service</title>
        <link>https://maids.example.com/the-housemaid</link>
        <description>House cleaning and maid service for your apartment.</description>
      </item>
    </channel></rss>
    """
    transcript_html = """
    <html><body>
    <h1>THE HOUSEMAID</h1>
    <p>INT. HOUSE - NIGHT</p>
    <p>Millie enters the kitchen with a tray.</p>
    <p>She tells Nina that Andrew is upstairs and the family is not ready.</p>
    <p>Nina says this house changes people and nobody leaves untouched.</p>
    <p>Millie looks toward the hallway and hears a scream from the second floor.</p>
    <p>The silverware rattles as she steps closer to the dining room.</p>
    </body></html>
    """

    class FakeResponse:
        def __init__(self, text: str = "", content: bytes = b"", status_code: int = 200, headers: dict | None = None) -> None:
            self.text = text
            self.content = content or text.encode("utf-8")
            self.status_code = status_code
            self.headers = headers or {}

        def raise_for_status(self) -> None:
            if self.status_code >= 400:
                raise RuntimeError("bad status")

    class FakeClient:
        def get(self, url: str, params: dict | None = None, headers: dict | None = None):
            if url == "https://search.brave.com/search":
                html = """
                <html><body>
                  <div class="result">
                    <a data-testid="result-title-a" href="https://scripts.example.com/the-housemaid-transcript.html">The Housemaid transcript</a>
                    <div>Movie transcript and screenplay notes for The Housemaid.</div>
                  </div>
                </body></html>
                """
                return FakeResponse(text=html, headers={"content-type": "text/html"})
            if url == "https://www.bing.com/search":
                assert params is not None
                return FakeResponse(text=xml, headers={"content-type": "application/rss+xml"})
            if url == "https://scripts.example.com/the-housemaid-transcript.html":
                return FakeResponse(text=transcript_html, headers={"content-type": "text/html"})
            return FakeResponse(status_code=404)

        def close(self) -> None:
            return None

    fetcher = ScriptContextFetcher()
    monkeypatch.setattr(fetcher, "_client", FakeClient())

    source = fetcher._fetch_web_search("The Housemaid", tmp_path)

    assert source is not None
    assert source.provider == "websearch"
    assert source.source_kind == "transcript"
    assert source.script_text_path is not None
    script_path = Path(source.script_text_path)
    assert script_path.exists()
    assert "Millie enters the kitchen" in script_path.read_text(encoding="utf-8")


def test_fetch_orders_web_search_after_curated_sources(tmp_path: Path, monkeypatch) -> None:
    fetcher = ScriptContextFetcher()
    calls: list[str] = []

    monkeypatch.setattr(fetcher, "_fetch_scriptslug", lambda *args, **kwargs: calls.append("scriptslug") or None)
    monkeypatch.setattr(fetcher, "_fetch_imsdb", lambda *args, **kwargs: calls.append("imsdb") or None)
    monkeypatch.setattr(fetcher, "_fetch_simplyscripts", lambda *args, **kwargs: calls.append("simplyscripts") or None)
    monkeypatch.setattr(fetcher, "_fetch_web_search", lambda *args, **kwargs: calls.append("websearch") or None)

    sources = fetcher.fetch("The Housemaid", 2025, tmp_path)

    assert sources == []
    assert calls == ["scriptslug", "imsdb", "simplyscripts", "websearch"]


def test_parse_simplyscripts_index_prefers_exact_title() -> None:
    html = """
    <html><body>
      <p><a href="https://www.dailyscript.com/scripts/House.html">House</a> by Someone Host Site <a href="https://www.dailyscript.com">Daily Script</a></p>
      <p><a href="/scripts/TheHousemaid.pdf">The Housemaid</a> by Rebecca Pollock & Kas Graham Host Site <a href="https://www.simplyscripts.com">SimplyScripts</a></p>
      <p><a href="https://example.com/thehousemaid2.pdf">The Housemaid 2</a> by Someone Else Host Site <a href="https://example.com">Example</a></p>
    </body></html>
    """

    entry = parse_simplyscripts_index(html, "https://www.simplyscripts.com/movie-screenplays.html", "The Housemaid", list_kind="screenplay")

    assert entry is not None
    assert entry.title == "The Housemaid"
    assert entry.script_url == "https://www.simplyscripts.com/scripts/TheHousemaid.pdf"
    assert entry.host_site == "SimplyScripts"


def test_extract_html_text_strips_scripts_and_styles() -> None:
    html = """
    <html><head><style>.x{color:red;}</style><script>bad()</script></head>
    <body><h1>INT. KITCHEN - NIGHT</h1><p>The maid opens the freezer.</p></body></html>
    """

    text = extract_html_text(html)

    assert text is not None
    assert "INT KITCHEN" not in text
    assert "INT. KITCHEN - NIGHT" in text
    assert "bad()" not in text


def test_fetch_simplyscripts_downloads_html_transcript(tmp_path: Path, monkeypatch) -> None:
    index_html = """
    <html><body>
      <p><a href="https://transcripts.example.com/housemaid.html">The Housemaid</a> by Rebecca Pollock & Kas Graham Host Site <a href="https://transcripts.example.com">Transcript Host</a></p>
    </body></html>
    """
    transcript_html = """
    <html><body>
    <h1>THE HOUSEMAID</h1>
    <p>INT. HOUSE - NIGHT</p>
    <p>Millie enters the kitchen with a tray.</p>
    <p>She tells Nina that Andrew is upstairs and the family is not ready.</p>
    <p>Nina says this house changes people and nobody leaves untouched.</p>
    <p>Millie looks toward the hallway and hears a scream from the second floor.</p>
    <p>The silverware rattles as she steps closer to the dining room.</p>
    </body></html>
    """

    class FakeResponse:
        def __init__(self, text: str = "", content: bytes = b"", status_code: int = 200, headers: dict | None = None) -> None:
            self.text = text
            self.content = content or text.encode("utf-8")
            self.status_code = status_code
            self.headers = headers or {}

        def raise_for_status(self) -> None:
            if self.status_code >= 400:
                raise RuntimeError("bad status")

    class FakeClient:
        def get(self, url: str):
            if url.endswith("movie-screenplays.html"):
                return FakeResponse(text="<html><body></body></html>", headers={"content-type": "text/html"})
            if url.endswith("full_movie_transcripts.html"):
                return FakeResponse(text=index_html, headers={"content-type": "text/html"})
            if url.endswith("housemaid.html"):
                return FakeResponse(text=transcript_html, headers={"content-type": "text/html"})
            return FakeResponse(status_code=404)

        def post(self, *args, **kwargs):
            raise AssertionError("Unexpected POST")

        def close(self) -> None:
            return None

    fetcher = ScriptContextFetcher()
    monkeypatch.setattr(fetcher, "_client", FakeClient())

    source = fetcher._fetch_simplyscripts("The Housemaid", tmp_path)

    assert source is not None
    assert source.provider == "simplyscripts"
    assert source.source_kind == "transcript"
    assert source.script_text_path is not None
    script_path = Path(source.script_text_path)
    assert script_path.exists()
    assert "Millie enters the kitchen" in script_path.read_text(encoding="utf-8")


def test_fetch_simplyscripts_skips_dead_candidate_link(tmp_path: Path, monkeypatch) -> None:
    index_html = """
    <html><body>
      <p><a href="https://bad.example.com/us.pdf">Us</a> by Jordan Peele Host Site <a href="https://bad.example.com">Bad Host</a></p>
      <p><a href="https://transcripts.example.com/housemaid.html">The Housemaid</a> by Rebecca Pollock & Kas Graham Host Site <a href="https://transcripts.example.com">Transcript Host</a></p>
    </body></html>
    """
    transcript_html = """
    <html><body>
    <h1>THE HOUSEMAID</h1>
    <p>INT. HOUSE - NIGHT</p>
    <p>Millie enters the kitchen with a tray.</p>
    <p>She tells Nina that Andrew is upstairs and the family is not ready.</p>
    <p>Nina says this house changes people and nobody leaves untouched.</p>
    <p>Millie looks toward the hallway and hears a scream from the second floor.</p>
    </body></html>
    """

    class FakeResponse:
        def __init__(self, text: str = "", content: bytes = b"", status_code: int = 200, headers: dict | None = None, url: str = "https://example.com") -> None:
            self.text = text
            self.content = content or text.encode("utf-8")
            self.status_code = status_code
            self.headers = headers or {}
            self.request = type("Req", (), {"url": url})()

        def raise_for_status(self) -> None:
            if self.status_code >= 400:
                import httpx
                raise httpx.HTTPStatusError("bad status", request=self.request, response=self)

    class FakeClient:
        def get(self, url: str):
            if url.endswith("movie-screenplays.html"):
                return FakeResponse(text=index_html, headers={"content-type": "text/html"}, url=url)
            if url.endswith("full_movie_transcripts.html"):
                return FakeResponse(text="<html><body></body></html>", headers={"content-type": "text/html"}, url=url)
            if url.endswith("us.pdf"):
                return FakeResponse(status_code=404, headers={"content-type": "application/pdf"}, url=url)
            if url.endswith("housemaid.html"):
                return FakeResponse(text=transcript_html, headers={"content-type": "text/html"}, url=url)
            return FakeResponse(status_code=404, url=url)

        def post(self, *args, **kwargs):
            raise AssertionError("Unexpected POST")

        def close(self) -> None:
            return None

    fetcher = ScriptContextFetcher()
    monkeypatch.setattr(fetcher, "_client", FakeClient())

    source = fetcher._fetch_simplyscripts("The Housemaid", tmp_path)

    assert source is not None
    assert source.title == "The Housemaid"
