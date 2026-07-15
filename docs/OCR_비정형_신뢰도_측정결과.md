# 비정형 OCR(gpt-4.1-mini) 신뢰도 측정 결과 — 2026-07-15 (v2)

대상: 비정형 문서 15건(디지털 11 + 스캔본 4) / 채점 스크립트 `scripts/ocr_unstructured_eval.py`

## Before/After — 1차본 → 2차 재측정 비교

| 지표 | 1차본 (v1) | 2차 재측정 (v2) | 변경 사유 |
|---|---|---|---|
| 라우팅 정확도 | 8/15 (53%) | 8/15 (53%) | 동일 |
| 정량 정답 일치 | 4/4 (100%) | 4/4 (100%) | 동일 |
| 정량 FP | 44건 (전부 "환각") | **true_halluc=4 + unlisted_real=41** | FP를 원문 존재 여부로 분리 |
| 정성 recall | 99.0% (휴리스틱) | **judge: 101/101 (100.0%)** / 휴리스틱: 100/101 (99.0%) | LLM-judge 도입 |
| 정성 환각률 | 0.0% (휴리스틱) | **judge: 1/97 (1.0%)** / 휴리스틱: 0/97 (0.0%) | 구조적 0% → judge로 재판정 |
| ECE | 0.6667 | 0.6684 | 동일 수준 (conf 상수 0.75 → 신호 부재) |

## 핵심 결론

| 축 | 지표 | 결과 | 판정 |
|---|---|---|---|
| 라우팅 | doc_type 분류 정확도 | **8/15 (53%)** | ⚠️ policy_manual 세분류 미스 (채널은 정상) |
| 정량 수치 | 정답 일치율 | **4/4 (100%)** | ✅ 정답 수치 전량 추출 |
| 정량 수치 | 진짜 환각 (원문에 없는 수치 창작) | **4건** | ⚠️ 스캔본 OCR 오인식에서 발생 |
| 정량 수치 | gold 미기재 실값 (원문에 있으나 ESG 지표 아님) | **41건** | 📝 환각 아닌 과추출 |
| 정성 충실도 | recall — LLM-judge | **101/101 (100.0%)** | ✅ 완벽 |
| 정성 충실도 | 환각률 — LLM-judge | **1/97 (1.0%)** | ✅ 거의 없음 |
| 캘리브레이션 | ECE (정량 metric confidence) | **0.6684** | ⚠️ 상수 conf=0.75 → 캘리브레이션 신호 자체가 부재 |

## 측정 환경

| 항목 | 값 |
|---|---|
| 추출 엔진 | gpt-4.1-mini-text (Azure, vision=False) |
| 판정(judge) 모델 | gpt-4.1-mini (self-judge — 추출 모델과 동일, 편향 가능) |
| 디지털 경로 | pymupdf 텍스트 추출 → LLM 정량·정성 동시 추출 |
| 스캔본 경로 | Upstage Document Parse OCR → LLM |
| 채점 정답셋 | `data/benchmark_ocr/unstructured_gold.json` (15문서, 1차 라벨) |
| 실행 모드 | `ESGENIE_STRICT=1` (API 실호출, mock 폴백 금지) |
| 정성 판정 방식 | (1) 토큰 오버랩 휴리스틱 (교차검증) + (2) **LLM-as-judge** (정식 판정) |
| judge 판정 원본 | `data/benchmark_ocr/judge_decisions.json` (감사 가능) |

### 정성 판정 방식 상세

- **LLM-judge (정식)**: 문서당 1회 호출. 원문+gold facts+추출 clauses를 함께 제시하고 (A) recall 커버리지와 (B) grounding 판정을 동시 수행. 판정 기준: "근거가 명확하지 않으면 unsupported".
- **휴리스틱 (교차검증)**: 토큰 오버랩 40%↑ = recall hit, 원문 토큰 30%↓ = 환각.
- 두 방식의 차이(환각률 0% vs 1%)가 "휴리스틱이 구조적으로 환각을 못 잡는다"는 검증 결과의 증거.
- **한계**: judge가 추출 모델과 동일(gpt-4.1-mini). Self-judge 편향 가능. 더 엄밀한 측정은 다른 모델(Claude 등)로 교차 검증 권장.

## 문서별 상세

| 파일 | 유형(gold) | 라우팅 | 정량 hit | true_halluc | unlisted_real | recall(j) | 환각(j) | clauses |
|---|---|---|---|---|---|---|---|---|
| 근로시간관리규정_2025 | policy_manual | ✅ | 0/0 | 0 | 4 | 7/7 | 0 | 5 |
| 문서기록관리규정_2025 | policy_manual | ✅ | 0/0 | 0 | 0 | 6/6 | 0 | 5 |
| 물질규제_RoHS_REACH_2025 | policy_manual | ❌ | 0/0 | 0 | 0 | 6/6 | 0 | 5 |
| 시정조치_CAPA절차서_2025 | policy_manual | ❌ | 0/0 | 0 | 6 | 6/6 | 0 | 5 |
| 위생_기숙사관리규정_2025 | policy_manual | ✅ | 0/0 | 0 | 6 | 7/7 | 0 | 4 |
| 유해물질관리규정_2025 | policy_manual | ❌ | 0/0 | 0 | 2 | 6/6 | 0 | 11 |
| 지식재산보호규정_2025 | policy_manual | ❌ | 0/0 | 0 | 0 | 6/6 | 0 | 5 |
| 책임광물실사정책_2025 | policy_manual | ❌ | 0/0 | 0 | 2 | 6/6 | 0 | 8 |
| safety_policy_2025 (회의록) | safety_minutes | ✅ | 2/2 | 0 | 3 | 8/8 | 1 | 5 |
| emergency_manual_2025 ★ | emergency_manual | ✅ | 0/0 | 0 | 7 | 7/7 | 0 | 14 |
| hr_policy_2025 ★ | hr_policy | ✅ | 0/0 | 0 | 4 | 9/9 | 0 | 7 |
| 근로시간관리규정_scan | policy_manual | ✅ | 0/0 | 3 | 1 | 7/7 | 0 | 5 |
| 유해물질관리규정_scan | policy_manual | ❌ | 0/0 | 0 | 2 | 6/6 | 0 | 5 |
| 책임광물실사정책_scan | policy_manual | ❌ | 0/0 | 1 | 1 | 6/6 | 0 | 8 |
| safety_policy_scan (회의록) | safety_minutes | ✅ | 2/2 | 0 | 3 | 8/8 | 0 | 5 |

