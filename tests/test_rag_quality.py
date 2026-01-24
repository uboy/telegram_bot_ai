"""
Тесты для оценки качества RAG системы на основе тест-набора rag_eval.yaml
"""
import pytest

yaml = pytest.importorskip("yaml")
import re
from pathlib import Path
from typing import List, Dict, Any
import sys
import os

# Добавить корневую директорию в путь
sys.path.insert(0, str(Path(__file__).parent.parent))

from shared.rag_system import rag_system
from shared.database import Session, KnowledgeBase


def load_test_cases() -> List[Dict[str, Any]]:
    """Загрузить тест-кейсы из YAML файла"""
    TEST_YAML_FILE = "rag_eval.yaml"
    test_file = Path(__file__).parent / TEST_YAML_FILE
    with open(test_file, 'r', encoding='utf-8') as f:
        data = yaml.safe_load(f)
    return data.get('test_cases', [])


def check_snippet_in_content(snippet: str, content: str) -> bool:
    """Проверить, содержится ли snippet (regex) в content"""
    try:
        pattern = re.compile(snippet, re.IGNORECASE | re.DOTALL)
        return bool(pattern.search(content))
    except re.error:
        # Если не валидный regex, проверяем как обычную строку
        return snippet.lower() in content.lower()


def evaluate_retrieval(test_case: Dict, results: List[Dict], kb_id: int) -> Dict[str, Any]:
    """Оценить качество retrieval для тест-кейса"""
    metrics = {
        'retrieval_at_k': 0,
        'found_expected_source': False,
        'found_snippets': [],
        'found_commands': [],
        'best_score': 0.0,
        'total_results': len(results)
    }
    
    expected_source = test_case.get('expected_source', '')
    expected_snippets = test_case.get('expected_snippets', [])
    expected_commands = test_case.get('expected_commands', [])
    
    # Проверяем top-k результатов
    k = 5
    top_k_results = results[:k]
    
    for idx, result in enumerate(top_k_results):
        content = result.get('content', '')
        source_path = result.get('source_path', '')
        score = float(result.get('rerank_score', result.get('distance', 0.0)))
        
        if idx == 0:
            metrics['best_score'] = score
        
        # Проверка источника
        if expected_source and expected_source.lower() in source_path.lower():
            metrics['found_expected_source'] = True
            metrics['retrieval_at_k'] = idx + 1
            break
        
        # Проверка snippets
        for snippet in expected_snippets:
            if snippet not in metrics['found_snippets']:
                if check_snippet_in_content(snippet, content):
                    metrics['found_snippets'].append(snippet)
        
        # Проверка команд
        for cmd in expected_commands:
            if cmd not in metrics['found_commands']:
                if check_snippet_in_content(cmd, content):
                    metrics['found_commands'].append(cmd)
    
    return metrics


def evaluate_answer(answer: str, test_case: Dict) -> Dict[str, Any]:
    """Оценить качество ответа LLM"""
    metrics = {
        'contains_commands': False,
        'contains_source': False,
        'contains_snippets': [],
        'answer_length': len(answer)
    }
    
    expected_snippets = test_case.get('expected_snippets', [])
    expected_commands = test_case.get('expected_commands', [])
    expected_source = test_case.get('expected_source', '')
    
    answer_lower = answer.lower()
    
    # Проверка команд
    for cmd in expected_commands:
        if check_snippet_in_content(cmd, answer):
            metrics['contains_commands'] = True
            break
    
    # Проверка источника
    if expected_source and expected_source.lower() in answer_lower:
        metrics['contains_source'] = True
    
    # Проверка snippets
    for snippet in expected_snippets:
        if check_snippet_in_content(snippet, answer):
            metrics['contains_snippets'].append(snippet)
    
    return metrics


