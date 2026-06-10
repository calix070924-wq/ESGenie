# ESGenie — K-ESG 공시 보고서 생성·검증 AI (v15)

2026 인공지능 루키 대회 제출용 프로토타입.  
DART 공시 + 내부 증빙 OCR을 단일 진실 원천(SSOT)으로 통합하고,  
K-ESG 4축 그린워싱 자동 검증 + 사내 규정 누락 조항 검출을 end-to-end로 제공.

---

## 6-Layer AI 파이프라인

```
DART JSON ──┐
            ├──► L0  SSOT EvidenceGraph  ──► L1 ──► L2 ──► L3 ──► L4 ──► L5
OCR 증빙  ──┘    (DART + 내부 증빙 통합)
```

```
입력: DART 공시 + 전기요금·가스·폐기물 고지서, 안전보건 회의록, 사내 규정집
  │
  ▼
┌─────────────────────────────────────────────────────────────────────────┐
│ L0  SSOT Evidence Graph                                                  │
│     DART 수치 노드(origin=dart) + OCR 증빙 노드(origin=ocr_*)            │
│     동일 metric/period → cross_check 엣지 자동 연결 (D1 교차검증 재료)   │
│     정성 조항 → TextNode (사내규정 검증 재료)                             │
└─────────────────────────────────────────────────────────────────────────┘
  │
  ▼
┌─────────────────────────────────────────────────────────────────────────┐
│ L1  K-ESG 추출 (프로파일 기반)                                            │
│     중소기업 기본형 28항목 | 상장사 61항목 전체 — 자동 판별               │
│     DART·OCR 복합 evidence_node_ids 부착, no_evidence 플래그 자동 해소    │
└─────────────────────────────────────────────────────────────────────────┘
  │
  ▼
┌─────────────────────────────────────────────────────────────────────────┐
│ L2  Hybrid RAG                                                           │
│     3채널(K-ESG·업종·자사) 병렬 검색 + 섹션 생성                         │
│     corp_index에 SSOT TextNode + OCR 수치 노드도 편입                    │
└─────────────────────────────────────────────────────────────────────────┘
  │
  ▼
┌─────────────────────────────────────────────────────────────────────────┐
│ L3  4축 리스크 분해                                                       │
│     D1 수치정확성(40%) · D2 수식어과장(25%)                               │
│     D3 의미일관성(25%) · D5 시계열모순(10%)                               │
│     P축(Policy): 규정집 ↔ K-ESG 체크리스트 LLM 대조 → 누락 조항 검출     │
└─────────────────────────────────────────────────────────────────────────┘
  │
  ▼
┌─────────────────────────────────────────────────────────────────────────┐
│ L4  제약 재생성 루프                                                      │
│     4축 제약 주입 → 재생성 → 수렴 (최대 3회) → HITL 에스컬레이션          │
└─────────────────────────────────────────────────────────────────────────┘
  │
  ▼
┌─────────────────────────────────────────────────────────────────────────┐
│ L5  Audit Trace + 엔터프라이즈 산출물                                     │
│     문장 단위 근거 추적 → audit_trace.json                                │
│     누락 조항 초안 자동 생성 → Excel evidence_pack                        │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## K-ESG 프로파일 (sme 28 / full 61)

K-ESG 61항목 체계 위에서 기업 규모에 맞는 추적 범위를 적용한다.

| 프로파일 | 항목 수 | 대상 | 커버리지 분모 |
|---|---|---|---|
| `sme` | 기본형 28 | 중소기업 (공급망 실사 대응 핵심) | 28 |
| `full` | 전체 61 | 상장·중견기업 | 61 |

- 종목코드로 **자동 판별** (6자리 상장코드 → full, 그 외 → sme), `--profile`로 강제 가능
- 중소기업을 61항목 분모로 평가하면 커버리지가 구조적으로 낮게 나와 의미가 없음 —
  분모는 "해당 기업에 적용 가능한 항목" 기준
- 프로파일 밖 추가 공시는 `beyond_profile`로 함께 추출하되 분모에 미포함
- 사업 확장 경로: 중소기업은 28항목으로 시작 → 성장 시 같은 시스템에서 61항목으로 확장

```bash
python -m esgenie.pipeline --ticker SME001 --areas E              # 자동: sme 프로파일
python -m esgenie.pipeline --ticker 005930 --areas E --profile sme  # 강제 지정
```

---

## 4축 그린워싱 리스크 (D4 제거)

| 축 | 이름 | 설명 | 가중치 |
|----|------|------|--------|
| D1 | 수치 정확성 | DART·OCR 복합 증빙과의 상대 오차 ± 2% 허용 | 40% |
| D2 | 수식어 과장 | 모호·최상급 수식어 밀도 | 25% |
| D3 | 의미 일관성 | RAG 청크와의 코사인 유사도 역수 | 25% |
| D5 | 시계열 모순 | L0 시계열 엣지와의 방향 일치 여부 | 10% |

> D4(업종 Z-score)는 중소기업 벤치마크 데이터 부족으로 제거. 가중치를 D1·D2·D3에 재배분.

---

## 하이브리드 검출 (룰 1차 + LLM 2차 판정)

`--llm-judge` 활성화 시 2단 검증 아키텍처로 동작한다.

```
전 문장 ──► [1차: 룰 스크리닝]  재현 가능 · 비용 0 · recall 담당
                │
                ├─ 전 축 < JUDGE_TRIGGER(0.25) → LLM 호출 생략 (비용 절감)
                ▼
          [2차: LLM 맥락 판정]  precision 담당
                │  verdict: false_positive | uncertain | confirmed
                ▼
          최종점수 = 0.4 × 룰 + 0.6 × LLM   (근거 인용 포함 → audit_trace 기록)
