# Audit Trace Schema

**파일 위치**: `outputs/audit_trace_{ticker}_{area}_{ts}.json`

ESGenie L5 레이어가 생성하는 JSON 파일의 전체 스키마 정의.

---

## 최상위 구조

```json
{
  "ticker":       "005930",
  "corp_name":    "삼성전자",
  "area":         "E",
  "generated_at": "2026-04-27T12:34:56.789012+00:00",
  "sentences":    [ ... ],
  "summary":      { ... }
}
```

| 필드 | 타입 | 설명 |
|------|------|------|
| `ticker` | string | 종목코드 (DART corp_code) |
| `corp_name` | string | 회사명 |
| `area` | string | 분석 영역: `"E"` / `"S"` / `"G"` |
| `generated_at` | ISO8601 string | UTC 생성 시각 |
| `sentences` | AuditSentence[] | 문장 단위 감사 레코드 목록 |
| `summary` | object | 전체 통계 요약 |

---

## AuditSentence

최종 보고서 텍스트를 문장 단위로 분리한 감사 레코드.

```json
{
  "sentence_id":        "005930_E_000",
  "sentence_text":      "온실가스 배출량은 1,670만 tCO2eq으로 전년 대비 2.1% 감소하였다.",
  "kesg_item_id":       "E-3-1",
  "evidence_node_ids":  ["005930_온실가스_2024", "005930_온실가스_2023_inferred"],
  "retrieved_chunk_ids": ["E-3-1", "chunk_2"],
  "risk_vector":        { ... },
  "refinement_attempts": [ ... ],
  "hitl_status":        "ok",
  "timestamps":         { "created": "...", "finalized": "..." },
  "model_versions":     { "llm": "gpt-4o-mini", "embed": "paraphrase-multilingual-MiniLM-L12-v2" }
}
```

| 필드 | 타입 | 설명 |
|------|------|------|
| `sentence_id` | string | `{ticker}_{area}_{idx:03d}` 형식의 고유 ID |
| `sentence_text` | string | 최종 보고서에서 추출한 원문 문장 |
| `kesg_item_id` | string \| null | 매핑된 K-ESG 항목 코드 (예: `"E-3-1"`) |
| `evidence_node_ids` | string[] | L0 EvidenceGraph에서 근거가 되는 노드 ID 목록 |
| `retrieved_chunk_ids` | string[] | L2 RAG에서 검색된 청크 ID 목록 (최대 3개) |
| `risk_vector` | RiskVector \| null | L3 5축 리스크 분해 결과 |
| `refinement_attempts` | RefinementAttempt[] | 이 문장에 영향을 준 L4 재생성 시도 목록 |
| `hitl_status` | `"ok"` \| `"HITL_REQUIRED"` | 사람 검토 필요 여부 |
| `timestamps` | object | `created`, `finalized` ISO8601 타임스탬프 |
| `model_versions` | object | 사용된 LLM / 임베딩 모델 버전 |

---

## RiskVector

L3 레이어의 5축 그린워싱 리스크 분해 결과.

```json
{
  "D1_numeric":     { "score": 0.12, "evidence": ["005930_온실가스_2024"], "detail": "..." },
  "D2_modifier":    { "score": 0.05, "evidence": [], "detail": "..." },
  "D3_semantic":    { "score": 0.18, "evidence": ["E-3-1"], "detail": "..." },
  "D4_industry":    { "score": 0.00, "evidence": [], "detail": "..." },
  "D5_timeseries":  { "score": 0.10, "evidence": ["005930_온실가스_2023_inferred"], "detail": "..." },
  "aggregate": {
    "risk_score": 0.102,
    "level":      "low",
    "top_axis":   "D3_semantic"
  }
}
```

### AxisScore

| 필드 | 타입 | 설명 |
|------|------|------|
| `score` | float [0.0, 1.0] | 축별 리스크 점수 |
| `evidence` | string[] | 점수 근거 노드/청크 ID |
| `detail` | string | 점수 산출 설명 |

### 5축 정의

| 축 | 이름 | 설명 | 데이터 소스 |
|----|------|------|------------|
| D1 | 수치 정확성 | DART 실측 수치와의 상대 오차 | L0 EvidenceGraph |
| D2 | 수식어 과장 | 모호·최상급 수식어 밀도 | 텍스트 패턴 |
| D3 | 의미 일관성 | RAG 청크와의 코사인 유사도 역수 | L2 RAG 벡터 |
| D4 | 업종 이탈 | 업계 평균 대비 표준편차 | benchmarks.json |
| D5 | 시계열 모순 | L0 시계열 엣지와의 방향 일치 여부 | L0 EvidenceEdge |

### Aggregate

| 필드 | 타입 | 설명 |
|------|------|------|
| `risk_score` | float [0.0, 1.0] | D1~D5 가중 평균 종합 점수 |
| `level` | `"low"` \| `"medium"` \| `"high"` | 리스크 등급 (low<0.25, medium<0.50, high≥0.50) |
| `top_axis` | string | 가장 높은 점수의 축 이름 |

---

## RefinementAttempt

L4 재생성 루프의 각 시도 기록.

```json
{
  "attempt_no":           1,
  "constraints_applied":  ["D2_modifier"],
  "before_text":          "세계 최고 수준의 친환경 성과를 달성...",
  "after_text":           "온실가스 배출량을 전년 대비 2.1% 감축...",
  "risk_vector":          { ... },
  "timestamp":            "2026-04-27T12:34:56+00:00"
}
```

| 필드 | 타입 | 설명 |
|------|------|------|
| `attempt_no` | int | 시도 번호 (1-based) |
| `constraints_applied` | string[] | 적용된 제약 축 목록 |
| `before_text` | string | 재생성 전 텍스트 (앞 200자) |
| `after_text` | string | 재생성 후 텍스트 (앞 200자) |
| `risk_vector` | RiskVector \| null | 재생성 후 리스크 벡터 |
| `timestamp` | ISO8601 string | 시도 시각 |

---

## Summary

전체 Audit Trace의 집계 통계.

```json
{
  "total_sentences":   12,
  "hitl_count":         1,
  "avg_risk_score":  0.093,
  "high_risk_axes":  ["D3_semantic", "D1_numeric"],
  "refinement_total":   2,
  "converged":       true
}
```

| 필드 | 타입 | 설명 |
|------|------|------|
| `total_sentences` | int | 전체 감사 문장 수 |
| `hitl_count` | int | HITL_REQUIRED 문장 수 |
| `avg_risk_score` | float | 전체 문장 평균 리스크 점수 |
| `high_risk_axes` | string[] | 빈도 상위 3개 고위험 축 |
| `refinement_total` | int | 전체 재생성 시도 횟수 |
| `converged` | bool | L4 수렴 여부 (false이면 HITL 검토 필요) |

---

## HITL 에스컬레이션 조건

`hitl_status = "HITL_REQUIRED"` 조건:

- L4 재생성 루프가 `MAX_REFINEMENT_ITER` (기본 3회)를 모두 소진했음에도 수렴 실패
- 해당 구역의 첫 번째 문장(idx=0)에 마킹
- `summary.converged = false`

사람 검토자는 해당 문장의 `evidence_node_ids`와 `risk_vector`를 확인하여 원문 수정 여부를 판단한다.

---

## 파일명 규칙

```
outputs/audit_trace_{ticker}_{area}_{YYYYMMDD_HHMMSS}.json
```

예: `outputs/audit_trace_005930_E_20260427_123456.json`
