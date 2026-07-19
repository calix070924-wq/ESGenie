# 비정형 OCR(gpt-4.1-mini) 신뢰도 측정 결과 — 2026-07-17 (v4 — 사람 독립 라벨 반영; 측정 수치는 v3와 동일)

대상: 비정형 문서 15건(디지털 11 + 스캔본 4) / 채점 스크립트 `scripts/ocr_unstructured_eval.py`

## Before/After — v2 → v3 재측정 비교

| 지표 | v2 (2026-07-15) | v3 (2026-07-17) | 변경 사유 |
|---|---|---|---|
| 라우팅 정확도 | 8/15 (53%) | 8/15 (53%) | 동일 |
| 정량 정답 일치 | 4/4 (100%) | 4/4 (100%) | 동일 |
| 정량 FP | true_halluc=4, unlisted_real=41 | **true_halluc=6, unlisted_real=43** | FP 매칭을 숫자토큰 수치비교로 강화 (부분문자열→토큰) |
| 정성 recall (judge) | 101/101 (100.0%) | **119/119 (100.0%)** | gold 재도출(원문-only, 2패스), fact 수 증가 |
| 정성 환각률 (judge) | 1/97 (1.0%) | **2/87 (2.3%)** | judge_failed 분모 제외 + gold 재도출 |
| ECE | 0.6684 | 0.6745 | gold 재도출로 metric FP 분포 변동 |
| gold 라벨링 | 1인 AI (claude-code) | **AI 2패스(78%) + 사람 1인(장지민) 독립 라벨 → 정합 agreement_human 79.2%** | 순환오염 차단 + 사람 독립 검증 |
| judge 모델 | gpt-4.1-mini (self-judge) | gpt-4.1-mini (self-judge) | ANTHROPIC_API_KEY 미설정, 독립 불가 |
| FP 매칭 방식 | 부분문자열 검색 | **원문 숫자토큰 파싱 → 수치비교** | 60이 2060/60,000 안에서 오매칭 방지 |
| hit 판정 단위 | 미검증 | **단위 동의어 정규화 후 일치 확인** | 값 같고 단위 틀려도 hit → 수정 |
| raw_text_source | 미기록 | **문서별 pymupdf/upstage/mock 증명** | 디지털/스캔 경로 투명성 |

## 핵심 결론

| 축 | 지표 | 결과 | 판정 |
|---|---|---|---|
| 라우팅 | doc_type 분류 정확도 | **8/15 (53%)** | ⚠️ policy_manual 세분류 미스 (채널은 정상) |
| 정량 수치 | 정답 일치율 | **4/4 (100%)** | ✅ 정답 수치 전량 추출 |
| 정량 수치 | 진짜 환각 (원문에 없는 수치 창작) | **6건** | ⚠️ 스캔본 OCR 오인식 + 디지털 1건 |
| 정량 수치 | gold 미기재 실값 (원문에 있으나 ESG 지표 아님) | **43건** | 📝 환각 아닌 과추출 |
| 정성 충실도 | recall — LLM-judge | **119/119 (100.0%)** | ✅ 완벽 |
| 정성 충실도 | 환각률 — LLM-judge | **2/87 (2.3%)** (failed 0건 제외) | ⚠️ self-judge 편향 가능 |
| 캘리브레이션 | ECE (정량 metric confidence) | **0.6745** | ⚠️ 상수 conf=0.75 → 캘리브레이션 신호 자체가 부재 |

## 측정 환경

| 항목 | 값 |
|---|---|
| 추출 엔진 | gpt-4.1-mini-text (Azure, vision=False) |
| 판정(judge) 모델 | gpt-4.1-mini (**self-judge** — 추출 모델과 동일, 편향 가능) |
| 디지털 경로 | pymupdf 텍스트 추출 → LLM 정량·정성 동시 추출 |
| 스캔본 경로 | Upstage Document Parse OCR → LLM |
| 채점 정답셋 | `data/benchmark_ocr/unstructured_gold.json` (15문서, AI 2패스 + 사람 1인(장지민) 독립 검증) |
| 실행 모드 | `ESGENIE_STRICT=1` (API 실호출, mock 폴백 금지) |
| 정성 판정 방식 | (1) 토큰 오버랩 휴리스틱 (교차검증) + (2) **LLM-as-judge** (정식 판정) |
| judge 판정 원본 | `data/benchmark_ocr/judge_decisions.json` (감사 가능) |
| FP 매칭 기준 | 원문 숫자토큰(`\d[\d,]*\.?\d*`) 파싱 → 수치비교 (eps=max(0.01, val×1e-6)) |
| hit 단위 검증 | 동의어 정규화 맵(ton/t/톤, %/퍼센트 등) 적용, 단위 불일치 시 miss |
| 환각률 분모 | judged clauses = total_clauses − judge_failed_clauses |

