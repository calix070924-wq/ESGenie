# ESGenie — K-ESG 공시 보고서 생성·검증 AI (v15)

2026 인공지능 루키 대회 제출용 프로토타입.  
DART 공시 + 내부 증빙 OCR을 단일 진실 원천(SSOT)으로 통합하고,  
K-ESG 4축 그린워싱 자동 검증 + D6 선택적 공시(체리피킹) 탐지 + 사내 규정 누락 조항 검출을 end-to-end로 제공.

> 그린워싱 측정의 학계 2기둥 중 **decoupling(말↔성과 괴리)** 은 D1·D2·D5가, **selective disclosure(유리한 것만 공개·불리한 건 누락)** 은 D6가 담당한다. 후자는 기존 도구(greenwatch.ai 등)가 비워둔 영역.
codex -p azure
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
│ L3  리스크 분해 (문장 단위 4축 + 문서 단위 D6 + P축)                       │
│     D1 수치정확성(40%) · D2 수식어과장(25%)                               │
│     D3 의미일관성(25%) · D5 시계열모순(10%)        ← 문장 단위 4축         │
│     D6 선택적 공시: 민감항목 누락 + 고아비율 → 체리피킹  ← 문서 단위       │
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
│     provenance 체인: 주장→SSOT노드→원본파일→문서내 bbox 위치              │
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
> D1~D5는 **문장 단위** 검출기다. 문서 전체의 누락·은폐를 보는 D6는 별도(아래).

---

## D6 선택적 공시(Selective Disclosure) 탐지 — 문서 단위

D1~D5가 "쓴 문장이 과장·불일치인가"를 본다면, D6는 **"무엇을 안 썼는가"**(cherry-picking)를
본다. `esgenie/layer3_disclosure.py`, 룰 기반 결정적 탐지기. 입력은 L1 추출 결과(어떤 K-ESG
항목이 공시/누락됐는지), 출력은 `DisclosureReport`(의심도 0~1, level low/medium/high).

| 신호 | 이름 | 내용 |
|------|------|------|
| A | 민감 항목 누락 | 배출량(E-3)·폐기물(E-6)·오염(E-7)·법규위반(E-8/S-9/G-6)·산재(S-4-2) 등 "숨기고 싶은" 항목이 프로파일 대상인데 누락. 항목별 민감도 가중을 **절대 누적식** `min(1, Σ가중 / 4.0)` 으로 합산(큰 분모에 희석되지 않도록) |
| B | 고아 비율 (가장 강한 신호) | 유리한 *비율*만 공시하고 그 분모·총량은 침묵. 예: 폐기물 재활용률(E-6-2)은 자랑, 폐기물 총량(E-6-1)은 누락 |

점수 = `0.60 × 신호A + 0.45 × 신호B`(2026-06-14 signal_a 재조정: 분모 희석 제거 후 가중 상향).

```bash
python -m esgenie.pipeline --ticker 005930 --areas E        # D6 의심도·고아비율 CLI 출력
python -m esgenie.benchmark_disclosure                       # 문서단위 벤치 12케이스
```

> 문서단위 벤치(`data/benchmark_v2/disclosure_bench.json`, 12케이스): 레벨 정확도 100% · 이진 100%.
> 단 12케이스를 직접 설계·튜닝한 결과라 일반화 아님 — 실보고서 held-out 검증은 백로그.

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

- LLM: Azure OpenAI(Foundry) `gpt-4.1-mini` 우선 → Anthropic(`ANTHROPIC_API_KEY`, 폴백 유지) → mock (키 없이도 전체 경로 시연 가능)
- `ESGENIE_STRICT=1` 설정 시 키 없음·API 실패에서 조용한 mock 폴백 대신 `LLMUnavailableError` raise (평가·운영용)
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

**실키 결과 (2026-06-11, gpt-4.1-mini via Azure AI Foundry, mock fallback 0건):**

| 검출기 | Precision | Recall | F1 | LLM 호출 |
|---|---|---|---|---|
| 룰 단독 (1차) | 0.657 | 1.000 | 0.793 | 0 |
| **하이브리드 (룰+LLM)** | **0.815** | **0.957** | **0.880** ← 최고 | 36 |
| LLM 단독 | 0.938 | 0.652 | 0.769 | 50 |

읽는 법:
- **룰 단독**: recall 1.0이지만 근거 수반 수식어·미래 계획에서 구조적 오탐 → precision 하락
- **LLM 단독**: precision은 높으나 수치 불일치·시계열 모순에서 recall 급락 — 증빙 대조 능력 부재
- **하이브리드**: 룰의 recall + LLM의 맥락 판정 결합으로 F1 최고, 호출 수는 트리거 게이트로 절감

실키 측정: `OPENAI_API_KEY=... ESGENIE_STRICT=1 python -m esgenie.benchmark`

