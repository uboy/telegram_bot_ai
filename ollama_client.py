import requests
from config import OLLAMA_BASE_URL, OLLAMA_MODEL

def query_ollama(prompt: str) -> str:
    resp = requests.post(
        f"{OLLAMA_BASE_URL}/api/generate",
        json={"model": OLLAMA_MODEL, "prompt": prompt, "stream": False}
    )
    if resp.status_code == 200:
        return resp.json().get("response", "").strip()
    return "Ошибка при обращении к Ollama."
