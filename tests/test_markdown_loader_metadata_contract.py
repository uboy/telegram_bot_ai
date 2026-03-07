from pathlib import Path

from shared.document_loaders.markdown_loader import MarkdownLoader


def test_markdown_loader_sets_section_fallback_and_code_lang_in_full_mode(tmp_path):
    doc = (
        "Text before header\n\n"
        "```bash\n"
        "repo init -u https://example.org/repo.git -b main\n"
        "repo sync -c -j 8\n"
        "```\n"
    )
    path = Path(tmp_path) / "SyncAndBuild.md"
    path.write_text(doc, encoding="utf-8")

    loader = MarkdownLoader()
    chunks = loader.load(str(path), options={"chunking_mode": "full"})

    assert len(chunks) == 1
    meta = chunks[0]["metadata"]
    assert meta["type"] == "markdown"
    assert meta["chunk_kind"] == "full_page"
    assert meta["doc_title"] == "SyncAndBuild"
    assert meta["section_title"] == "SyncAndBuild"
    assert meta["section_path"] == "SyncAndBuild"
    assert meta.get("code_lang") == "bash"
