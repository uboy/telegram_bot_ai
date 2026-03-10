from shared.kb_settings import default_kb_settings


def test_default_kb_settings_use_section_chunking_for_wiki_and_markdown():
    settings = default_kb_settings()

    assert settings["chunking"]["wiki"]["mode"] == "section"
    assert settings["chunking"]["markdown"]["mode"] == "section"
    assert int(settings["chunking"]["wiki"]["max_chars"]) > 0
    assert int(settings["chunking"]["markdown"]["overlap"]) >= 0
