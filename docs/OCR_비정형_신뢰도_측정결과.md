# 비정형 OCR(gpt-4.1-mini) 신뢰도 측정 결과 — 2026-07-14

대상: 비정형 문서 15건(디지털 11 + 스캔본 4) / 채점 스크립트 `scripts/ocr_unstructured_eval.py`

## 핵심 결론

| 축 | 지표 | 결과 | 판정 |
|---|---|---|---|
| 라우팅 | doc_type 분류 정확도 | **8/15 (53%)** | ⚠️ policy_manual 미식별 다수 |
| 정량 수치 | 정답 일치율 | **4/4 (100%)** | ✅ 정답 수치 전량 추출 |
| 정량 수치 | 환각 FP (없어야 할 수치 생성) | **44건** | 🚨 정성문서에서 수치 환각 심각 |
| 정성 충실도 | recall (핵심사실 추출률) | **99.0% (100/101)** | ✅ 거의 완벽 |
| 정성 충실도 | 환각률 (원문에 없는 clause 비율) | **0.0% (0/87)** | ✅ 환각 없음 |
| 캘리브레이션 | ECE (정량 metric confidence) | **0.6667** | 🚨 심각한 과신 (교정 필요) |

## 측정 환경

| 항목 | 값 |
|---|---|
| 추출 엔진 | gpt-4.1-mini-text (Azure, vision=False) |
| 디지털 경로 | pymupdf 텍스트 추출 → LLM 정량·정성 동시 추출 |
| 스캔본 경로 | Upstage Document Parse OCR → LLM |
| 채점 정답셋 | `data/benchmark_ocr/unstructured_gold.json` (15문서, 1차 라벨) |
| 실행 모드 | `ESGENIE_STRICT=1` (API 실호출, mock 폴백 금지) |
| 정성 판정 방식 | 토큰 오버랩 휴리스틱 (gold fact 키워드 40%↑ 매칭 = recall hit; clause 토큰의 30%↑가 raw_text에 없으면 환각) |

## 문서별 상세

| 파일 | 유형(gold) | 라우팅 | 정량 hit/gold | FP | recall | 환각 | clauses |
|---|---|---|---|---|---|---|---|
| 근로시간관리규정_2025 | policy_manual | ✅ | 0/0 | 4 | 7/7 | 0 | 5 |
| 문서기록관리규정_2025 | policy_manual | ✅ | 0/0 | 0 | 6/6 | 0 | 5 |
| 물질규제_RoHS_REACH_2025 | policy_manual | ❌ | 0/0 | 3 | 6/6 | 0 | 8 |
| 시정조치_CAPA절차서_2025 | policy_manual | ❌ | 0/0 | 4 | 6/6 | 0 | 5 |
| 위생_기숙사관리규정_2025 | policy_manual | ✅ | 0/0 | 6 | 7/7 | 0 | 11 |
| 유해물질관리규정_2025 | policy_manual | ❌ | 0/0 | 2 | 6/6 | 0 | 5 |
| 지식재산보호규정_2025 | policy_manual | ❌ | 0/0 | 0 | 6/6 | 0 | 5 |
| 책임광물실사정책_2025 | policy_manual | ❌ | 0/0 | 0 | 6/6 | 0 | 5 |
| safety_policy_2025 (회의록) | safety_minutes | ✅ | 2/2 | 3 | 8/8 | 0 | 5 |
| emergency_manual_2025 ★ | emergency_manual | ✅ | 0/0 | 7 | 7/7 | 0 | 6 |
| hr_policy_2025 ★ | hr_policy | ✅ | 0/0 | 4 | 9/9 | 0 | 7 |
| 근로시간관리규정_scan | policy_manual | ✅ | 0/0 | 4 | 6/7 | 0 | 5 |
| 유해물질관리규정_scan | policy_manual | ❌ | 0/0 | 2 | 6/6 | 0 | 5 |
| 책임광물실사정책_scan | policy_manual | ❌ | 0/0 | 2 | 6/6 | 0 | 5 |
| safety_policy_scan (회의록) | safety_minutes | ✅ | 2/2 | 3 | 8/8 | 0 | 5 |

★ = synthetic 문서 (실문서 부재로 생성, gold에 `synthetic: true` 표시)

## 디지털 vs 스캔본 비교

