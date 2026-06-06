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
│ L1  K-ESG 추출                                                           │
│     61개 항목 매핑 + DART·OCR 복합 evidence_node_ids 부착                │
│     OCR 증빙으로 no_evidence 플래그 자동 해소                             │
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

## 4축 그린워싱 리스크 (D4 제거)

| 축 | 이름 | 설명 | 가중치 |
|----|------|------|--------|
| D1 | 수치 정확성 | DART·OCR 복합 증빙과의 상대 오차 ± 2% 허용 | 40% |
| D2 | 수식어 과장 | 모호·최상급 수식어 밀도 | 25% |
| D3 | 의미 일관성 | RAG 청크와의 코사인 유사도 역수 | 25% |
| D5 | 시계열 모순 | L0 시계열 엣지와의 방향 일치 여부 | 10% |

> D4(업종 Z-score)는 중소기업 벤치마크 데이터 부족으로 제거. 가중치를 D1·D2·D3에 재배분.

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
│   ├── layer1_extract.py               # L1: K-ESG 61항목 추출
│   ├── layer2_rag.py                   # L2: Hybrid RAG (3채널)
│   ├── layer3_detect.py                # L3: 4축 리스크 분해
│   ├── layer4_verify.py                # L4: 제약 재생성 루프
│   ├── layer5_audit_trace.py           # L5: Audit Trace 생성
│   ├── pipeline.py                     # 6-Layer 오케스트레이터
│   └── knowledge/
│       ├── kesg_items.py               # K-ESG 61항목 정의
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