> 위 50문장은 합성 튜닝셋이다. **일반화는 held-out 분리 평가**로 본다
> (`docs/held_out_eval_methodology.md`, `scripts/held_out_eval.py`):
> dev F1 1.000 vs **교차도메인(광고) test F1 0.842**, **동일도메인(실보고서) 특이도 0.978**(46문장 중 45건 올바른 미플래그).
> mock 모드(`ESGENIE_FORCE_MOCK=1`)는 결정적 휴리스틱이라 성능 주장엔 쓰지 않는다.

---

## OCR 듀얼 채널

| 채널 | 대상 문서 | 처리 방식 |
|------|----------|----------|
| 정형 (Structured) | 전기요금·가스·수도 고지서, 폐기물 대장, 연료 영수증 | **Azure AI Document Intelligence**(`prebuilt-read`, 한국어·표·좌표) → `gpt-4.1-mini`(Azure) 단위 정규화·K-ESG 코드 추정. 키 없으면 pymupdf+정규식(디지털 PDF) → mock 폴백 |
| 비정형 (Unstructured) | 안전보건위원회 회의록, 비상대응 매뉴얼, 사내 규정집 | VLM(`gpt-4.1-mini` via Azure) → 정성 텍스트 추출 |

> 2026-06-13 OCR 스택 Azure 이관: 정형=Azure Document Intelligence, 비정형=gpt-4.1-mini(Anthropic 경로 제거).

- API 키 없이도 **mock fallback**으로 전 채널 동작 보장
- OCR 수치는 DART와 cross_check 엣지로 연결 → D1 교차검증 자동화
- OCR 토큰의 polygon 좌표를 [0,1] 정규화해 저장 → 감사 추적의 **bbox 오버레이**(원본 PDF 위 위치 표시) 재료
- 정성 조항(TextNode)은 P축 규정 검증과 L2 RAG 인덱스에 공유

---

## 설치

```bash
pip install -r requirements.txt   # 전체 의존성 (코어 + SSOT/OCR)
cp .env.example .env   # 키가 있다면 채우기 (없어도 동작)
```

`.env` 설정 항목:

```env
OPENAI_API_KEY=           # LLM 1순위 (Azure Foundry 키도 여기에) — 없으면 Anthropic → mock
AZURE_OPENAI_ENDPOINT=    # Azure OpenAI(Foundry) 엔드포인트 — gpt-4.1-mini 판정/생성/OCR 후처리
ANTHROPIC_API_KEY=        # LLM 폴백 (선택)
DART_API_KEY=             # 실시간 DART 조회 (없으면 샘플 데이터)
AZURE_DOC_INTEL_ENDPOINT= # 정형 증빙 OCR — Azure Document Intelligence (없으면 pymupdf → mock)
AZURE_DOC_INTEL_KEY=
OPENAI_MODEL=gpt-4.1-mini
EMBED_MODEL=paraphrase-multilingual-MiniLM-L12-v2
```

---

## 실행 방법

### 1. CLI 파이프라인

```bash
# 삼성전자 E 영역 분석
python -m esgenie.pipeline --ticker 005930 --areas E

# 중소기업 (sme 프로파일 자동 적용)
python -m esgenie.pipeline --ticker SME001 --areas E

# 그린워싱 시연 + 하이브리드 검출
python -m esgenie.pipeline --ticker 005930 --areas E --demo-greenwash --llm-judge
```

### 2. Streamlit UI (통합)

```bash
streamlit run app.py
```

- 증빙 파일 업로드 (PDF/이미지) → 채널 자동 분기 → SSOT 통합
- K-ESG 커버리지 + Evidence Graph 노드 테이블
- 4축 레이더 차트 + P축 규정 누락 조항 인라인 표시
- **공시 진단 탭**: D6 선택적 공시 패널(의심도·level 배지·고아비율·누락 민감항목·근거)
- **감사 추적 탭**: provenance 탐색기(주장→SSOT→원본파일→문서위치 체인, 검증 배지, bbox 위치 오버레이)
- evidence_pack Excel 다운로드 (감사 증빙 서류철)

### 3. 벤치마크 / 테스트 / 환경 점검

```bash
python -m esgenie.benchmark              # 그린워싱(문장) 3검출기 비교
python -m esgenie.benchmark_disclosure   # D6 선택적 공시(문서) 벤치
python -m pytest tests/ -q               # 테스트 전체
python -m esgenie.doctor --smoke         # 데모 전 환경 사전점검 (패키지·키·임베딩 백엔드·E2E)

# held-out 분리 평가 (일반화 측정, 실키)
ESGENIE_STRICT=1 PYTHONPATH=. python scripts/held_out_eval.py
```

> **시연 전 필수:** `doctor`는 조용한 폴백(임베딩 해시 폴백, mock LLM 등)을 노출한다.
> 폴백은 "키 없이도 도는 데모"를 보장하는 설계지만, 시연 머신마다 품질이 달라지는
> 원인이므로 발표 전 반드시 종합 판정 🟢 확인. 임베딩 백엔드는 사이드바·로그·
> audit_trace(`model_versions.embed_backend`)에도 기록된다.

---

## Mock 모드

