import json
import threading

_model = None
_available = None
_load_lock = threading.Lock()


def is_available() -> bool:
    global _available
    if _available is None:
        try:
            import sentence_transformers
            import numpy
            _available = True
        except ImportError:
            _available = False
    return _available


def get_model():
    global _model
    if _model is None:
        with _load_lock:
            if _model is None:
                from sentence_transformers import SentenceTransformer
                _model = SentenceTransformer("all-MiniLM-L6-v2")
    return _model


def generate_embedding(text: str) -> str | None:
    if not is_available():
        return None
    try:
        import numpy as np
        model = get_model()
        emb = model.encode(text)
        if isinstance(emb, np.ndarray):
            emb = emb.tolist()
        return json.dumps(emb)
    except Exception:
        return None


def cosine_similarity(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def parse_embedding(emb_str: str | None) -> list[float] | None:
    if not emb_str:
        return None
    try:
        return json.loads(emb_str)
    except (json.JSONDecodeError, TypeError):
        return None
