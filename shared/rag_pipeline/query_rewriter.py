"""
Conversation-aware query reformulation for RAG (RAGCONV-001).
Detects follow-up queries and rewrites them using conversation history.
"""
import re
import logging
from typing import List, Dict, Optional, Tuple
from shared.ai_providers import ai_manager

logger = logging.getLogger(__name__)

# §4.2 Heuristic Detection Patterns
FOLLOW_UP_PRONOUNS_RU = [
    "тот", "та", "то", "те", "это", "этот", "эта", "эти",
    "там", "туда", "же", "тоже", "также", "вместо",
    "другой", "другая", "другое", "другие", "ещё", "еще",
]
FOLLOW_UP_PHRASES_RU = [
    "а что насчет", "а как насчет", "а если", "как насчет", "что насчет",
]
FOLLOW_UP_PRONOUNS_EN = [
    "that", "this", "it", "those", "the same", "another",
    "instead", "there", "which"
]
FOLLOW_UP_PHRASES_EN = [
    "what about", "how about",
]

REFORMULATION_PROMPT = """Given the conversation:
{history}
Rewrite only the last user question as a self-contained search query.
Output only the rewritten query, no explanation.
"""

def is_follow_up(query: str, history: List[Dict[str, str]]) -> bool:
    """
    Detect if the current query is a follow-up based on history and heuristics.
    Heuristics from §4.2:
    - short query (< 6 non-stopword tokens)
    - contains follow-up pronouns/phrases
    """
    if not history:
        return False
    
    query_lower = query.lower()
    
    # 1. Check for pronouns/phrases
    all_patterns = FOLLOW_UP_PRONOUNS_RU + FOLLOW_UP_PHRASES_RU + FOLLOW_UP_PRONOUNS_EN + FOLLOW_UP_PHRASES_EN
    for pattern in all_patterns:
        # Use word boundaries for pronouns
        if " " in pattern:
            if pattern in query_lower:
                return True
        else:
            if re.search(rf"\b{pattern}\b", query_lower):
                return True
                
    # 2. Check for short query (< 6 non-stopword tokens)
    # Simple tokenization by word
    tokens = re.findall(r"\w+", query_lower)
    # We don't have the full stopword list here but common ones are enough for heuristic
    common_stopwords = {"и", "в", "не", "на", "что", "как", "это", "a", "the", "is", "in", "to"}
    content_tokens = [t for t in tokens if t not in common_stopwords]
    
    if len(content_tokens) < 6:
        return True
        
    return False

def reformulate_query(
    query: str, 
    history: List[Dict[str, str]], 
    model: Optional[str] = None,
    provider: Optional[str] = None
) -> Tuple[str, bool, int]:
    """
    Reformulates query if follow-up detected.
    Returns (reformulated_query, applied_flag, turns_used).
    """
    if not history or not is_follow_up(query, history):
        return query, False, 0
        
    # Take last 3 turns (up to assistant response)
    # history expected as list of {"role": "user"|"assistant", "text": "..."}
    # Current query is already in history as last item? 
    # Usually caller passes history EXCLUDING current query, or INCLUDING.
    # Design says: "last 2 prior turns".
    
    prior_turns = history[-2:] # Last turn pair
    history_text = ""
    for turn in prior_turns:
        role = "User" if turn["role"] == "user" else "Assistant"
        text = turn["text"][:400] # Truncate per §4.2
        history_text += f"{role}: {text}\n"
    history_text += f"User: {query}\n"
    
    prompt = REFORMULATION_PROMPT.format(history=history_text)
    
    try:
        rewritten = ai_manager.query(
            prompt,
            model=model,
            provider_name=provider,
            max_tokens=60,
            telemetry_meta={"feature": "query_rewriter"}
        ).strip().strip('"')
        
        if rewritten and len(rewritten) > 2:
            logger.info("Reformulated query: %r -> %r", query, rewritten)
            return rewritten, True, len(prior_turns)
    except Exception as e:
        logger.warning("Query reformulation failed: %s", e)
        
    return query, False, 0
