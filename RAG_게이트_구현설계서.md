# RAG 게이트 구현 설계서

작성일: 2026-06-23
짝 문서(개념): `RAG_검색_근거게이트_설계.md` — *왜/무엇*을 다룸
이 문서: *어디에·어떻게* — 실제 파일·함수·데이터구조·config·테스트 수준 매핑
관련 OCR 게이트: `OCR_표복원_검증게이트_설계.md` (동일 "게이트 사다리" 철학, 메타데이터 승계)

---

## 0. 한 줄 요약

개념 설계는 끝났다. 게이트 A(검색 게이트)는 **0% 미구현**, 게이트 B(근거 게이트)는 `verify_and_refine`의 5축 루프로 **~40% 부분 구현**, 추적 메타데이터는 `AuditSentence`에 **~30% 골격만** 있다. 이 문서는 그 격차를 PR 단위 구현 작업으로 분해한다.

---

## 1. 현황 — 코드 vs 개념설계 (정밀 진단)

### 1.1 이미 있는 것 ✅

| 구성요소 | 위치 | 비고 |
|---|---|---|
| 멀티 인덱스 검색 | `layer2_rag.py::HybridRAG.retrieve()` | kesg/industry/corp 3개 인덱스 병렬 top-k |
| search_terms 쿼리 확장 | `layer2_rag.py::_expand_query_with_search_terms()` | 개념설계 Q6 = **주입되고 있음** |
| 임베딩 백엔드 가시화 | `embeddings.py::backend_summary()` | sbert / hash-fallback 노출 |
| 청크 메타에 출처·페이지 | `ssot_pipeline.py::build_rag_with_ssot()` (L258·L281) | corp 청크에 `source_file`, `page` 실림 |
| 5축 자가검증 루프 | `layer4_verify.py::verify_and_refine()` | D1수치·D2수식어·D3의미·D5시계열 + 최대 3회 재생성 → `HITL_REQUIRED` |
| 청크 ID 사후 매칭 | `layer5_audit_trace.py::_match_chunk_ids()` | 문장↔청크 유사도로 `retrieved_chunk_ids` 채움 |
| 추적 스키마 골격 | `schemas.py::AuditSentence` | `retrieved_chunk_ids`, `evidence_node_ids` 필드 존재 |

### 1.2 빠진 것 (= 이번 작업 범위)

**게이트 A — 검색 게이트 (생성 *전*): 0%**
- `retrieve()`가 점수 `(doc, score)`를 받지만 **임계치 판정이 없다.** 무조건 top-k를 생성으로 넘김.
- R1(top-1 점수)·R2(마진)·R3(필드 커버리지)·R4(방식 합의) 전부 없음.
- 캐스케이드 티어 없음 — 단일 임베딩 한 방(SBERT, 미설치 시 해시 폴백). BM25·하이브리드·리랭커·에이전트형 부재.

**게이트 B — 근거 게이트 (생성 *후*): ~40%**
- 있는 것: D1(클레임 수치 검증), D3(생성↔청크 코사인 유사도), 재생성→HITL.
- **빠진 핵심:**
  - **G1 인용 강제** — 생성 답에 청크 ID가 안 달린다. L5에서 *사후* 유사도로 추정할 뿐.
  - **G2 숫자 청크 원문 대조** — D1은 DART *구조화 수치*와 비교지, "인용 청크 텍스트에 그 숫자가 글자 그대로 있나"가 아니다. 개념설계가 "가성비 1위"로 꼽은 그것.
  - **G4 단위/기간 일치** — 명시 검증 없음 (`unit` 필드는 스키마에 있으나 게이트에서 안 씀).
  - **G5 적절 기권** — `HITL_REQUIRED`는 위험점수 기반이지 "검색 실패인데 단정"을 못 잡음 (게이트 A가 없어서).

**추적 메타데이터: ~30%**
- `retrieved_chunk_ids`는 있으나 `retrieval_tier`·`retrieval_scores`·`grounding_status`가 없다.
- 청크 ID가 **안정적이지 않다** — `search()`가 `(IndexedDoc, score)`만 반환, 전역 고유 ID 부재.

**검색 평가셋: 0%**
- `benchmark.py`는 그린워싱 탐지기 precision/recall이지 검색 Recall@k/MRR이 아니다. 개념설계 7절이 "없으면 1순위"라 한 그것 그대로 비어있음.

---

## 2. 구현 아키텍처 — 어디에 무엇을 넣나

신규 모듈은 layer2(검색)와 layer4(생성·검증) 사이에 끼운다. 기존 `verify_and_refine`를 깨지 않고 게이트를 *주입*하는 방향(시그니처 하위호환 유지, 신규 인자 default).

