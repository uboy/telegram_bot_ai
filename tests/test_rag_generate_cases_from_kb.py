from scripts import rag_generate_cases_from_kb as generator


def test_build_query_uses_sentence_and_truncates():
    text = (
        "This is a long sentence for query generation in compare script with many words "
        "that should be truncated to a stable retrieval prompt. Second sentence."
    )
    query = generator._build_query(text, min_words=4)
    assert query
    assert len(query.split()) <= 16
    assert "Second sentence" not in query


def test_build_query_returns_empty_for_too_short_content():
    assert generator._build_query("short", min_words=4) == ""


def test_source_label_returns_basename():
    assert generator._source_label("/tmp/docs/test.pdf") == "test.pdf"
