import pytest

from shared.utils import create_prompt_with_language, format_for_telegram_answer


def test_format_for_telegram_answer_keeps_headingless_direct_answer_readable():
    html = format_for_telegram_answer(
        "Разметка данных - этап обработки данных.\n\nrepo sync -c -j 8",
        enable_citations=False,
    )

    assert "Разметка данных - этап обработки данных." in html
    assert "<code>repo sync -c -j 8</code>" in html
    assert "Main Answer" not in html
    assert "Additionally Found" not in html
    assert "Основной ответ" not in html
    assert "Дополнительно" not in html


@pytest.mark.parametrize(
    "refusal",
    [
        "В базе знаний нет точной информации по этому вопросу.",
        "There is no exact information about this question in the knowledge base.",
    ],
)
def test_format_for_telegram_answer_preserves_no_evidence_refusal(refusal):
    html = format_for_telegram_answer(refusal, enable_citations=False)
    assert html == refusal


def test_create_prompt_with_language_answer_contract_has_no_forced_headings():
    ru_prompt = create_prompt_with_language(
        "Что такое разметка данных?",
        context="SOURCE_ID: doc1\nРазметка данных - этап обработки данных.",
        task="answer",
    )
    en_prompt = create_prompt_with_language(
        "What is data labeling?",
        context="SOURCE_ID: doc1\nData labeling is the process of assigning identifiers to data.",
        task="answer",
    )

    for prompt in (ru_prompt, en_prompt):
        assert "Main Answer" not in prompt
        assert "Additionally Found" not in prompt
        assert "Основной ответ" not in prompt
        assert "Дополнительно найдено" not in prompt


def test_format_for_telegram_answer_preserves_legacy_heading_compatibility():
    html = format_for_telegram_answer(
        "Main Answer: First line.\nAdditionally Found: Second line.",
        enable_citations=False,
    )

    assert "<b>Main Answer</b>" in html
    assert "<b>Additionally Found</b>" in html
    assert "First line." in html
    assert "Second line." in html