### 정성 판정 방식 상세

- **LLM-judge (정식)**: 문서당 1회 호출. 원문+gold facts+추출 clauses를 함께 제시하고 (A) recall 커버리지와 (B) grounding 판정을 동시 수행. 판정 기준: "근거가 명확하지 않으면 unsupported".
- **휴리스틱 (교차검증)**: 토큰 오버랩 40%↑ = recall hit, 원문 토큰 30%↓ = 환각.
- 두 방식의 차이(환각률 0% vs 2.3%)가 "휴리스틱이 구조적으로 환각을 못 잡는다"는 검증 결과의 증거.
- **한계 (self-judge)**: judge가 추출 모델과 동일(gpt-4.1-mini). Self-judge 편향 가능. ANTHROPIC_API_KEY 설정 시 독립 모델(Claude)로 교차 검증 가능하나, 이번 측정에서는 키 미설정으로 **self-judge로 실행됨**. 결론의 단정 수준을 이에 맞춰 하향.

### Gold 라벨링 방식 (순환오염 차단)

- **Pass A**: 원문 PDF 텍스트(pymupdf)만 보고 독립 도출. LLM 추출 출력 미참조.
- **Pass B**: 별도 컨텍스트에서 원문만 보고 독립 도출. Pass A 결과 미참조.
- **AI 패스 간 Agreement**: 78.0% (69 matched / 88+89 facts, 디지털 11문서. 스캔 4개는 gold가 디지털 원본과 동일하여 제외).
- **사용된 gold**: Pass A를 base로 사용.
- **사람 독립 라벨 (완료)**: 장지민이 원문 PDF만 보고 독립 라벨(`라벨링패킷_지민.docx` → `labeling_worksheet.csv`, 88 facts). LLM 추출·gold 미참조.
- **정합 agreement_human = 79.2%** (합의 80 / 전체 101). 사람↔AI gold를 의미 기준으로 수동 정합(같은 사실이면 표현·부호·문장결합 차이 무시), 판정 근거 전량 `reconciliation_log.md`. 문서별 값은 gold의 `agreement_human`, 사람 원본은 `facts_gold_human`에 저장.
- 참고: 토큰매칭(strict)으로는 41%였으나 이는 사람의 패러프레이즈를 못 잡은 착시. 정합 79.2%는 AI 2패스(78%)와 근접 → **독립 사람이 gold 내용을 검증**함.
- **범위(정직)**: 사람 1인(장지민) 독립 라벨 = 리뷰어 최소요건("최소 1명") 충족. 작업지시서의 완전한 "2인 독립"은 2번째 사람 라벨 시 완성(선택).
- 프로비넌스: gold(commit A) → code(commit B) → 추출 결과(commit C) 순서로 git 히스토리에 선재.

## 문서별 상세

