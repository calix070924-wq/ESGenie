# ESGenie v15 상세 설계 문서
### DART + 내부 증빙 OCR 통합 → 대기업 공급망 실사 데이터 출력

> **버전 목표(MVP):** 기존 v10의 고도화 기술(Evidence Graph · 5축 위험 분해 · Audit Trace) **뼈대를 100% 유지**하되, 입력단(L0)과 출력단(L5)의 범위를 중소·중견기업 현실에 맞게 확장한다.
>
> - **L0 확장:** DART 단일 입력 → `DART + 내부 증빙 문서 OCR(하이브리드 라우팅)`
> - **L5 확장:** 150쪽 줄글 PDF → `대기업 실사 시스템용 정량 엑셀 + 증빙 서류철(하드링크)`
> - **신규 트랙:** 사내 규정집 검증(누락 조항 인라인 지적) + 부족 문구 자동 보완

---

## 0. 한눈에 보는 전체 워크플로우

```
[중소기업 담당자]
   │ ① 회사정보 + ② 증빙 업로드(전기요금/폐기물대장/회의록/규정집)
   ▼
┌──────────────────────────────────────────────────────────────────────┐
│ L0-A  OCR 하이브리드 라우터 (ocr_router.py)                            │
│   route_document() → 정형(전통OCR+LLM후처리) | 비정형(VLM우선) 자동분기 │
│   extract_document() → OcrExtraction(metrics[], clauses[])             │
└──────────────────────────────────────────────────────────────────────┘
   │            ▲
   │    DART JSON (선택)
   ▼            │
┌──────────────────────────────────────────────────────────────────────┐
│ L0   통합 Evidence Graph (evidence_graph.py) = 단일 진실 원천(SSOT)    │
│   build_unified_graph(dart, [extractions])                            │
│   · EvidenceNode(origin: dart|ocr_structured|ocr_unstructured)        │
│   · 전력/가스 → tCO2eq 파생 노드 자동 생성                              │
│   · DART↔OCR 동일항목 = cross_check 엣지                                │
│   · 정성 조항 → TextNode (규정 검증용)                                  │
└──────────────────────────────────────────────────────────────────────┘
   ▼
┌──────────────────────────────────────────────────────────────────────┐
│ L1~L2  (기존 유지) K-ESG 61항목 매핑 + Hybrid RAG                      │
│ L3   5축 위험 분해 (detector_5axis.py)                                 │
│   · D1 수치 일치성 — 증빙 노드 + cross_check 기반으로 강화             │
│   · D2~D5 (기존) + ★P축 사내규정 검증(LLM gap detection)               │
│ L4   (기존 유지) 제약 재생성 루프 → 수렴/HITL                          │
└──────────────────────────────────────────────────────────────────────┘
   ▼
┌──────────────────────────────────────────────────────────────────────┐
│ L5   대기업 실사 출력 (audit_trace.py + excel_exporter.py)            │
│   · audit_trace_v15.json  (data_points + policy_audit + sentences)    │
│   · ESG_DataSheet_대기업제출용.xlsx (값+증빙파일 하이퍼링크)           │
│   · evidence_pack/ (원본 증빙 복사본 = 셀 하드링크 타깃)               │
└──────────────────────────────────────────────────────────────────────┘
   ▼
[대기업 공급망 실사 시스템에 복사·붙여넣기 / 외부 감사 서류철 제출]
```

---

## 1. 방향성 4 — L0-A: OCR 하이브리드 라우팅 설계

### 1.1 라우팅 판단 논리

중소기업 증빙은 성격이 둘로 갈린다. **비용 효율과 정확도를 동시에** 잡기 위해 채널을 자동 분기한다.

| 채널 | 대상 문서 | 처리 엔진 | 강점 |
|------|-----------|-----------|------|
| **정형(structured)** | 한전 전기요금 고지서, 도시가스/수도 영수증, 올바로 폐기물 대장 | 전통 OCR(Naver CLOVA OCR 권장) + LLM 후처리 | 표·숫자 정확도, 저비용, 좌표(bbox) 확보 |
| **비정형(unstructured)** | 안전보건위 회의록, 비상대응 매뉴얼, 사내 규정집 | VLM 우선(GPT-4o Vision 등) | 레이아웃 자유도, 서술형 정성 추출 |