```

룰 단독의 한계를 LLM이 보정한다:

| 사례 | 룰 단독 | 하이브리드 |
|------|---------|-----------|
| "업계 최고 수준 인증 취득 (1,200 tCO2eq)" | D2=1.00 (과장 오탐) | D2=0.43 — 정량 근거 수반 → false_positive |
| "압도적이고 선도적인 친환경 기업" | D2=1.00 | D2=1.00 — confirmed (위험 유지) |
| "감소 목표를 수립" (미래 계획) | D5 모순 오탐 가능 | 시제 구분 → false_positive |

```bash
python -m esgenie.pipeline --ticker 005930 --areas E --demo-greenwash --llm-judge
```

- LLM: OpenAI 우선 → Anthropic(`ANTHROPIC_API_KEY`) → mock (키 없이도 전체 경로 시연 가능)
- 판정 결과(verdict·근거·모델)는 `audit_trace`의 `aggregate.judge`에 기록 → 감사 추적 유지

---

## 벤치마크 (룰 단독 vs 하이브리드 vs LLM 단독)

라벨링된 50문장 벤치마크(`data/benchmark/greenwash_bench.json`)로 3개 검출기를 비교한다.
7개 카테고리: 순수 과장 / 근거 수반 수식어(룰 오탐 함정) / 수치 불일치 / 수치 일치 /
시계열 모순 / 미래 계획(룰 오탐 함정) / 사실 서술.

```bash
python -m esgenie.benchmark                  # 결과: outputs/benchmark/*.md, *.json
python -m esgenie.benchmark --detectors rule hybrid
```

mock 모드 실행 예시 (아키텍처 데모용 — 성능 주장에는 실키 재실행 결과를 사용할 것):

| 검출기 | Precision | Recall | F1 | LLM 호출 |
|---|---|---|---|---|
| 룰 단독 (1차) | 0.639 | 1.000 | 0.780 | 0 |
| 하이브리드 (룰+LLM) | 1.000 | 1.000 | 1.000 | 37 |
| LLM 단독 | 0.500 | 0.348 | 0.410 | 50 |

읽는 법:
- **룰 단독**: recall은 높지만 근거 수반 수식어(0/8)·미래 계획(1/6)에서 구조적 오탐 → precision 하락
- **LLM 단독**(mock=나이브 휴리스틱): 수치 불일치(0/10)·시계열 모순(0/5) 전멸 — 증빙 대조 능력 부재
- **하이브리드**: 룰의 recall + LLM의 맥락 판정 결합, 호출 수는 전수 대비 26% 절감(트리거 게이트)

> ⚠ mock 판정은 결정적 휴리스틱이라 위 수치는 파이프라인 데모일 뿐이다.
> 발표·논문용 수치는 `ANTHROPIC_API_KEY` 설정 후 재실행해 실모델 결과로 교체할 것.

---

## OCR 듀얼 채널

| 채널 | 대상 문서 | 처리 방식 |
|------|----------|----------|
| 정형 (Structured) | 전기요금·가스·수도 고지서, 폐기물 대장, 연료 영수증 | CLOVA OCR → 템플릿 매칭 → LLM 정규화 |
| 비정형 (Unstructured) | 안전보건위원회 회의록, 비상대응 매뉴얼, 사내 규정집 | GPT-4o Vision (VLM) → JSON 추출 |

- API 키 없이도 **mock fallback**으로 전 채널 동작 보장
- OCR 수치는 DART와 cross_check 엣지로 연결 → D1 교차검증 자동화
- 정성 조항(TextNode)은 P축 규정 검증과 L2 RAG 인덱스에 공유

---

## 설치

```bash
pip install -r requirements.txt          # v10 공통 의존성
pip install -r v15_scaffold/requirements.txt   # v15 추가 의존성 (OCR 등)
cp .env.example .env   # 키가 있다면 채우기 (없어도 동작)
```

`.env` 설정 항목:

```env
OPENAI_API_KEY=          # GPT-4o Vision + LLM 후처리 (없으면 mock)
DART_API_KEY=            # 실시간 DART 조회 (없으면 샘플 데이터)
CLOVA_OCR_SECRET=        # Naver CLOVA OCR (없으면 mock)
CLOVA_OCR_URL=           # CLOVA OCR 엔드포인트
OPENAI_MODEL=gpt-4o-mini
EMBED_MODEL=paraphrase-multilingual-MiniLM-L12-v2
```

---

## 실행 방법

### 1. v10 CLI

```bash
# 삼성전자 E 영역 분석
python -m esgenie.pipeline --ticker 005930 --areas E