| 파일 | 유형(gold) | 라우팅 | 정량 hit | true_halluc | unlisted_real | recall(j) | 환각(j) | clauses | raw_source |
|---|---|---|---|---|---|---|---|---|---|
| 근로시간관리규정_2025 | policy_manual | ✅ | 0/0 | 0 | 4 | 8/8 | 0 | 5 | pymupdf |
| 문서기록관리규정_2025 | policy_manual | ✅ | 0/0 | 0 | 4 | 8/8 | 0 | 5 | pymupdf |
| 물질규제_RoHS_REACH_2025 | policy_manual | ❌ | 0/0 | 0 | 0 | 8/8 | 0 | 5 | pymupdf |
| 시정조치_CAPA절차서_2025 | policy_manual | ❌ | 0/0 | 1 | 5 | 8/8 | 0 | 5 | pymupdf |
| 위생_기숙사관리규정_2025 | policy_manual | ✅ | 0/0 | 0 | 6 | 8/8 | 0 | 11 | pymupdf |
| 유해물질관리규정_2025 | policy_manual | ❌ | 0/0 | 0 | 2 | 8/8 | 0 | 5 | pymupdf |
| 지식재산보호규정_2025 | policy_manual | ❌ | 0/0 | 0 | 0 | 7/7 | 0 | 5 | pymupdf |
| 책임광물실사정책_2025 | policy_manual | ❌ | 0/0 | 0 | 2 | 7/7 | 0 | 8 | pymupdf |
| safety_policy_2025 (회의록) | safety_minutes | ✅ | 2/2 | 0 | 3 | 8/8 | 1 | 5 | pymupdf |
| emergency_manual_2025 ★ | emergency_manual | ✅ | 0/0 | 0 | 7 | 8/8 | 0 | 6 | pymupdf |
| hr_policy_2025 ★ | hr_policy | ✅ | 0/0 | 0 | 4 | 10/10 | 0 | 7 | pymupdf |
| 근로시간관리규정_scan | policy_manual | ✅ | 0/0 | 3 | 1 | 8/8 | 0 | 5 | upstage |
| 유해물질관리규정_scan | policy_manual | ❌ | 0/0 | 0 | 2 | 8/8 | 0 | 5 | upstage |
| 책임광물실사정책_scan | policy_manual | ❌ | 0/0 | 1 | 1 | 7/7 | 0 | 5 | upstage |
| safety_policy_scan (회의록) | safety_minutes | ✅ | 2/2 | 1 | 2 | 8/8 | 1 | 5 | upstage |

★ = synthetic 문서 (실문서 부재로 생성, gold에 `synthetic: true` 표시)

## 디지털 vs 스캔본 비교

| 경로 | raw_text_source | 라우팅 | 정성 recall(judge) | 정성 환각률(judge) | 정량 true_halluc |
|---|---|---|---|---|---|
| 디지털 (11건) | **pymupdf** (전건) | 6/11 (55%) | 88/88 (100.0%) | 1/67 (1.5%) | 1건 |
| 스캔본 (4건) | **upstage** (전건) | 2/4 (50%) | 31/31 (100.0%) | 1/20 (5.0%) | 5건 |

**경로 증명**: `eval_results.json`의 `raw_text_source` 필드로 확인.
- 디지털 11건: 전부 pymupdf (텍스트 레이어 존재, len=394~699 chars)
- 스캔본 4건: 전부 upstage (텍스트 레이어 0 → Upstage Document Parse OCR 경유, len=409~649 chars)
- mock 폴백: 0건. 전문서 실API 경유 확인됨.

스캔본의 true_halluc이 디지털보다 높은 것은 OCR 오인식에서 비롯된 수치(문자 깨짐 → 존재하지 않는 숫자 추출).
정성 추출은 디지털/스캔 모두 recall 100%.

## 발견된 문제점 및 한계

### ⚠️ 문제 ① — 라우팅 doc_type 세분류 미스 (53%)
`_UNSTRUCTURED_SIGNATURES["policy_manual"]` 키워드가 일부 문서(RoHS, CAPA, 유해물질 등)에서
매칭 점수 부족으로 `ambiguous_fallback_vlm`으로 빠짐.
**그러나 채널은 여전히 비정형(UNSTRUCTURED)으로 정상 라우팅되어 추출 자체는 수행됨.**
doc_type 세분류만 틀린 것이며, 추출 품질에 실질적 영향 없음.

- **수정 방향**: policy_manual 시그니처에 "절차서", "관리", "물질", "화학", "광물" 등 추가.

### ⚠️ 문제 ② — 정량 과추출 (unlisted_real=43건)
정성 문서의 참조 숫자("30인당 1개소", "5년 보존", "60시간")를 LLM이 ESG 지표로 추출.
이들은 원문에 실재하는 수치이므로 "환각"이 아닌 **"과추출(over-extraction)"**.
ESG 정량 지표와 규정 내 참조 수치를 구분하는 필터가 필요.

- **수정 방향**: doc_type이 정성류면 metric 추출 억제, 또는 "이 수치가 ESG 성과지표인가" 후필터.

### ⚠️ 문제 ③ — 진짜 환각 6건
- 디지털: 1건 (capa_2025). FP 매칭 강화(숫자토큰 비교)로 v2에서 miss → v3에서 발견.
- 스캔본: 5건. Upstage OCR 문자 인식 오류가 존재하지 않는 숫자를 만들어내고 LLM이 이를 그대로 추출.