`route_document()`는 세 신호를 결합해 판정한다.

1. **키워드 시그니처** — 1페이지 프리뷰(`_quick_preview`)에서 발급기관/양식 키워드 매칭 (`한국전력`, `kWh`, `올바로`, `산업안전보건위원회` …).
2. **레이아웃 특징** — `table_area_ratio`(표 격자 밀도). 정형 문서일수록 높음 → 정형 점수 가산.
3. **파일명 힌트** — 보조 신호.

> **안전 폴백:** 두 채널 점수가 모두 임계치(`0.30`) 미만이면 **비정형(VLM)** 으로 보낸다. VLM은 정형도 어느 정도 읽을 수 있어 "놓치는 것"보다 "비싸게라도 읽는 것"이 안전하기 때문.

### 1.2 통합 출력 스키마 (채널 무관)

두 채널은 **반드시 동일한 `OcrExtraction`** 을 반환한다. 그래서 하위 `evidence_graph`는 채널을 신경 쓰지 않는다.

```python
OcrExtraction(
  source_file: str,           # "한전고지서_2025_12.pdf"  ← L5 증빙 하드링크 키
  channel: DocChannel,
  doc_type: str,              # "kepco_bill" | "safety_minutes" | ...
  metrics: list[ExtractedMetric],   # 정량 → EvidenceNode 후보
  clauses: list[ExtractedClause],   # 정성 → TextNode 후보
  raw_text: str,
  router_meta: dict,          # 라우팅 근거(감사 추적)
)
```

### 1.3 데이터 흐름 (정형 vs 비정형)

```
정형 채널:  파일 → CLOVA OCR(토큰+bbox) → 양식 템플릿 매칭(_load_template)
                 → LLM 후처리(STRUCTURED_NORMALIZE_PROMPT: 단위환산+K-ESG코드추정)
                 → ExtractedMetric[]

비정형 채널: 파일 → 페이지 이미지화(pdf2image) → VLM(VLM_EXTRACT_PROMPT)
                 → JSON(metrics + clauses) → ExtractedMetric[] + ExtractedClause[]
```

---

## 2. 방향성 1 — L0: 통합 Evidence Graph (SSOT)

### 2.1 핵심 설계 원칙

DART 공백을 OCR 증빙으로 메우되, **출처를 잃지 않고 하나의 그래프로 묶는다.**

- `EvidenceNode.origin` ∈ `{dart, ocr_structured, ocr_unstructured}` — 출처 추적.
- 모든 OCR 노드는 `source_file`을 보존 → L5 증빙 서류철 하드링크 키가 된다.
- **파생 노드 자동 생성:** 전력(kWh)/가스(MJ) 사용량 노드 → 배출계수 환산 → `E-3-1` 온실가스(tCO2eq) 노드. *중소기업 사업보고서에 없는 배출량을 증빙으로부터 계산해 채운다.* (검증 결과 128,400 kWh → 61.4 tCO2eq 자동 산출 확인)
- **교차검증 엣지:** DART와 OCR이 같은 metric/period를 가지면 `cross_check` 엣지로 연결하고 오차%를 기록 → D1이 활용.
- 정성 조항은 `TextNode`로 별도 보관 → 규정 검증(P축)에서 사용.

### 2.2 노드 ID 규약

```
정량: {corp}_{kesg_code}_{period}__{origin}
       예) LOCAL_E-4-1_2025__ocr_structured
파생: {corp}_E-3-1_{period}__derived_{origin}
정성: {corp}_TXT_{idx:04d}
```

### 2.3 metric_hint → K-ESG 코드 확정

LLM이 추정한 `kesg_code_guess`를 화이트리스트 사전(`_HINT_TO_KESG`)으로 2차 보정한다(환각 방지). 예: `사용전력량→E-4-1`, `폐기물→E-6-1`, `용수→E-5-1`.

---

## 3. 방향성 2 — L5: 대기업 실사 대응 산출물

### 3.1 `audit_trace_v15.json` 스키마

기존 v10 문장 단위 추적(`sentences[]`)은 **그대로 유지**하고, 실사 대응용 두 블록을 추가한다.