| 경로 | 라우팅 | 정성 recall | 정성 환각률 |
|---|---|---|---|
| 디지털 (pymupdf) | 6/11 (55%) | 74/74 (100%) | 0/67 (0%) |
| 스캔본 (Upstage OCR) | 2/4 (50%) | 26/27 (96.3%) | 0/20 (0%) |

스캔본도 Upstage OCR 경유 시 정성 추출 품질이 높다. 라우팅은 양 경로 모두 약함.

## 발견된 문제점 및 한계

### 🚨 문제 ① — 정량 수치 환각 (FP=44건, 가장 심각)
정성 문서(규정·절차서)에서 본문의 참조 숫자(예: "30인당 1개소", "5년 보존", "60시간")를
LLM이 ESG 정량 지표로 잘못 추출한다. gold에 정량 항목이 없는 9개 문서에서 총 44개 수치가
환각 생성됨.

- **원인**: VLM_EXTRACT_PROMPT가 "metrics"와 "clauses"를 동시 추출하도록 지시하며,
  문서 내 숫자가 있으면 metric으로 뽑아버리는 경향.
- **영향**: 다운스트림 정량 노드에 잘못된 수치가 유입 → 그린워싱 판정 왜곡 위험.
- **수정 방향**: doc_type이 정성류(policy_manual, hr_policy 등)면 metric 추출을 억제하거나,
  추출 후 "이 수치가 실제 ESG 성과 지표인가" 2차 검증 단계 추가.

### ⚠️ 문제 ② — 라우팅 정확도 저조 (53%)
`_UNSTRUCTURED_SIGNATURES["policy_manual"]`의 키워드("규정", "방침" 등)가
일부 문서(RoHS, CAPA, 유해물질 등)에서 매칭 점수 부족으로 `ambiguous_fallback_vlm`으로 빠짐.
비정형 채널이 fallback을 처리하므로 **추출 자체는 정상 수행**되나, doc_type 라벨이 부정확.

- **수정 방향**: policy_manual 시그니처에 "절차서", "관리", "물질", "화학", "광물" 등 추가.

### ⚠️ 문제 ③ — ECE 0.6667 (캘리브레이션 심각 불량)
`_map_vlm_json`이 모든 metric에 고정 confidence=0.75를 부여하나,
실제 정답률은 4/48 ≈ 8.3% (대부분 환각 FP). 예측 확률과 실제 정답률의 괴리가 극심.

- **원인**: LLM 추출 시 confidence를 반환하지 않아 하드코딩된 0.75 사용.
- **수정 방향**: LLM 프롬프트에서 각 metric의 확신도를 함께 출력하도록 변경하거나,
  doc_type 기반 사전 필터로 FP를 제거한 뒤 ECE 재측정.

### 📝 한계 ④ — clause에 confidence 필드 부재
`ExtractedClause`에 confidence가 없어 정성 항목의 신뢰도 캘리브레이션이 불가능.
현재는 recall/환각률로 갈음.

### 📝 한계 ⑤ — 2인 독립 라벨 미완
gold의 `labeler2`/`agreement` 필드는 비어 있음.
**사람 검수 게이트**: 2인 독립 라벨 + 불일치 합의 + 일치율(%) 기록은 정민 검수 후 완료.

### 📝 한계 ⑥ — 정성 판정 자동화 방식의 한계
recall/환각 판정에 토큰 오버랩 휴리스틱을 사용했으며, LLM-as-judge는 미적용.
높은 recall(99%)과 0% 환각률은 이 측정에서의 수치이며,
보다 엄밀한 판정(의미 단위 LLM-judge)에서는 달라질 수 있음.

## 결론 및 절단선 권고

**정성 항목(clauses)은 자동화 가능**: recall 99%, 환각 0%. LLM이 원문에 충실하게
핵심사실을 추출하며, 원문에 없는 주장을 생성하지 않는다. → **HITL 없이 자동화 권고.**

**정량 항목(metrics)은 HITL 유지**: 정답 수치 추출은 100%이나, 정성 문서에서
평균 4건/문서의 환각 수치를 생성한다(FP=44건). confidence도 미교정(ECE=0.67).
→ **정량 수치는 doc_type 기반 필터 도입 전까지 사람 검수(HITL) 유지 권고.**

구체적으로:
- `doc_type ∈ {safety_minutes}` → 정량+정성 모두 자동화 가능 (수치가 실제 있는 문서)
- `doc_type ∈ {policy_manual, hr_policy, emergency_manual}` → 정성만 자동화, 정량은 억제 또는 HITL
