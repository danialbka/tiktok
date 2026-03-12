from movie_shorts.scripts import (
    extract_imsdb_script_text,
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
