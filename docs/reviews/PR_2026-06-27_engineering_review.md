# PR: ESGenie 파이프라인 엔지니어링 리뷰 (구조/품질/운영 리스크)

## Summary
- ESGenie 6-Layer 파이프라인(L0~L6), SSOT, RAG Gate, Report 조립 경로를 엔지니어링 관점에서 리뷰함.
- 테스트 스모크 기준 핵심 경로는 정상 동작(실행 통과).
- 다만 운영 환경에서 정확도/격리성에 영향 줄 수 있는 리스크 5건을 우선 개선 권고.

## Scope
- Core: `esgenie/pipeline.py`, `esgenie/ssot/*`, `esgenie/layer2_rag.py`, `esgenie/layer3_detect.py`, `esgenie/layer4_verify.py`, `esgenie/layer6_report.py`
- Tests: `tests/test_pipeline_e2e.py`, `tests/test_ssot.py`, `tests/test_rag_cascade.py`, `tests/test_retrieval_gate.py`

## Test Run
- `pytest -q tests/test_retrieval_gate.py tests/test_rag_cascade.py` -> 9 passed
- `pytest -q tests/test_ssot.py -k "route or build_unified_graph_without_dart or same_metric_from_multiple_files_keeps_distinct_nodes or derived_emission_node"` -> 17 passed
- `pytest -q tests/test_pipeline_e2e.py -k "local_ssot_without_dart or merges_uploaded_evidence_into_ssot or passes_resolved_industry_module_to_verify"` -> 3 passed

## Findings
### 1) [High] 요청 간 데이터 오염 가능성 (전역 싱글톤 RAG)
- `HybridRAG`가 전역 싱글톤으로 유지되고 실행마다 `corp_index`를 재빌드함.
- 멀티세션/동시 요청 시 회사 컨텍스트가 섞일 가능성.
- 관련 파일:
  - `esgenie/layer2_rag.py` (싱글톤 생성/재사용)
  - `esgenie/pipeline.py` (run 진입점에서 싱글톤 사용)
  - `esgenie/ssot/ssot_pipeline.py` (`build_rag_with_ssot`에서 corp 인덱스 갱신)
- 권고:
  - `kesg/industry` 인덱스만 공유 캐시.
  - `corp_index`는 run 스코프 인스턴스로 분리.

### 2) [High] D5 시계열 위험도 과소평가 가능
- D5 계산에서 `timeseries` 외 엣지(`cross_check`)도 분모에 포함되어 점수가 희석됨.
- 관련 파일:
  - `esgenie/layer3_detect.py`
- 권고:
  - D5는 `edge_type == "timeseries"`만 집계.
  - 분자/분모 기준 일치시키고 테스트 보강.

### 3) [Medium] SearchTerm 확장 의도 대비 실매칭 약함
- L1은 search term 확장을 수행하지만 `EvidenceGraph.search_nodes()`는 metric 문자열 중심 매칭이라 확장 이득이 제한적.
- 관련 파일:
  - `esgenie/layer1_extract.py`
  - `esgenie/ssot/evidence_graph.py`
- 권고:
  - `raw_text/source_file/alias`까지 검색 인덱스를 확장.

### 4) [Medium] D1 연도 맥락 미반영
- 동일 코드 다년도 노드가 있을 때 최신값만 비교해 문장 연도와 어긋날 수 있음.
- 관련 파일:
  - `esgenie/layer3_detect.py`
- 권고:
  - 보고연도 우선 매칭, 미존재 시 최신값 폴백.

### 5) [Medium] D3 근거 추적성 저하
- `_compute_text_risk_vector()`에서 원본 chunk id 대신 `kesg_{i}`를 사용하고 `industry_hits`를 배제함.
- 관련 파일:
  - `esgenie/layer4_verify.py`
- 권고:
  - 원본 `doc.chunk_id` 유지.
  - `industry_hits` 포함 여부를 옵션화/기본 포함.

### 6) [Low] 업종 벤치마크 로더 중복
- 동일 로직이 pipeline/report 모듈에 복제되어 유지보수 비용 증가.
- 관련 파일:
  - `esgenie/pipeline.py`
  - `esgenie/layer6_report.py`
- 권고:
  - 공통 유틸로 통합 + 캐시 적용.

## Proposed Follow-up Plan
1. RAG 인덱스 격리 리팩터링 (동시성 안전성 확보)
2. D5 집계 로직 수정 + 회귀 테스트 추가
3. D1 연도 우선 매칭 및 D3 chunk 추적 개선
4. `search_nodes` 검색 범위 확장
5. 중복 유틸 통합

## Risk/Impact
- 현재 기능은 동작하지만, 운영 트래픽/데이터 규모 증가 시 품질 변동과 디버깅 비용이 커질 가능성 높음.
- 우선순위는 동시성 격리(High)와 D5 정확도(High)부터 권장.

## Checklist
- [x] 파이프라인 핵심 경로 코드 리뷰
- [x] 테스트 스모크 실행 확인
- [x] 심각도 기준 이슈 분류
- [ ] High 2건 패치
- [ ] Medium 3건 패치