```
신규/수정 파일
esgenie/
├─ embeddings.py          [수정] BM25 인덱스 + chunk_id 안정화 + search가 점수정규화 반환
├─ rag_gates/             [신규 패키지]
│   ├─ __init__.py
│   ├─ retrieval_gate.py   ← 게이트 A (R1·R2·R3·R4 + 판정 정책)
│   ├─ grounding_gate.py   ← 게이트 B (G1·G2·G4·G5 + 판정 정책)
│   ├─ cascade.py          ← Tier 0~4 캐스케이드 오케스트레이터
│   └─ signals.py          ← 공용 신호 계산기(숫자추출/단위추출/커버리지)
├─ layer2_rag.py          [수정] retrieve()가 RAGContext에 점수/티어/chunk_id 싣기
├─ layer4_verify.py       [수정] 생성 후 grounding_gate 호출, 결과를 step에 부착
├─ schemas.py             [수정] GroundingResult / RetrievalDecision 추가, AuditSentence 확장
├─ config.py              [수정] 게이트 임계치 키 추가
└─ eval/                  [신규] 검색·근거 평가셋 + Recall@k/MRR/faithfulness 러너
tests/
├─ test_retrieval_gate.py [신규]
├─ test_grounding_gate.py [신규]
├─ test_rag_cascade.py    [신규]
└─ test_rag_eval.py       [신규]
```

판정 결과 enum은 OCR 게이트와 동일하게 셋: `ACCEPT` / `ESCALATE` / `HUMAN`. 가능하면 OCR 게이트의 결정 타입을 공용 모듈로 빼서 재사용한다.

---

## 3. 데이터 구조 (schemas.py 추가분)

```python
from enum import Enum

class GateDecision(str, Enum):
    ACCEPT = "accept"
    ESCALATE = "escalate"
    HUMAN = "human"

@dataclass
class RetrievalDecision:
    """게이트 A 결과."""
    decision: GateDecision
    tier: int                       # 통과한 티어 (0~3), 실패 시 4
    top1_score: float               # R1
    score_margin: float             # R2
    field_coverage: dict[str, bool] # R3: {"value":T,"unit":F,"period":T,"source":T}
    method_overlap: float           # R4: BM25∩임베딩 비율
    hard_fails: list[str]
    soft_flags: list[str]
    chunk_ids: list[str]            # 통과 시 생성에 넘길 청크 ID
    scores: list[float]             # 대응 점수 (추적용)

@dataclass
class GroundingResult:
    """게이트 B 결과 (문장 단위 집계)."""
    decision: GateDecision
    g1_uncited_sentences: list[str] # 인용 없는 문장
    g2_orphan_numbers: list[str]    # 청크에 없는 숫자 (환각)
    g4_unit_mismatches: list[str]   # 단위/기간 불일치
    g5_overclaim: bool              # 근거 없는데 단정
    hard_fails: list[str]
    soft_flags: list[str]
    faithfulness: float             # 0~1 (인용 청크가 주장 뒷받침하는 비율)
```

`AuditSentence` 확장 (기존 필드 유지, 추가만):
```python
retrieval_tier: int | None = None
retrieval_scores: list[float] = field(default_factory=list)
grounding_status: str = "unknown"   # "grounded" | "partial" | "ungrounded"
```

`IndexedDoc`에 안정적 ID 추가:
```python
@dataclass
class IndexedDoc:
    text: str
    meta: dict[str, Any]
    chunk_id: str = ""   # 빌드 시 "{source}_{page}_{idx}" 형식으로 부여
```
→ `build()`에서 `chunk_id` 미지정 시 자동 부여. 이게 G1/추적의 전제. 청크 메타의 `source_file`·`page`(이미 있음)를 ID에 녹여 OCR이 단 좌표를 RAG가 그대로 승계.

---

## 4. 게이트 A — 검색 게이트 (`rag_gates/retrieval_gate.py`)

### 4.1 신호 계산 (모두 점수만으로 — 공짜~쌈)

```python
def evaluate_retrieval(query, kesg_hits, corp_hits, *, item=None) -> RetrievalDecision:
    # R1: top-1 정규화 점수
    top1 = corp_hits[0][1] if corp_hits else 0.0
    # R2: top-1 − top-k 꼬리 마진
    margin = top1 - (corp_hits[-1][1] if len(corp_hits) > 1 else 0.0)
    # R3: 필드 커버리지 — item.unit 있으면 청크에 단위 문자열 존재해야
    coverage = _field_coverage(corp_hits, item)   # signals.py
    # R4: BM25 top-k ∩ 임베딩 top-k 비율
    overlap = _method_overlap(bm25_hits, emb_hits) # signals.py
    ...
```