API 키 없이도 전체 파이프라인이 동작한다.

| 키 미설정 시 | 동작 |
|------------|------|
| `OPENAI_API_KEY` | 템플릿 기반 Mock LLM 활성화 |
| `AZURE_DOC_INTEL_*` | pymupdf+정규식(디지털 PDF) → kepco_bill·gas_bill·waste_ledger 샘플 데이터 폴백 |
| `DART_API_KEY` | 로컬 샘플 DART JSON 사용 (삼성·현대차·POSCO) |

```bash
unset OPENAI_API_KEY
python -m esgenie.pipeline --ticker 005930 --areas E
```

---

## 프로젝트 구조

```
ESGenie/
├── app.py                              # Streamlit UI (통합 — 코어 + SSOT/공시진단/감사추적 탭)
├── requirements.txt                    # 통합 의존성 (코어 + SSOT/OCR)
├── esgenie/                            # 단일 패키지 (구 v10 + v15 통합)
│   ├── config.py                       # 환경 변수 + 임계값 (4축, 판정기, strict)
│   ├── schemas.py                      # 공유 데이터클래스 (RiskVector, AxisScore)
│   ├── llm.py                          # Azure OpenAI(Foundry)/OpenAI/Anthropic + Mock LLM
│   ├── dart_client.py                  # DART OpenAPI 래퍼
│   ├── embeddings.py                   # FAISS + TF-IDF 폴백
│   ├── layer0_evidence_graph.py        # L0: EvidenceGraph (DART 전용)
│   ├── layer1_extract.py               # L1: K-ESG 추출 (프로파일 기반 28/61)
│   ├── layer2_rag.py                   # L2: Hybrid RAG (3채널)
│   ├── layer3_detect.py                # L3: 4축 리스크 분해 (룰 1차, 문장 단위)
│   ├── layer3_judge.py                 # L3.5: LLM 2차 판정 (하이브리드)
│   ├── layer3_disclosure.py            # L3.6: D6 선택적 공시 탐지 (문서 단위, 룰 기반)
│   ├── layer4_verify.py                # L4: 제약 재생성 루프
│   ├── layer5_audit_trace.py           # L5: Audit Trace 생성
│   ├── pipeline.py                     # 6-Layer 오케스트레이터 (+ D6 통합)
│   ├── provenance.py                   # 감사 추적: 주장→SSOT→원본파일→문서위치 체인
│   ├── pdf_render.py                   # PDF 페이지→PNG + 정규화 bbox 오버레이 (PyMuPDF/PIL)
│   ├── calibrate.py                    # judge 임계값 오프라인 그리드서치 (capture/search)
│   ├── evaluate.py                     # 신뢰도·불확실성 평가 (부트스트랩 CI)
│   ├── benchmark.py                    # 그린워싱 검출 벤치마크 하네스 (문장 단위)
│   ├── benchmark_disclosure.py         # D6 문서단위 벤치 하네스 (레벨 3분류+이진)
│   ├── doctor.py                       # 데모 전 환경 사전점검 (--smoke)
│   ├── ssot/                           # SSOT/OCR 확장 (구 v15_scaffold/esgenie_v15)
│   │   ├── evidence_graph.py           # L0: SSOT 통합 그래프 (DART + OCR)
│   │   ├── ocr_router.py               # OCR 듀얼 채널 (Azure Doc Intelligence + VLM, mock 폴백)
│   │   ├── ssot_pipeline.py            # L1/L2 SSOT 브리지
│   │   ├── detector_5axis.py           # L3: D1 증빙 강화 + P축 규정 검증
│   │   ├── prompts.py                  # LLM 프롬프트 전략
│   │   ├── audit_trace.py              # L5: 엔터프라이즈 Audit Trace (bbox EvidenceLink 포함)
│   │   └── excel_exporter.py           # evidence_pack Excel 내보내기
│   └── knowledge/
│       ├── kesg_items.py               # K-ESG 61항목 정의 + 프로파일(sme/full) + D6 민감항목
│       └── greenwash_lexicon.py        # 과장 수식어 사전
├── data/
│   ├── sample_dart/                    # 샘플 DART (대기업 3사 + SME 2사)
│   ├── benchmark/                      # 그린워싱 벤치마크 (문장 50케이스)
│   ├── benchmark_v2/                   # D6 문서벤치 + dev/test held-out 스플릿
│   ├── kesg/                           # K-ESG 가이드라인
│   ├── industry/                       # 업종 벤치마크
│   └── best_reports/                   # 우수 보고서 발췌
├── scripts/                            # build_splits·held_out_eval·recompute_ci·build_disclosure_bench
├── docs/                               # 스키마/설계 + held-out 평가 방법론/라벨링 루브릭
├── outputs/                            # audit_trace JSON + 벤치마크 결과
└── tests/                              # L0/L3/D6/provenance/OCR bbox/프로파일/SSOT/벤치/E2E 테스트
```

---

## 팀

ESGenie · 한양대학교 ERICA · 2026 인공지능 루키 대회
