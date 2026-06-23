"""Embedding 및 FAISS/BM25 래퍼."""
from __future__ import annotations

import hashlib
import math
import re
from collections import Counter
from dataclasses import dataclass
from typing import Any

import numpy as np

from .config import SETTINGS


@dataclass
class IndexedDoc:
    text: str
    meta: dict[str, Any]
    chunk_id: str = ""


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
        self._docs = _assign_chunk_ids(docs)
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


class BM25Index:
    """경량 BM25 인덱스."""

    def __init__(self, *, k1: float = 1.5, b: float = 0.75) -> None:
        self.k1 = k1
        self.b = b
        self._docs: list[IndexedDoc] = []
        self._doc_tokens: list[list[str]] = []
        self._doc_term_freqs: list[Counter[str]] = []
        self._idf: dict[str, float] = {}
        self._avgdl = 0.0

    def build(self, docs: list[IndexedDoc]) -> None:
        self._docs = _assign_chunk_ids(docs)
        self._doc_tokens = [_tokenize(doc.text) for doc in self._docs]
        self._doc_term_freqs = [Counter(tokens) for tokens in self._doc_tokens]
        self._avgdl = (
            sum(len(tokens) for tokens in self._doc_tokens) / len(self._doc_tokens)
            if self._doc_tokens else 0.0
        )
        df: Counter[str] = Counter()
        for tokens in self._doc_tokens:
            df.update(set(tokens))
        n_docs = len(self._doc_tokens)
        self._idf = {
            term: math.log(1.0 + (n_docs - freq + 0.5) / (freq + 0.5))
            for term, freq in df.items()
        }

    def search(self, query: str, k: int = 3) -> list[tuple[IndexedDoc, float]]:
        if not self._docs:
            return []
        q_tokens = _tokenize(query)
        if not q_tokens:
            return []
        scores = np.zeros(len(self._docs), dtype="float32")
        for idx, tf in enumerate(self._doc_term_freqs):
            dl = max(1, len(self._doc_tokens[idx]))
            score = 0.0
            for term in q_tokens:
                freq = tf.get(term, 0)
                if freq == 0:
                    continue
                idf = self._idf.get(term, 0.0)
                denom = freq + self.k1 * (1 - self.b + self.b * dl / max(self._avgdl, 1.0))
                score += idf * (freq * (self.k1 + 1)) / max(denom, 1e-9)
            scores[idx] = score
        order = np.argsort(-scores)[:k]
        return [
            (self._docs[int(i)], float(scores[int(i)]))
            for i in order
            if scores[int(i)] > 0
        ]


def _tokenize(text: str) -> list[str]:
    """Simple Korean-aware tokenizer: 2-gram characters + space-split words."""
    words = [w for w in text.split() if w]
    bigrams = [text[i:i + 2] for i in range(len(text) - 1) if not text[i:i + 2].isspace()]
    return words + bigrams


def _assign_chunk_ids(docs: list[IndexedDoc]) -> list[IndexedDoc]:
    seen: dict[str, int] = {}
    out: list[IndexedDoc] = []
    for idx, doc in enumerate(docs):
        base = doc.chunk_id or _chunk_id_from_meta(doc.meta, idx)
        suffix = seen.get(base, 0)
        seen[base] = suffix + 1
        chunk_id = base if suffix == 0 else f"{base}_{suffix}"
        doc.chunk_id = chunk_id
        doc.meta.setdefault("id", chunk_id)
        out.append(doc)
    return out


def _chunk_id_from_meta(meta: dict[str, Any], idx: int) -> str:
    node_id = str(meta.get("node_id") or "").strip()
    if node_id:
        return _slug(node_id)

    source = _slug(meta.get("source") or "chunk")
    corp_code = _slug(meta.get("corp_code") or "")
    code = _slug(meta.get("code") or meta.get("kesg_code") or "")
    source_file = _slug(meta.get("source_file") or "")
    page = _slug(meta.get("page") or meta.get("report_year") or "")
    parts = [p for p in (source, corp_code, code, source_file, page, str(idx)) if p]
    return "_".join(parts) or f"chunk_{idx}"


def _slug(value: Any) -> str:
    text = str(value).strip()
    if not text:
        return ""
    text = text.replace("/", "_").replace("\\", "_")
    text = re.sub(r"[^0-9A-Za-z가-힣._-]+", "_", text)
    text = re.sub(r"_+", "_", text).strip("._")
    return text.lower()


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
