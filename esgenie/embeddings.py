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
        #: 벡터 한 줄에 대응하는 문서. 임베딩 분할을 쓰면 한 부모 문서가 여러 줄을
        #: 갖는다(같은 IndexedDoc 객체가 반복 등장). 기본값은 _docs와 동일.
        self._row_docs: list[IndexedDoc] = []
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

    def split_documents(self, docs: list[IndexedDoc]) -> list[IndexedDoc]:
        """검색용 조각을 만들고 원문 ID·문자 위치를 보존한다.

        build()에서 자동 적용하지 않는다 — 인덱스를 만드는 쪽이 정할 일이다.
        토큰 수는 현재 모델의 tokenizer로 직접 재서 encode()의 조용한 뒷부분
        잘림을 피한다.

        이미 조각난 문서를 다시 넣어도 부모 추적이 끊기지 않는다. 한도 안에 들어가는
        조각은 id가 그대로 유지되고, `parent_chunk_id`는 조각 자신이 아니라 **원래
        부모**를 계속 가리킨다. 영역 검색 인덱스를 분할한 뒤 `retrieve_for_items`가
        같은 문서를 한 번 더 통과시키기 때문에 필요하다.
        """
        tokenizer = getattr(self._st_model, "tokenizer", None)
        limit = getattr(self._st_model, "max_seq_length", None)
        parents = _assign_chunk_ids([
            IndexedDoc(doc.text, dict(doc.meta), doc.chunk_id) for doc in docs
        ])
        result: list[IndexedDoc] = []
        for doc in parents:
            spans = _embedding_text_spans(doc.text, tokenizer, limit)
            root_id = doc.meta.get("parent_chunk_id") or doc.chunk_id
            # 이미 조각인 문서를 다시 나눌 때 문자 위치가 조각 기준으로 덮어써지면
            # 부모 원문에서 어디였는지 잃는다. 조각의 기존 시작 위치를 더해 둔다.
            base = doc.meta.get("char_start") or 0 if doc.meta.get("parent_chunk_id") else 0
            for part, (start, end) in enumerate(spans):
                chunk_id = doc.chunk_id if len(spans) == 1 else f"{doc.chunk_id}__part_{part:04d}"
                meta = dict(doc.meta)
                meta.update({
                    "id": chunk_id,
                    "parent_chunk_id": root_id,
                    # 원본 PDF의 좌표가 아니라 부모 IndexedDoc.text의 문자 위치다.
                    "char_start": base + start,
                    "char_end": base + end,
                })
                result.append(IndexedDoc(doc.text[start:end], meta, chunk_id))
        return result

    # ---- public API ---------------------------------------------------
    def build(self, docs: list[IndexedDoc], *, embedding_split: bool = False) -> None:
        """문서를 임베딩해 인덱스를 만든다.

        `embedding_split=True`면 토큰 한도를 넘는 문서를 조각내 **조각마다** 벡터를
        만들고, 검색 결과로는 **부모 문서를 그대로** 돌려준다. 모델의 max_seq_length가
        128이어서 encode()가 긴 문서의 뒷부분을 조용히 버리는 문제만 없애고, 청크 id·
        본문·게이트가 보는 근거 단위는 종전과 같게 유지한다. 인덱스에 담기는 문서
        집합(`_docs`)도 바뀌지 않으므로 BM25·감사 기록·원장 대조는 영향이 없다.
        """
        self._docs = _assign_chunk_ids(docs)
        if embedding_split:
            parts = self.split_documents(self._docs)
            by_id = {doc.chunk_id: doc for doc in self._docs}
            self._row_docs = [by_id[p.meta["parent_chunk_id"]] for p in parts]
            texts = [p.text for p in parts]
        else:
            self._row_docs = self._docs
            texts = [d.text for d in self._docs]
        self._vectors = self._embed(texts)
        if self._faiss is not None and self._vectors.size > 0:
            d = self._vectors.shape[1]
            self._index = self._faiss.IndexFlatIP(d)
            self._index.add(self._vectors)

    def search(self, query: str, k: int = 3) -> list[tuple[IndexedDoc, float]]:
        if not self._docs or self._vectors is None:
            return []
        rows = self._row_docs or self._docs
        qv = self._embed([query])
        if len(rows) == len(self._docs):
            if self._index is not None:
                scores, idx = self._index.search(qv, min(k, len(self._docs)))
                return [(self._docs[i], float(scores[0, j])) for j, i in enumerate(idx[0]) if i >= 0]
            sims = (self._vectors @ qv[0])
            order = np.argsort(-sims)[:k]
            return [(self._docs[int(i)], float(sims[int(i)])) for i in order]
        # 한 부모가 여러 줄을 갖는 경우: 같은 문서가 상위 k를 중복 점유하지 않도록
        # 문서별 최고 점수만 남긴다. faiss에 k를 늘려 요청하면 중복 제거 후 k를 못
        # 채울 수 있으므로 전체를 훑는다(수천 줄 규모에서 비용이 문제되지 않는다).
        sims = (self._vectors @ qv[0])
        best: dict[str, tuple[int, float]] = {}
        for i, score in enumerate(sims):
            doc = rows[i]
            current = best.get(doc.chunk_id)
            if current is None or score > current[1]:
                best[doc.chunk_id] = (i, float(score))
        ranked = sorted(best.values(), key=lambda pair: -pair[1])[:k]
        return [(rows[i], score) for i, score in ranked]


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


def _embedding_text_spans(text: str, tokenizer: Any, limit: int | None) -> list[tuple[int, int]]:
    """모든 문자를 덮는 겹친 구간. 분할 결과도 특수 토큰을 포함해 한도 검증."""
    if tokenizer is None or not limit:
        return [(0, len(text))]

    def fits(value: str) -> bool:
        return len(tokenizer(value, add_special_tokens=True, truncation=False)["input_ids"]) <= limit

    if fits(text):
        return [(0, len(text))]
    spans: list[tuple[int, int]] = []
    start = 0
    while start < len(text):
        low, high = start + 1, len(text)
        end = start
        while low <= high:
            middle = (low + high) // 2
            if fits(text[start:middle]):
                end = middle
                low = middle + 1
            else:
                high = middle - 1
        if end == start:
            raise ValueError("Embedding token limit cannot fit one input character")
        # 문장·줄·단어 경계가 가까우면 그곳에서 자른다. 토큰 한도는 다시 확인한다.
        boundaries = list(re.finditer(r"[.!?。]\s+|\n+|\s+", text[start:end]))
        if end < len(text) and boundaries:
            boundary = start + boundaries[-1].end()
            if boundary > start + (end - start) // 2 and fits(text[start:boundary]):
                end = boundary
        spans.append((start, end))
        if end == len(text):
            break
        # 짧은 겹침으로 경계의 수치·단위를 함께 찾을 여지를 남긴다.
        # 문자 수를 명시해 토큰 수와 혼동하지 않으며 항상 전진한다.
        overlap_chars = min(48, (end - start) // 4)
        start = end - overlap_chars
    return spans


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