- **R1** `top1 < RAG_R1_MIN(0.6)` → hard fail
- **R3** `item.unit`이 있는데 어느 청크에도 단위 정규식 매칭 없음 → hard fail. search_terms 핵심어 미커버 → soft.
- **R2** `margin < RAG_R2_MIN(0.1)` → soft. **R4** `overlap < RAG_R4_MIN(0.2)` → soft.

### 4.2 판정 정책 (개념설계 3.2 그대로 코드화)
```
hard ≥ 1                  → ESCALATE (다음 티어)
hard == 0 and soft >= 2   → ESCALATE
hard == 0 and soft <= 1   → ACCEPT
Tier 3까지 ESCALATE        → HUMAN ("근거 미발견")
```

### 4.3 캐스케이드 (`rag_gates/cascade.py`)

| Tier | 방식 | 의존 | 신규 작업 |
|---|---|---|---|
| 0 | BM25 + search_terms | `rank_bm25` (경량) | embeddings.py에 BM25 인덱스 추가 |
| 1 | 하이브리드(BM25+임베딩, RRF 결합) | 기존 VectorIndex | RRF 결합 함수 |
| 2 | 질의확장(multi-query/HyDE) + cross-encoder 리랭크 | `sentence-transformers` CrossEncoder | 리랭커 래퍼, 미설치 시 Tier1로 폴백 |
| 3 | 에이전트형 반복검색 (LLM이 후속 질의 생성) | 기존 `CLIENT` | 반복 루프, 상한 N회 |
| 4 | 근거 없음 / HUMAN | — | 사유에 시도한 search_terms·티어 기록 |

각 티어 끝에서 `evaluate_retrieval` 호출 → ACCEPT면 멈춤. **속도 최적화(쉬운 항목 티어 스킵)는 정확도 안정화 이후 별도 트랙** (개념설계 9절 단서 준수).

---

## 5. 게이트 B — 근거 게이트 (`rag_gates/grounding_gate.py`)

생성 직후, `verify_and_refine` 루프 안에서 호출. 기존 5축 detect와 **병행**(중복 아님 — 5축은 위험도, 게이트 B는 근거충실성).

### 5.1 신호

```python
def evaluate_grounding(answer_text, cited_chunks, *, item=None) -> GroundingResult:
    sents = _split_sentences(answer_text)
    # G1: 인용 강제 — 각 주장 문장에 [chunk_id] 마커 있나
    uncited = [s for s in sents if _has_claim(s) and not _has_citation(s)]
    # G2: 숫자 원문 대조 (가성비 1위) — 답의 모든 숫자가 인용 청크에 글자 그대로?
    orphans = [n for n in _extract_numbers(answer_text)
               if not _number_in_any_chunk(n, cited_chunks)]
    # G4: 단위/기간 — item.unit, 연도가 인용 청크와 일치?
    unit_mm = _unit_period_mismatch(answer_text, cited_chunks, item)
    # G5: 검색 게이트 실패였는데 단정적이면 fail
    ...
```

- **G1** 인용 없는 주장 문장 존재 → hard
- **G2** 청크 밖 숫자 존재 → hard (즉시 fail, 문자열 매칭이라 쌈·결정적)
- **G5** 게이트 A가 ESCALATE/HUMAN인데 답이 단정 → hard
- **G3** 함의(entailment) → 회색지대에서만 LLM 심판(`layer3_judge` 재활용) → soft
- **G4** 단위/기간 불일치 → soft

> 핵심 대응: OCR의 **합계 검증** ↔ RAG의 **G2 숫자 출처 대조**. 둘 다 싸고 결정적인 ESG 특화 게이트.

### 5.2 판정 + 재생성
```
hard >= 1   → 재생성 1회 ("인용 청크 밖 정보·숫자 절대 금지" 강조)
              재생성도 hard → 검색 티어 ESCALATE 또는 HUMAN
hard 0, soft >= 2 → G3(LLM 심판) → 통과 시 ACCEPT
hard 0, soft <= 1 → ACCEPT (답변 확정 + 증빙 링크)
```

### 5.3 생성 프롬프트 변경 (G1 전제)
`layer2_rag.generate_section`의 user 프롬프트에 추가:
- 검색 컨텍스트를 `[chunk_id] 텍스트` 형식으로 넘기고,
- "모든 수치·주장 문장 끝에 근거 `[chunk_id]`를 반드시 표기" 지시.
- `as_context_text()`가 chunk_id를 함께 출력하도록 수정.

---

## 6. config.py 추가 키 (환경변수 오버라이드, 평가셋으로 보정)