### 📝 문제 ④ — ECE 0.6745 (캘리브레이션 신호 부재)
`_map_vlm_json`이 모든 metric에 상수 confidence=0.75를 부여.
실제 정답률 4/53 ≈ 7.5%. **이것은 캘리브레이션 불량이 아니라 캘리브레이션 신호 자체가 없는 것.**
LLM이 confidence를 출력하지 않아 판별력 있는 신호를 얻을 수 없는 구조적 한계.

### ⚠️ 한계 ⑤ — Self-judge 편향 (미해소)
judge 모델이 추출 모델과 동일(gpt-4.1-mini). 자기 출력을 자기가 채점하므로
환각을 관대하게 판정할 수 있음. 이번 측정에서 ANTHROPIC_API_KEY가 미설정되어
**독립 모델 교차검증을 실행하지 못함.** 코드는 Anthropic 키 존재 시 자동으로 독립 judge를 사용하도록
구현 완료(`_build_judge_client`).

### ✅ 항목 ⑥ — 사람 독립 라벨 완료 (블로커① 해소)
gold를 AI 2패스 독립 도출로 재작성(순환오염 차단)한 뒤, 사람(장지민)이 원문만 보고 독립 라벨 → 의미 기준 정합.
- AI 패스 간 agreement: 78.0%
- 사람↔AI 정합 **agreement_human = 79.2%** (`labeler2_human: 장지민`, 근거 `reconciliation_log.md`)
- 리뷰어 요건("최소 1명 원문만 보고 독립 재라벨 + agreement 기록") 충족. 완전한 "2인 독립"은 2번째 사람 라벨 시 완성(선택).

### 📝 발견 ⑦ — 사람 라벨이 잡은 AI gold 누락 실사실 13건
사람 독립 라벨 과정에서, 원문에 실재하지만 AI gold가 놓친 핵심사실 13건을 발견(gold ~13% 불완전).
예: 개인정보 재직중+퇴직후 3년 보존 · 혼합보관 금지 물질 별도 구획 · 코발트 별도 실사(OECD 3단계) ·
고충 접수 14일 조사·30일 통보 · 소방장비 점검주기 등. 전체 목록 `reconciliation_log.md`.
반대로 AI만 잡은 8건은 대부분 목적·범위·절차 문구(사람이 합리적으로 생략).
→ 본 측정은 현 gold 기준으로 확정(문서화). gold 보강은 향후 개선 후보(보강 시 recall 재측정 필요).

## 결론 및 절단선 권고

### 정성 항목(clauses)
- judge 기준 recall **119/119 (100%)**, 환각 **2/87 (2.3%)**.
- LLM이 원문에 충실하게 핵심사실을 추출하며, 의미 단위 환각이 매우 낮음.
- **단, self-judge 판정이므로 결론을 단정할 수 없음.**
- → **독립 모델(Claude 등) 교차검증을 선행 조건으로 권고**; 그 전까지 정성도 **샘플 HITL 병행**.
- 독립 judge에서도 환각률 5% 이하로 확인되면 자동화 전환 가능.

### 정량 항목
- `doc_type = safety_minutes` (정량 있는 문서): 정답 100% 추출, 단위도 일치 → **자동화 가능 (단위 검증 필터 추가 완료).**
- `doc_type ∈ {policy_manual, hr_policy, emergency_manual}` (정성 문서):
  true_halluc 6건 발생(대부분 스캔본). 과추출(unlisted_real) 43건이 다운스트림 오염 위험.
  → **doc_type 기반 metric 억제 필터 도입 후 자동화 전환 권고.** 필터 도입 전까지는 HITL 유지.

### 잔여 선행 조건
1. **독립 judge 교차검증** (ANTHROPIC_API_KEY 설정 후 재측정) — self-judge 편향 제거
2. **2번째 사람 독립 라벨** (선택 — 현재 1인(장지민) 완료로 리뷰어 최소요건 충족)
3. **policy_manual 시그니처 보강** (라우팅 53% → 목표 80%+)
4. (선택) 사람이 발견한 13건 gold 보강 후 recall 재측정

판단이지 확정 아님 — 최종 결정은 정민.
