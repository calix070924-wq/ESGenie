# ESGenie — K-ESG 공시 보고서 생성·검증 AI (v10)

2026 인공지능 루키 대회 제출용 프로토타입.
DART 공시 연동 + 한국형 ESG 기준(K-ESG) 특화 + 그린워싱 자동 검증을 end-to-end로 제공.

---

## 6-Layer AI 파이프라인

```
DART JSON
    │
    ▼
┌─────────────────────────────────────────────────────────────────┐
│ L0  Evidence Graph   DART 수치 팩트 노드 + 시계열 엣지 구축      │
└─────────────────────────────────────────────────────────────────┘
    │ EvidenceGraph
    ▼
┌─────────────────────────────────────────────────────────────────┐
│ L1  K-ESG 추출       61개 항목 매핑 + evidence_node_ids 부착     │
└─────────────────────────────────────────────────────────────────┘
    │ ExtractionResult
    ▼
┌─────────────────────────────────────────────────────────────────┐
│ L2  Hybrid RAG       3채널(K-ESG·업종·자사) 병렬 검색 + 생성     │
└─────────────────────────────────────────────────────────────────┘
    │ GenerationResult
    ▼
┌─────────────────────────────────────────────────────────────────┐
│ L3  5축 리스크 분해   D1수치·D2수식어·D3의미·D4업종·D5시계열     │
└─────────────────────────────────────────────────────────────────┘
    │ RiskVector
    ▼
┌─────────────────────────────────────────────────────────────────┐
│ L4  제약 재생성 루프  5축 제약 주입 → 재생성 → 수렴 (최대 3회)   │
│                      수렴 실패 시 → HITL 에스컬레이션            │
└─────────────────────────────────────────────────────────────────┘
    │ VerificationResult
    ▼
┌─────────────────────────────────────────────────────────────────┐
│ L5  Audit Trace      문장 단위 근거 추적 → audit_trace.json      │
└─────────────────────────────────────────────────────────────────┘
```

| Layer | 역할 | 핵심 기술 |
|-------|------|-----------|
| L0 Evidence Graph | DART 수치 → 팩트 노드 + YoY 시계열 엣지 | 정규식 YoY 추출, dataclass 그래프 |
| L1 K-ESG 추출 | 61개 항목 매핑 + 증거 노드 연결 | JSON Schema, coverage 통계 |
| L2 Hybrid RAG | 3채널 병렬 검색 + 섹션 생성 | FAISS × 3, Sentence-BERT, TF-IDF 폴백 |
| L3 5축 리스크 | D1~D5 독립 점수 + 가중 합산 | SBERT 코사인, 업종 Z-score, 시계열 방향 검증 |
| L4 재생성 루프 | 5축 제약 주입 → 반복 개선 → HITL 에스컬레이션 | Iterative Refinement, max 3회 |
| L5 Audit Trace | 문장 단위 모든 근거 묶음 → JSON 저장 | AuditSentence, AuditTrace 스키마 |

---

## 설치

```bash
pip install -r requirements.txt
cp .env.example .env   # 키가 있다면 채우기 (없어도 동작)
```

---

## 실행 방법

### 1. CLI (파이프라인 단독)

```bash
# 삼성전자 E 영역 분석
python -m esgenie.pipeline --ticker 005930 --areas E

# 3사 전체 E/S/G 분석
python -m esgenie.pipeline --ticker 005930 --areas E S G
python -m esgenie.pipeline --ticker 005380 --areas E S G
python -m esgenie.pipeline --ticker 005490 --areas E S G

# 그린워싱 시연 모드 (의도적 과장 생성 → 자동 검증)
python -m esgenie.pipeline --ticker 005930 --areas E --demo-greenwash
```

출력: `outputs/audit_trace_{ticker}_{area}_{ts}.json`

### 2. Streamlit UI

```bash
streamlit run app.py
```