```python
RAG_R1_MIN   = float(os.getenv("RAG_R1_MIN", "0.6"))   # top-1 절대 임계
RAG_R2_MIN   = float(os.getenv("RAG_R2_MIN", "0.1"))   # 점수 마진
RAG_R4_MIN   = float(os.getenv("RAG_R4_MIN", "0.2"))   # 방식 합의
RAG_MAX_TIER = int(os.getenv("RAG_MAX_TIER", "2"))     # 본선 시연은 2까지(속도)
GROUNDING_REGEN_MAX = int(os.getenv("GROUNDING_REGEN_MAX", "1"))
```
초기값은 개념설계 제안치. **반드시 평가셋(7절)으로 재보정** — 지금 값은 가설일 뿐.

---

## 7. 평가 — "384 passed"와 다른 축 (`eval/`)

단위테스트 통과 ≠ 검색 정확. 별도 평가셋 필요.

**검색 평가셋** `eval/retrieval_qrels.jsonl`
```jsonc
{"query_id":"E-1-1", "query":"온실가스 Scope1 배출량",
 "relevant_chunk_ids":["hanwool_env_p12_3","hanwool_env_p12_4"]}
```
측정: **Recall@k**(정답 청크가 top-k에 드는 비율), **MRR**(정답 순위 역수 평균). 러너 `eval/run_retrieval_eval.py`.

**근거 평가셋** `eval/grounding_labels.jsonl`
```jsonc
{"answer":"...", "cited_chunks":[...], "faithful":true, "hallucinated_numbers":[]}
```
측정: faithfulness, 환각률, 적절 기권율.

데이터 소스: 이미 만든 **한울정밀 가상 증빙 7종**(시연 증빙세트)으로 30~50개 질의-정답 라벨을 시드. D6 그린워싱 모순 케이스는 게이트가 잡아야 할 **negative 라벨**로 투입.

이 평가셋이 6절 임계치를 보정하는 근거가 된다. → **OCR 평가셋 구축과 한 트랙으로 진행.**

---

## 8. PR 분해 (개념설계 9절 → 실행 단위)

각 PR은 feature branch에서 작업 후 푸시 (프로젝트 워크플로우 규칙).

| PR | 범위 | 핵심 파일 | 신규 테스트 | 가성비 |
|---|---|---|---|---|
| **PR-A** | 게이트 B의 **G1 인용강제 + G2 숫자대조** | `grounding_gate.py`, `signals.py`, `layer2_rag.generate_section`(프롬프트), `layer4_verify`(훅) | `test_grounding_gate.py` | ★★★ (검색 안 건드리고 환각 즉시↓) |
| **PR-B** | 게이트 A의 **R1+R3** (못 찾으면 기권) | `retrieval_gate.py`, `layer2_rag.retrieve`(점수/커버리지) | `test_retrieval_gate.py` | ★★★ |
| **PR-C** | **평가셋 구축 + 임계치 보정** | `eval/`, qrels/labels (한울정밀 시드) | `test_rag_eval.py` | ★★ (이후 모든 보정의 근거) |
| **PR-D** | **Tier0 BM25 + Tier1 하이브리드 + Tier2 리랭크** | `embeddings.py`(BM25), `cascade.py` | `test_rag_cascade.py` | ★★ (재현율 본격↑) |
| **PR-E** | **Tier3 에이전트형 + 추적 메타 승계** | `cascade.py`, `schemas.AuditSentence`, `layer5` | 확장 | ★ |

> **PR-A → PR-B → PR-C** 순이 본선 시연용 최소 셋. D6 모순을 "환각/근거없음"으로 자동 차단하는 데모가 가능해진다(공급망 실사 산출물 신뢰성의 핵심 셀링포인트).
> 속도 최적화(임베딩 캐싱, 쉬운 항목 티어 스킵)는 PR-C 이후 별도.

---

## 9. 하위호환·리스크

- `verify_and_refine` 시그니처는 유지하고 `grounding_gate=None` default로 주입 → 기존 384 테스트 불변.
- 리랭커·BM25 라이브러리 미설치 환경: `backend_summary()` 패턴대로 **조용한 폴백 금지**, 어느 티어로 돌았는지 항상 노출.
- 게이트가 과하게 ESCALATE하면 시연 중 "근거없음" 남발 위험 → PR-C 보정 전엔 임계치 보수적으로(R1_MIN 낮춰) 시작하고 평가셋으로 조인다.

---

## 10. 다음 액션

1. PR-A부터 시작할지 결정 → feature branch `feature/rag-grounding-gate` 생성
2. 한울정밀 증빙 7종으로 평가셋 30~50건 시드 (PR-C 선행 가능 — 라벨이 PR-A 테스트도 먹임)
3. OCR 평가셋과 묶어 임계치 보정 트랙 합치기