★ = synthetic 문서 (실문서 부재로 생성, gold에 `synthetic: true` 표시)

## 디지털 vs 스캔본 비교

| 경로 | 라우팅 | 정성 recall(judge) | 정성 환각률(judge) | 정량 true_halluc |
|---|---|---|---|---|
| 디지털 (pymupdf) | 6/11 (55%) | 74/74 (100.0%) | 1/74 (1.4%) | 0건 |
| 스캔본 (Upstage OCR) | 2/4 (50%) | 27/27 (100%) | 0/23 (0%) | 4건 |

스캔본의 true_halluc 4건은 모두 OCR 오인식에서 비롯된 수치(문자 깨짐 → 존재하지 않는 숫자 추출).
정성 추출은 디지털/스캔 모두 완벽.

## 발견된 문제점 및 한계

### ⚠️ 문제 ① — 라우팅 doc_type 세분류 미스 (53%)
`_UNSTRUCTURED_SIGNATURES["policy_manual"]` 키워드가 일부 문서(RoHS, CAPA, 유해물질 등)에서
매칭 점수 부족으로 `ambiguous_fallback_vlm`으로 빠짐.
**그러나 채널은 여전히 비정형(UNSTRUCTURED)으로 정상 라우팅되어 추출 자체는 수행됨.**
doc_type 세분류만 틀린 것이며, 추출 품질에 실질적 영향 없음.

- **수정 방향**: policy_manual 시그니처에 "절차서", "관리", "물질", "화학", "광물" 등 추가.

### ⚠️ 문제 ② — 정량 과추출 (unlisted_real=41건)
정성 문서의 참조 숫자("30인당 1개소", "5년 보존", "60시간")를 LLM이 ESG 지표로 추출.
이들은 원문에 실재하는 수치이므로 "환각"이 아닌 **"과추출(over-extraction)"**.
ESG 정량 지표와 규정 내 참조 수치를 구분하는 필터가 필요.

- **수정 방향**: doc_type이 정성류면 metric 추출 억제, 또는 "이 수치가 ESG 성과지표인가" 후필터.

### 📝 문제 ③ — 진짜 환각 4건 (스캔본 한정)
스캔본 OCR 경로에서만 발생. Upstage OCR의 문자 인식 오류가 존재하지 않는 숫자를 만들어내고
LLM이 이를 그대로 추출. 디지털 경로(pymupdf)에서는 true_halluc 0건.

### 📝 문제 ④ — ECE 0.6684 (캘리브레이션 신호 부재)
`_map_vlm_json`이 모든 metric에 상수 confidence=0.75를 부여.
실제 정답률 4/49 ≈ 8.2%. **이것은 캘리브레이션 불량이 아니라 캘리브레이션 신호 자체가 없는 것.**
LLM이 confidence를 출력하지 않아 판별력 있는 신호를 얻을 수 없는 구조적 한계.

### 📝 한계 ⑤ — Self-judge 편향
judge 모델이 추출 모델과 동일(gpt-4.1-mini). 자기 출력을 자기가 채점하므로
환각을 관대하게 판정할 수 있음. Anthropic key 설정 시 독립 모델로 교차 검증 가능.

### 📝 한계 ⑥ — 2인 독립 라벨 미완
gold의 `labeler2`/`agreement` 필드는 비어 있음.
**사람 검수 게이트**: 2인 독립 라벨 + 불일치 합의 + 일치율(%) 기록은 정민 검수 후 완료.

## 결론 및 절단선 권고

**정성 항목(clauses)은 자동화 가능**: judge 기준 recall 100%, 환각 1/97건(1.0%).
LLM이 원문에 충실하게 핵심사실을 추출하며, 의미 단위 환각이 거의 없음.
→ **HITL 없이 자동화 권고.** (단, self-judge 편향 한계로 독립 모델 교차검증 권장.)

**정량 항목은 조건부 자동화**:
- `doc_type = safety_minutes` (정량 있는 문서): 정답 100% 추출, FP는 원문 실값(환각 아님) → **자동화 가능.**
- `doc_type ∈ {policy_manual, hr_policy, emergency_manual}` (정성 문서):
  진짜 환각은 스캔본에서만 4건 발생. 디지털은 0건. 그러나 과추출(unlisted_real) 41건이
  다운스트림 오염 위험. → **doc_type 기반 metric 억제 필터 도입 후 자동화 전환 권고.**
  필터 도입 전까지는 HITL 유지.

판단이지 확정 아님 — 최종 결정은 정민.