```jsonc
{
  "schema_version": "v15",
  "ticker": "LOCAL",
  "corp_name": "(주)예시중소기업",
  "generated_at": "2026-05-30T...Z",

  "data_points": [                      // ★ 신규: 대기업 입력용 확정 정량값
    {
      "kesg_code": "E-4-1",
      "kesg_name": "에너지 사용량",
      "value": 128400, "unit": "kWh", "period": 2025,
      "confidence": 0.93,
      "verification": "verified",       // verified|estimated|unverified
      "d1_risk": 0.0,
      "evidence_files": [               // ★ 수치 옆 증빙 하드링크
        { "file_name": "한전고지서_2025_12.pdf",
          "relative_path": "evidence_pack/한전고지서_2025_12.pdf",
          "origin": "ocr_structured",
          "bbox": [x0,y0,x1,y1],
          "node_id": "LOCAL_E-4-1_2025__ocr_structured" }
      ]
    }
  ],

  "policy_audit": [                     // ★ 신규: 규정 검증 결과
    { "kesg_code": "S-3-1", "passed": false,
      "findings": [
        { "requirement": "근로자 대표의 참여 보장 문구",
          "status": "missing",
          "gap_comment": "...", "suggested_fix": "..." } ],
      "source_files": ["안전보건규정.pdf"] }
  ],

  "sentences": [ /* 기존 v10 AuditSentence[] 그대로 */ ],
  "summary": { "data_point_count": 4, "verified_ratio": 1.0,
               "policy_pass": 0, "policy_total": 3 }
}
```

### 3.2 대기업 제출용 엑셀 매핑 구조

`ESG_DataSheet_대기업제출용.xlsx` — 3개 시트. `data_points` → 시트1 행 1:1 매핑.

| 시트 | 내용 | 컬럼 |
|------|------|------|
| **DataSheet** | 복붙용 정량 데이터 | K-ESG 코드 · 항목명 · 값 · 단위 · 연도 · **검증상태(색상)** · D1위험도 · **증빙파일(하이퍼링크)** |
| **PolicyAudit** | 규정 검증 결과 | 코드 · 통과 · 요구사항 · 상태 · 갭 코멘트 · 보완 제안 |
| **Glossary** | 범례 | verified/estimated/unverified 의미 |

- **검증상태 셀 색상:** verified=녹색 / estimated=노랑 / unverified=빨강 → 대기업 담당자가 한눈에 신뢰도 파악.
- **증빙 하드링크:** 셀의 하이퍼링크 = `evidence_pack/{원본파일명}`. 엑셀·JSON·서류철이 **동일 상대경로**를 가리켜 외부 감사가 클릭 추적 가능.

### 3.3 증빙 서류철(`evidence_pack/`)

업로드 원본을 그대로 복사 + `index.json`(audit_trace 링크). 엑셀의 '증빙파일' 열과 파일명이 일치하므로, 대기업/감사인은 **수치 → 원본 고지서**를 즉시 역추적한다.

---

## 4. 방향성 3 — 사내 규정 검증 & 문구 보완 (LLM 프롬프트 전략)

부족한 정성 데이터를 "검증"과 "생성" 두 단계로 보완한다. 둘 다 **수치·법적 필수문구 환각 금지**를 시스템 차원에서 강제한다.

### 4.1 검증 (gap detection) — `audit_policy_documents()`

규정집 `TextNode` ↔ K-ESG **필수 구성요소 체크리스트**(`POLICY_CHECKLISTS`)를 한 줄씩 대조.

- 시스템 프롬프트(`POLICY_AUDIT_SYSTEM`)는 심사관 페르소나 + **"모호하면 미흡으로 본다"** 보수적 판정 원칙.
- 출력은 항목별 `{status: met|insufficient|missing, evidence_quote, gap_comment, suggested_fix}` JSON.
- 예: S-3-1 안전보건에서 `근로자 대표의 참여 보장 문구` 누락을 인라인으로 지적(검증 통과 확인).

```
체크리스트 예시 (S-3-1 안전보건):
 - 산업안전보건위원회 설치 및 정기 개최 명시
 - 근로자 대표의 참여 보장 문구      ← 중소기업이 자주 누락
 - 위험성 평가 절차의 주기·방법 규정
 - 비상대응 절차 및 책임자 지정
```