- **홈**: 4개 KPI + L0/L1/L4 통계 요약
- **Step 1**: K-ESG 커버리지 + Evidence Graph 노드 테이블
- **Step 2**: RAG 초안 + 검색 청크 확인
- **Step 3**: 최종 보고서 + **5축 레이더 차트** + 재생성 이력
- **Step 4**: **HITL 패널** (문장별 승인/거부) + Audit Trace 다운로드

### 3. 테스트

```bash
python -m pytest tests/ -q
```

---

## Mock LLM 모드

OpenAI API 키 없이도 전체 파이프라인이 동작한다.

- `OPENAI_API_KEY` 미설정 시 자동으로 Mock LLM 활성화
- Mock LLM은 템플릿 기반으로 사전 정의된 ESG 보고서 문장 반환
- L0 Evidence Graph, L1 추출, L3 리스크 분해, L5 Audit Trace 모두 정상 동작
- 심사위원이 즉시 시연 가능한 상태

```bash
# 키 없이 실행 확인
unset OPENAI_API_KEY
python -m esgenie.pipeline --ticker 005930 --areas E
```

---

## 5축 리스크 (D1~D5)

| 축 | 이름 | 설명 | 데이터 소스 |
|----|------|------|------------|
| D1 | 수치 정확성 | DART 실측 수치와의 상대 오차 | L0 EvidenceGraph |
| D2 | 수식어 과장 | 모호·최상급 수식어 밀도 | 텍스트 패턴 사전 |
| D3 | 의미 일관성 | RAG 청크와의 코사인 유사도 역수 | L2 RAG 벡터 |
| D4 | 업종 이탈 | 업계 평균 대비 표준편차 | benchmarks.json |
| D5 | 시계열 모순 | L0 시계열 엣지와의 방향 일치 여부 | L0 EvidenceEdge |

가중치: D1(35%) · D2(20%) · D3(20%) · D4(15%) · D5(10%)

---

## 프로젝트 구조

```
ESGenie/
├── app.py                         # Streamlit UI (5탭 + HITL 패널)
├── requirements.txt
├── README.md
├── docs/
│   └── audit_trace_schema.md      # Audit Trace JSON 스키마 문서
├── esgenie/
│   ├── config.py                  # 환경 변수 + v10 임계값 상수
│   ├── schemas.py                 # v10 공유 데이터클래스 (RiskVector, AuditTrace 등)
│   ├── llm.py                     # OpenAI + Mock LLM 폴백
│   ├── dart_client.py             # DART OpenAPI 래퍼 + 샘플 로더
│   ├── embeddings.py              # 임베딩 + FAISS + TF-IDF 폴백
│   ├── layer0_evidence_graph.py   # [NEW] L0: EvidenceGraph 구축
│   ├── layer1_extract.py          # L1: K-ESG 61항목 추출
│   ├── layer2_rag.py              # L2: Hybrid RAG
│   ├── layer3_detect.py           # L3: 5축 리스크 분해
│   ├── layer4_verify.py           # L4: 제약 재생성 루프
│   ├── layer5_audit_trace.py      # [NEW] L5: Audit Trace 생성
│   ├── pipeline.py                # 6-Layer 통합 오케스트레이터
│   └── knowledge/
│       ├── kesg_items.py          # K-ESG 61항목 정의
│       └── greenwash_lexicon.py   # 과장 수식어 사전
├── data/
│   ├── sample_dart/               # 샘플 DART 보고서 (005930/005380/005490)
│   ├── kesg/                      # K-ESG 가이드라인 발췌
│   ├── industry/                  # 업종 벤치마크 (benchmarks.json)
│   └── best_reports/              # 우수 지속가능경영보고서 발췌
├── outputs/                       # audit_trace JSON 저장 위치
└── tests/
    ├── test_layer0_evidence_graph.py   # L0 40개 테스트
    ├── test_layer3_risk_vector.py      # L3 24개 테스트
    └── test_pipeline_e2e.py            # E2E 15개 테스트 (3사 × 5종)
```

---

## 라이선스 / 팀

ESGenie 팀 · 한양대학교 ERICA · 2026 인공지능 루키 대회
