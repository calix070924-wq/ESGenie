"""Embedding 및 FAISS 래퍼. sentence-transformers가 없으면 TF-IDF 폴백."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

import numpy as np

from .config import SETTINGS


@dataclass
class IndexedDoc:
    text: str
    meta: dict[str, Any]


# 모듈 수준 캐시 — SentenceTransformer·FAISS는 한 번만 로드
_ST_MODEL_CACHE: dict[str, Any] = {}
_FAISS_MODULE: Any = None
_FAISS_LOADED: bool = False


def _get_st_model(model_name: str) -> Any:
    if model_name not in _ST_MODEL_CACHE:
        try:
            from sentence_transformers import SentenceTransformer  # type: ignore
            _ST_MODEL_CACHE[model_name] = SentenceTransformer(model_name)
        except Exception:
            _ST_MODEL_CACHE[model_name] = None
    return _ST_MODEL_CACHE[model_name]


def _get_faiss() -> Any:
    global _FAISS_MODULE, _FAISS_LOADED
    if not _FAISS_LOADED:
        try:
            import faiss  # type: ignore
            _FAISS_MODULE = faiss
        except Exception:
            _FAISS_MODULE = None
        _FAISS_LOADED = True
    return _FAISS_MODULE


class VectorIndex:
    """FAISS 기반 벡터 인덱스 (모델 로딩 실패 시 해시 기반 폴백)."""

    def __init__(self, model_name: str | None = None) -> None:
        self.model_name = model_name or SETTINGS.embed_model
        self._st_model = _get_st_model(self.model_name)
        self._faiss = _get_faiss()
        self._index = None
        self._docs: list[IndexedDoc] = []
        self._vectors: np.ndarray | None = None

    def _embed(self, texts: list[str]) -> np.ndarray:
        if self._st_model is not None:
            emb = self._st_model.encode(texts, convert_to_numpy=True, normalize_embeddings=True)
            return emb.astype("float32")
        return self._fallback_embed(texts)

    def _fallback_embed(self, texts: list[str], dim: int = 256) -> np.ndarray:
        """Hash-based bag-of-characters embedding as an installation-free fallback."""
        vectors = np.zeros((len(texts), dim), dtype="float32")
        for i, text in enumerate(texts):
            tokens = _tokenize(text)
            for tok in tokens:
                h = int(hashlib.md5(tok.encode("utf-8")).hexdigest(), 16) % dim
                vectors[i, h] += 1.0
            norm = np.linalg.norm(vectors[i])
            if norm > 0:
                vectors[i] /= norm
        return vectors

    # ---- public API ---------------------------------------------------
    def build(self, docs: list[IndexedDoc]) -> None:
        self._docs = docs
        texts = [d.text for d in docs]
        self._vectors = self._embed(texts)
        if self._faiss is not None and self._vectors.size > 0:
            d = self._vectors.shape[1]
            self._index = self._faiss.IndexFlatIP(d)
            self._index.add(self._vectors)

    def search(self, query: str, k: int = 3) -> list[tuple[IndexedDoc, float]]:
        if not self._docs or self._vectors is None:
            return []
        qv = self._embed([query])
        if self._index is not None:
            scores, idx = self._index.search(qv, min(k, len(self._docs)))
            return [(self._docs[i], float(scores[0, j])) for j, i in enumerate(idx[0]) if i >= 0]
        sims = (self._vectors @ qv[0])
        order = np.argsort(-sims)[:k]
        return [(self._docs[int(i)], float(sims[int(i)])) for i in order]


def _tokenize(text: str) -> list[str]:
    """Simple Korean-aware tokenizer: 2-gram characters + space-split words."""
    words = [w for w in text.split() if w]
    bigrams = [text[i:i + 2] for i in range(len(text) - 1) if not text[i:i + 2].isspace()]
    return words + bigrams


# ---- 백엔드 가시화 -----------------------------------------------------------
# 폴백은 좋은 설계지만 '조용한 폴백'은 환경별 품질 변동의 원인.
# 어느 백엔드로 도는지 항상 조회 가능하게 노출한다 (로그/UI/audit_trace용).

def embedding_backend() -> str:
    """현재 임베딩 백엔드: 'sbert' | 'hash-fallback'."""
    return "sbert" if _get_st_model(SETTINGS.embed_model) is not None else "hash-fallback"


def faiss_available() -> bool:
    return _get_faiss() is not None


def backend_summary() -> dict[str, Any]:
    """환경 진단용 백엔드 요약."""
    backend = embedding_backend()
    return {
        "embedding_backend": backend,
        "embed_model": SETTINGS.embed_model if backend == "sbert" else "(미설치 — 해시 n-gram 폴백)",
        "faiss": faiss_available(),
        "quality_note": (
            "정상 (SBERT 의미 임베딩)" if backend == "sbert"
            else "주의: sentence-transformers 미설치 — D3 의미검증 품질 저하. "
                 "pip install sentence-transformers faiss-cpu 권장"
        ),
    }