### 4.2 생성 (보완 초안) — `draft_missing_policy()`

검증에서 나온 누락/미흡 항목만 모아 **업종 표준 조문 초안**을 자동 작성.

- 시스템 프롬프트(`POLICY_DRAFT_SYSTEM`) 3대 규칙:
  1. 회사가 실제 이행 가능한 현실적 수준 (과장·미사여구 금지)
  2. **정량 수치는 `[○○]` 플레이스홀더** (임의 수치 창작 금지)
  3. 법적 필수 문구(근로자 대표 참여 등) 반드시 포함
- 출력 끝에 `※ 담당자 확인 필요 항목` 으로 채워야 할 플레이스홀더 요약 → HITL 유도.

### 4.3 D1 수치 검증과의 연결

`detector_5axis.detect_d1_numeric()`은 문장 수치를 SSOT 노드와 ±2% 대조한다.

- 일치 노드가 OCR 증빙이면 `evidence`에 **파일명**을 기록(감사 추적).
- **근거 노드 자체가 없으면 0.9 고위험** = "미증빙 수치" → 대기업에 내보내지 않거나 `unverified` 라벨.
- DART↔OCR `cross_check` 오차 >5%면 위험 가산.

(검증: 일치 수치 → score 0.0 + 증빙파일 기록 / 9999 오입력 → score 0.6 미일치 경고)

---

## 5. 코드 스캐폴드 구조

```
v15_scaffold/
├── app.py                       # Streamlit UX (업로드→실행→엑셀/JSON 다운로드)
├── requirements.txt
├── docs/
│   └── ESGenie_v15_설계문서.md   # (본 문서)
└── esgenie_v15/
    ├── __init__.py
    ├── ocr_router.py            # L0-A 하이브리드 라우팅 + 정형/비정형 추출 스텁
    ├── evidence_graph.py        # L0 통합 SSOT (DART+OCR, 탄소파생, cross_check)
    ├── detector_5axis.py        # L3 D1 수치검증 + P축 규정검증/보완
    ├── audit_trace.py           # L5 audit_trace_v15 (data_points + policy_audit)
    ├── excel_exporter.py        # L5 대기업 엑셀 + 증빙 서류철
    └── prompts.py               # OCR후처리/규정검증/문구보완 프롬프트
```

### 구현 우선순위 (팀 분담 가이드)

| 담당 | 모듈 | 채울 TODO |
|------|------|-----------|
| **OCR/데이터 엔지니어** | `ocr_router.extract_structured/unstructured` | CLOVA OCR 연동, VLM 호출, 템플릿 매칭 |
| **RAG 개발자** | `evidence_graph.build_from_dart` + 기존 L1/L2 결합 | DART 노드 origin 래핑, RAG가 SSOT 조회하도록 연결 |
| **검증 로직** | `detector_5axis` D2~D5 | 기존 layer3_detect 재사용 + P축 체크리스트 확장 |
| **프론트** | `app.py` | 결과 탭 UX, 다운로드, evidence_pack 미리보기 |

### 검증 완료 항목 (스캐폴드 실행 결과)

- ✅ 라우팅: `한국전력_전기요금_고지서…` → structured/kepco_bill, `산업안전보건위원회_회의록…` → unstructured/safety_minutes
- ✅ SSOT 통합 + 탄소 파생: 128,400 kWh → **61.4 tCO2eq(E-3-1)** 자동 노드 생성
- ✅ D1 수치검증: 일치(0.0, 증빙파일 기록) / 미일치(0.6 경고)
- ✅ 규정검증: 근로자 대표 참여 누락 검출
- ✅ L5: data_points 생성 + `ESG_DataSheet_대기업제출용.xlsx`(3시트) + audit_trace_v15.json 출력

> 모든 외부 호출부(OCR·VLM·DART·LLM)는 `NotImplementedError` 스텁 또는 `_MockLLM` 폴백으로 막혀 있어, 키 없이도 데모가 끝까지 동작한다(`app.py`의 `_demo_extraction`). 실제 연동은 표시된 TODO만 채우면 된다.
