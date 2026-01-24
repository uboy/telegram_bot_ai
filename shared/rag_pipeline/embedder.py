from typing import List, Optional, Any


def embed_texts(texts: List[str], encoder: Optional[Any] = None) -> List[Optional[List[float]]]:
    """
    Placeholder embedder. Uses provided encoder if available.
    Returns a list aligned with input texts.
    """
    if not encoder:
        return [None for _ in texts]

    vectors = encoder.encode(texts, convert_to_numpy=False)
    if isinstance(vectors, list):
        return [list(v) for v in vectors]
    return [list(vectors)]
