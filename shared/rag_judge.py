"""
LLM-as-judge logic for RAG answer quality evaluation (RAGEVAL-001).
"""
import json
import re
import time
from typing import Any, Dict, List, Optional, Tuple
from shared.logging_config import logger
from shared.ai_providers import ai_manager

# §4.1 Judge Prompt from Design Doc
JUDGE_PROMPT_TEMPLATE = """You are an impartial judge evaluating the quality of an AI-generated answer based on the provided retrieved context.
Your task is to score the answer on three dimensions: Faithfulness, Relevance, and Completeness.

### Dimensions:
1. **Faithfulness (0.0-1.0)**: Is the answer derived ONLY from the provided context? Score 0.0 if the answer contains hallucinations or external knowledge not present in the context.
2. **Relevance (0.0-1.0)**: does the answer directly address the user's query?
3. **Completeness (0.0-1.0)**: Does the answer provide all necessary information from the context to fully satisfy the query?

### Input:
**Query:** {query}
**Retrieved Context:**
{context}

**AI Answer:**
{answer}

### Evaluation Rules:
- If the AI Answer is an "Intentional Refusal" (e.g., "I don't know", "Information not found"), score Relevance and Completeness based on whether a refusal was appropriate given the context. 
- If the context DOES NOT contain the answer, a refusal is 1.0 on Relevance.
- Provide a brief reasoning for your scores.

### Output Format:
Return ONLY a JSON object with the following structure:
{{
  "faithfulness": float,
  "relevance": float,
  "completeness": float,
  "reasoning": "string"
}}
"""

# §4.1 Intentional Refusal Detection
REFUSAL_PATTERNS = [
    r"информац(ии|ия)\s+недостаточно",
    r"не могу ответить",
    r"не найдено в контексте",
    r"cannot answer from the provided context",
    r"not enough information",
    r"i do not have enough context",
    r"can't determine from the provided context",
    r"я не знаю",
    r"в предоставленных документах нет"
]

def is_intentional_refusal(answer: str) -> bool:
    """Detects if the answer is an intentional refusal to answer due to lack of context."""
    for pattern in REFUSAL_PATTERNS:
        if re.search(pattern, answer, re.IGNORECASE):
            return True
    return False

def score_answer(
    query: str, 
    answer: str, 
    context_snippets: List[str],
    model: Optional[str] = None,
    provider: Optional[str] = None
) -> Dict[str, Any]:
    """
    Evaluates a single RAG answer using an LLM judge.
    Returns scores and reasoning.
    """
    # Truncate context to 2000 chars as per §4.1
    combined_context = "\n---\n".join(context_snippets)
    if len(combined_context) > 2000:
        combined_context = combined_context[:2000] + "... [TRUNCATED]"

    prompt = JUDGE_PROMPT_TEMPLATE.format(
        query=query,
        context=combined_context,
        answer=answer
    )

    started_at = time.monotonic()
    try:
        response_text = ai_manager.query(
            prompt,
            provider_name=provider,
            model=model,
            telemetry_meta={"feature": "rag_judge"}
        )
        
        # Parse JSON from response
        # Find JSON block if there's conversational filler
        json_match = re.search(r"\{.*\}", response_text, re.DOTALL)
        if json_match:
            result = json.loads(json_match.group(0))
        else:
            result = json.loads(response_text)
            
        # Validate scores
        for key in ["faithfulness", "relevance", "completeness"]:
            if key not in result:
                result[key] = 0.0
            else:
                result[key] = max(0.0, min(1.0, float(result[key])))
        
        result["judge_skipped"] = False
        result["judge_latency_ms"] = int((time.monotonic() - started_at) * 1000)
        return result

    except Exception as e:
        logger.error("Judge evaluation failed: %s", e)
        return {
            "faithfulness": 0.0,
            "relevance": 0.0,
            "completeness": 0.0,
            "reasoning": f"Judge error: {str(e)}",
            "judge_skipped": True,
            "judge_latency_ms": int((time.monotonic() - started_at) * 1000)
        }
