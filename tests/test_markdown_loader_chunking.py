from pathlib import Path

from shared.document_loaders.markdown_loader import MarkdownLoader


def test_markdown_loader_splits_oversized_full_mode_chunks(tmp_path: Path):
    markdown_path = tmp_path / "oversized.md"
    section = "## Status\n\n" + ("value " * 2000) + "\n\n"
    markdown_path.write_text("# Big Doc\n\n" + section * 12, encoding="utf-8")

    loader = MarkdownLoader()
    chunks = loader.load(
        str(markdown_path),
        options={"chunking_mode": "full", "max_chars": 5000, "overlap": 0},
    )

    assert len(chunks) > 1
    assert [chunk["metadata"]["chunk_no"] for chunk in chunks] == list(range(1, len(chunks) + 1))
    assert all(len(chunk["content"]) <= 5000 for chunk in chunks)
    assert all(chunk["metadata"]["chunk_kind"] != "full_page" for chunk in chunks)
