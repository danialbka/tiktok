from movie_shorts.rd import RealDebridClient


def test_infer_metadata_extracts_title_and_year_from_release_name() -> None:
    metadata = RealDebridClient.infer_metadata("The.Housemaid.2025.2160p.4K.WEB.x265.10bit.AAC5.1-[YTS.BZ].mkv")

    assert metadata["parsed_title"] == "The Housemaid"
    assert metadata["parsed_year"] == 2025


def test_infer_metadata_stops_at_quality_tokens_without_year() -> None:
    metadata = RealDebridClient.infer_metadata("Mercy.1080p.WEBRip.x264.AAC5.1-[YTS.BZ].mp4")

    assert metadata["parsed_title"] == "Mercy"
    assert metadata["parsed_year"] is None
