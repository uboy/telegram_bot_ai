from shared.utils import create_prompt_with_language


def test_create_prompt_with_language_ru_answer_contract_has_no_heading_template():
    prompt = create_prompt_with_language(
        "Что такое разметка данных?",
        context="SOURCE_ID: doc1\nРазметка данных - этап обработки данных.",
        task="answer",
    )

    assert "В базе знаний нет точной информации по этому вопросу." in prompt
    assert "Основной ответ" not in prompt
    assert "Main Answer" not in prompt
    assert "Additionally Found" not in prompt
    assert "Один прямой ответ по сути вопроса" in prompt
    assert "Use inline citations in the format [source_id]" not in prompt
    assert "Inline citations в формате [source_id]" in prompt


def test_create_prompt_with_language_en_answer_contract_matches_direct_grounded_policy():
    prompt = create_prompt_with_language(
        "What is data labeling?",
        context="SOURCE_ID: doc1\nData labeling is the process of assigning identifiers to data.",
        task="answer",
    )

    assert "There is no exact information about this question in the knowledge base." in prompt
    assert "Main Answer" not in prompt
    assert "Additionally Found" not in prompt
    assert "Give a direct answer without template headings" in prompt
    assert "One direct answer to the question without service headings." in prompt


def test_create_prompt_with_language_disables_citation_instructions_when_requested():
    prompt = create_prompt_with_language(
        "What is data labeling?",
        context="SOURCE_ID: doc1\nData labeling is the process of assigning identifiers to data.",
        task="answer",
        enable_citations=False,
    )

    assert "Do not add citations if they are disabled." in prompt
    assert "Use inline citations in the format [source_id]" not in prompt