# 3사 전체 E/S/G
python -m esgenie.pipeline --ticker 005930 005380 005490 --areas E S G

# 그린워싱 시연 모드 (의도적 과장 생성 → 자동 검증)
python -m esgenie.pipeline --ticker 005930 --areas E --demo-greenwash
```

### 2. v15 Streamlit UI

```bash
cd v15_scaffold
streamlit run app.py
```

- 증빙 파일 업로드 (PDF/이미지) → 채널 자동 분기 → SSOT 통합
- K-ESG 커버리지 + Evidence Graph 노드 테이블
- 4축 레이더 차트 + P축 규정 누락 조항 인라인 표시
- evidence_pack Excel 다운로드 (감사 증빙 서류철)

### 3. v10 Streamlit UI

```bash
streamlit run app.py
```

### 4. 테스트

```bash
python -m pytest tests/ -q        # v10 77개 테스트
```

---

## Mock 모드

API 키 없이도 전체 파이프라인이 동작한다.

| 키 미설정 시 | 동작 |
|------------|------|
| `OPENAI_API_KEY` | 템플릿 기반 Mock LLM 활성화 |
| `CLOVA_OCR_SECRET` | kepco_bill·gas_bill·waste_ledger 샘플 데이터 반환 |
| `DART_API_KEY` | 로컬 샘플 DART JSON 사용 (삼성·현대차·POSCO) |

```bash
unset OPENAI_API_KEY
python -m esgenie.pipeline --ticker 005930 --areas E
```

---

## 프로젝트 구조

```
ESGenie/
├── app.py                              # v10 Streamlit UI
├── requirements.txt                    # v10 의존성
├── esgenie/                            # v10 핵심 패키지
│   ├── config.py                       # 환경 변수 + 임계값 (4축)
│   ├── schemas.py                      # 공유 데이터클래스 (RiskVector 등)
│   ├── llm.py                          # OpenAI + Mock LLM
│   ├── dart_client.py                  # DART OpenAPI 래퍼
│   ├── embeddings.py                   # FAISS + TF-IDF 폴백
│   ├── layer0_evidence_graph.py        # L0: EvidenceGraph (DART 전용)
│   ├── layer1_extract.py               # L1: K-ESG 추출 (프로파일 기반 28/61)
│   ├── layer2_rag.py                   # L2: Hybrid RAG (3채널)
│   ├── layer3_detect.py                # L3: 4축 리스크 분해
│   ├── layer4_verify.py                # L4: 제약 재생성 루프
│   ├── layer5_audit_trace.py           # L5: Audit Trace 생성
│   ├── pipeline.py                     # 6-Layer 오케스트레이터
│   └── knowledge/
│       ├── kesg_items.py               # K-ESG 61항목 정의 + 프로파일(sme/full)
│       └── greenwash_lexicon.py        # 과장 수식어 사전
├── v15_scaffold/                       # v15 확장 (OCR + SSOT + 엔터프라이즈)
│   ├── app.py                          # v15 Streamlit UI
│   ├── requirements.txt                # v15 추가 의존성
│   └── esgenie_v15/
│       ├── evidence_graph.py           # L0: SSOT 통합 그래프 (DART + OCR)
│       ├── ocr_router.py               # OCR 듀얼 채널 (VLM + CLOVA)
│       ├── ssot_pipeline.py            # L1/L2 SSOT 브리지
│       ├── detector_5axis.py           # L3: 4축 + P축 검증
│       ├── prompts.py                  # LLM 프롬프트 전략
│       ├── audit_trace.py              # L5: 엔터프라이즈 Audit Trace
│       └── excel_exporter.py           # evidence_pack Excel 내보내기
├── data/
│   ├── sample_dart/                    # 샘플 DART (005930/005380/005490)
│   ├── kesg/                           # K-ESG 가이드라인
│   ├── industry/                       # 업종 벤치마크
│   └── best_reports/                   # 우수 보고서 발췌
├── outputs/                            # audit_trace JSON 저장
└── tests/
    ├── test_layer0_evidence_graph.py   # L0 40개
    ├── test_layer3_risk_vector.py      # L3 24개
    └── test_pipeline_e2e.py            # E2E 15개 (3사 × 5종)
```

---

## 팀

ESGenie · 한양대학교 ERICA · 2026 인공지능 루키 대회