def run_test_case(test_case: Dict, kb_id: int) -> Dict[str, Any]:
    """Запустить один тест-кейс"""
    query = test_case['query']
    
    print(f"\n{'='*80}")
    print(f"Тест: {test_case.get('id', 'unknown')}")
    print(f"Запрос: {query}")
    print(f"Ожидаемый источник: {test_case.get('expected_source', 'N/A')}")
    print(f"{'='*80}")
    
    # Выполнить поиск
    results = rag_system.search(
        query=query,
        knowledge_base_id=kb_id,
        top_k=10
    )
    
    # Оценить retrieval
    retrieval_metrics = evaluate_retrieval(test_case, results, kb_id)
    
    # Формируем ответ (симуляция того, что делает LLM)
    # В реальности это делается в backend_service/api/routes/rag.py
    answer_parts = []
    if results:
        best_result = results[0]
        answer_parts.append(f"Found information:\n{best_result.get('content', '')[:500]}")
        answer_parts.append(f"\nSource: {best_result.get('source_path', 'N/A')}")
    
    answer = "\n".join(answer_parts)
    answer_metrics = evaluate_answer(answer, test_case)
    
    # Объединить метрики
    return {
        'test_id': test_case.get('id'),
        'query': query,
        'retrieval': retrieval_metrics,
        'answer': answer_metrics,
        'results_count': len(results),
        'passed': (
            retrieval_metrics['found_expected_source'] or 
            len(retrieval_metrics['found_snippets']) >= len(test_case.get('expected_snippets', [])) * 0.5
        )
    }


def run_all_tests(kb_name: str = "Test KB") -> Dict[str, Any]:
    """Запустить все тесты"""
    session = Session()
    
    # Найти или создать тестовую базу знаний
    kb = session.query(KnowledgeBase).filter_by(name=kb_name).first()
    if not kb:
        print(f"⚠️ База знаний '{kb_name}' не найдена!")
        print("Создайте базу знаний и загрузите Sync&Build.md перед запуском тестов.")
        return {'error': f"Knowledge base '{kb_name}' not found"}
    
    kb_id = kb.id
    print(f"📚 Используется база знаний: {kb_name} (ID: {kb_id})")
    
    # Загрузить тест-кейсы
    test_cases = load_test_cases()
    print(f"📋 Загружено тест-кейсов: {len(test_cases)}")
    
    # Запустить тесты
    results = []
    for test_case in test_cases:
        result = run_test_case(test_case, kb_id)
        results.append(result)
        
        # Вывести результаты
        print(f"\n✅ Результаты теста {result['test_id']}:")
        print(f"   Retrieval@5: {'✅' if result['retrieval']['retrieval_at_k'] > 0 else '❌'}")
        print(f"   Найденных snippets: {len(result['retrieval']['found_snippets'])}/{len(test_case.get('expected_snippets', []))}")
        print(f"   Найденных команд: {len(result['retrieval']['found_commands'])}/{len(test_case.get('expected_commands', []))}")
        print(f"   Best score: {result['retrieval']['best_score']:.4f}")
        print(f"   Статус: {'✅ PASSED' if result['passed'] else '❌ FAILED'}")
    
    # Подсчитать статистику
    passed = sum(1 for r in results if r['passed'])
    total = len(results)
    accuracy = (passed / total * 100) if total > 0 else 0
    
    summary = {
        'total_tests': total,
        'passed': passed,
        'failed': total - passed,
        'accuracy': accuracy,
        'results': results
    }
    
    print(f"\n{'='*80}")
    print(f"📊 ИТОГОВАЯ СТАТИСТИКА")
    print(f"{'='*80}")
    print(f"Всего тестов: {total}")
    print(f"Пройдено: {passed}")
    print(f"Провалено: {total - passed}")
    print(f"Точность: {accuracy:.1f}%")
    print(f"{'='*80}")
    
    session.close()
    return summary


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="Тесты качества RAG системы")
    parser.add_argument("--kb-name", default="Test KB", help="Имя базы знаний для тестирования")
    parser.add_argument("--kb-id", type=int, help="ID базы знаний (альтернатива --kb-name)")
    
    args = parser.parse_args()
    
    if args.kb_id:
        session = Session()
        kb = session.query(KnowledgeBase).filter_by(id=args.kb_id).first()
        if not kb:
            print(f"❌ База знаний с ID {args.kb_id} не найдена")
            sys.exit(1)
        kb_name = kb.name
        session.close()
    else:
        kb_name = args.kb_name
    
    run_all_tests(kb_name)

