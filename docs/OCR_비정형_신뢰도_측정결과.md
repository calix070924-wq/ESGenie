# 비정형 OCR(gpt-4.1-mini) 신뢰도 측정 결과 — 2026-07-17 (v5 — 사람 gold + 독립 judge 반영)

대상: 비정형 문서 15건(디지털 11 + 스캔본 4) / 채점 스크립트 `scripts/ocr_unstructured_eval.py`

> v5 핵심 변화: (1) 정답셋(gold)을 **사람(장지민)이 원문만 보고 작성한 118개**로 교체(순환오염 차단),
> (2) recall을 **사람(장지민) 판정 + 독립 judge(Gemini, 다른 벤더) 교차검증(두 판정 일치)** 으로 재측정,
> (3) 사람↔AI 일치율을 **결정적 스크립트**로 재현 가능하게 고정.

## Before/After — v4 → v5

| 지표 | v4 | v5 | 변경 사유 |
|---|---|---|---|
| 채점 gold | AI 2패스 base | **사람(장지민) 원문 독립 라벨 118개** | 순환오염·누락 동시 차단 (AI가 놓친 13건 포함) |
| 정성 recall | self-judge 119/119 (100%) | **사람(장지민)+독립 judge(Gemini) 일치 116/118 (98.3%)** / 휴리스틱 93/118 (78.8%) | self-judge → 사람+독립 judge, 사람 gold로 재측정 |
| recall 판정 | self-judge (gpt-4.1-mini) | **사람(장지민) + 독립 judge(Gemini) 교차검증(일치)** + Cowork Claude 검증 | self-judge 편향 제거 |
| 사람↔AI 일치율 | 수기 79.2% (재현 불가) | **스크립트 83.0% (재현 가능)** | `human_ai_agreement.py`, `--write` 없이 결정적 |
| 정량 FP | true_halluc=6, unlisted_real=43 | true_halluc=5, unlisted_real=39 | 사람 gold + 재추출(LLM 비결정 변동) |

## 핵심 결론

| 축 | 지표 | 결과 | 판정 |
|---|---|---|---|
| 라우팅 | doc_type 분류 정확도 | **8/15 (53%)** | ⚠️ policy_manual 세분류 미스 (채널은 정상 라우팅, 추출은 수행됨) |
| 정량 수치 | 정답 일치율 | **4/4 (100%)** | ✅ 정답 수치 전량 추출(값·단위) |
| 정량 수치 | 진짜 환각 (원문에 없는 수치 창작) | **5건** | ⚠️ 대부분 스캔본 OCR 오인식 |
| 정량 수치 | 과추출 (원문 실값이나 ESG 지표 아님) | **39건** | 📝 환각 아님, 다운스트림 필터 필요 |
| 정성 충실도 | recall — **사람(장지민)+독립 judge(Gemini) 일치** | **116/118 (98.3%)** | ✅ 높음(사람·독립 LLM 일치). 단 아래 한계 ①② 참조 |
| 정성 충실도 | recall — 휴리스틱(교차검증) | 93/118 (78.8%) | 어휘 매칭 하한(패러프레이즈 놓침) |
| 정성 충실도 | 환각률 | **0/93 (0.0%)** | ✅ 원문에 없는 조항 생성 없음 |
| 캘리브레이션 | ECE (정량 metric confidence) | **0.6667** | ⚠️ 상수 conf=0.75 → 캘리브레이션 신호 자체가 부재 |

## 측정 환경

| 항목 | 값 |
|---|---|
| 추출 엔진 | gpt-4.1-mini-text (Azure AI Foundry, vision=False) |
| **정성 recall 판정** | **사람(장지민) 판정 + 독립 judge(Gemini, 다른 벤더) 교차검증(일치) + Cowork Claude 수기 검증** |
| 디지털 경로 | pymupdf 텍스트 추출 → LLM 정량·정성 동시 추출 (11건, 경로 증명 `raw_text_source=pymupdf`) |
| 스캔본 경로 | Upstage Document Parse OCR → LLM (4건, `raw_text_source=upstage`, mock 폴백 0건) |
| 채점 정답셋 | `data/benchmark_ocr/unstructured_gold.json` — `facts_gold` = 사람(장지민) 원문 독립 라벨 118개. AI 2패스는 `facts_gold_ai`/`gold_passA·B.json`에 보존 |
| 실행 모드 | `ESGENIE_STRICT=1` (API 실호출, mock 폴백 금지) |
| recall 판정 원본 | `data/benchmark_ocr/human_recall_judge.csv` (문서·사실·AI조항·O/X·이유) |
| 사람↔AI 일치율 | `scripts/human_ai_agreement.py` (결정적·재현 가능) = **83.0%** |
| FP 매칭 기준 | 원문 숫자토큰 파싱 → 수치비교 (60이 2060/60,000 안에서 오매칭 방지) |
| hit 단위 검증 | 동의어 정규화 맵(ton/t/톤, %/퍼센트 등) 적용, 단위 불일치 시 miss |

### 정성 recall — 3가지 판정 비교 (핵심)

| 판정 방식 | recall | 성격 |
|---|---|---|
| self-judge (gpt-4.1-mini, 추출=판정 동일) | 118/118 (100.0%) | 자기채점 — 신뢰 불가(상한) |
| **사람(장지민) + 독립 judge(Gemini) 일치** | **116/118 (98.3%)** | **정식 값** — 사람 판정과 독립 LLM이 동일, 자기채점과도 근접 → 편향 아님 확인 |
| 휴리스틱 (토큰 오버랩) | 93/118 (78.8%) | 어휘 매칭 하한 — 패러프레이즈를 놓침 |

- **판정·검증**: 장지민이 원문·조항을 대조해 판정 → 독립 judge(Gemini, 다른 벤더)로 교차검증, **두 판정이 116/118로 일치**. Cowork Claude가 휴리스틱이 "누락 의심"한 21건을 **전체 조항과 전수 수기 대조** → 전부 실제로는 조항에 존재(어휘 차이로 놓친 것), 미추출은 2건뿐.
- **미추출 2건**: `safety_minutes`(디지털·스캔) "다음 위원회 회의 일정(2025-06-15)" — 회의록의 절차성 세부가 추출에서 누락됨.
- **판정 방식 정직 고지**: 사람 판정(장지민) + 독립 LLM(Gemini) 교차검증. 스크립트 원터치 재현은 아니나 판정 기록(`human_recall_judge.csv`, O/X·이유)으로 감사 가능. (작업지시서 "사람 판정 또는 LLM-as-judge" 명시 허용.)

### Gold 라벨링 (순환오염 차단)

- **facts_gold = 사람(장지민) 원문 독립 라벨 118개.** 원문 PDF만 보고 작성(`라벨링패킷_지민.docx` → `labeling_worksheet.csv`), **LLM 추출·기존 AI gold 미참조.**
- AI가 놓쳤던 실사실 13건이 사람 라벨에 포함되어 gold 누락도 해소.
- 기존 AI 2패스 라벨은 `facts_gold_ai`/`gold_passA·B.json`에 보존(비교용).
- **사람↔AI 일치율 = 83.0%** (`human_ai_agreement.py`, 접두어 토큰 Dice, 결정적·재현 가능). 수기 79.2%는 폐기.
- 범위(정직): 사람 1인(장지민) 독립 작성 = 리뷰어 최소요건("최소 1명") 충족. 완전한 "2인 독립"은 2번째 사람 작성 시(선택).

## 문서별 상세 (recall = 독립 judge/검증)

| 문서 | 유형 | 라우팅 | 정량 hit | true_halluc | 과추출 | recall(독립) | 환각 | raw_source |
|---|---|---|---|---|---|---|---|---|
| 근로시간관리규정 | policy_manual | ✅ | 0/0 | 0 | 4 | 8/8 | 0 | pymupdf |
| 문서기록관리규정 | policy_manual | ✅ | 0/0 | 0 | 0 | 9/9 | 0 | pymupdf |
| 물질규제_RoHS_REACH | policy_manual | ❌ | 0/0 | 0 | 0 | 8/8 | 0 | pymupdf |
| 시정조치_CAPA | policy_manual | ❌ | 0/0 | 1 | 5 | 7/7 | 0 | pymupdf |
| 위생_기숙사관리 | policy_manual | ✅ | 0/0 | 0 | 7 | 9/9 | 0 | pymupdf |
| 유해물질관리 | policy_manual | ❌ | 0/0 | 0 | 2 | 7/7 | 0 | pymupdf |
| 지식재산보호 | policy_manual | ❌ | 0/0 | 0 | 0 | 8/8 | 0 | pymupdf |
| 책임광물실사 | policy_manual | ❌ | 0/0 | 0 | 0 | 8/8 | 0 | pymupdf |
| safety_minutes(회의록) | safety_minutes | ✅ | 2/2 | 0 | 3 | **6/7** | 0 | pymupdf |
| emergency_manual ★ | emergency_manual | ✅ | 0/0 | 0 | 7 | 7/7 | 0 | pymupdf |
| hr_policy ★ | hr_policy | ✅ | 0/0 | 0 | 4 | 10/10 | 0 | pymupdf |
| 근로시간_scan | policy_manual | ✅ | 0/0 | 3 | 1 | 8/8 | 0 | upstage |
| 유해물질_scan | policy_manual | ❌ | 0/0 | 0 | 2 | 7/7 | 0 | upstage |
| 책임광물_scan | policy_manual | ❌ | 0/0 | 1 | 1 | 8/8 | 0 | upstage |
| safety_minutes_scan | safety_minutes | ✅ | 2/2 | 0 | 3 | **6/7** | 0 | upstage |

★ = synthetic 문서. recall 미달은 safety_minutes 2건(회의 일정)뿐.

## 발견된 한계 (정직 — 우리가 먼저 명시)

### ⚠️ 한계 ① — 높은 recall은 "near-verbatim 추출"의 산물 (중요)
AI가 뽑은 조항(`ai_clauses`)을 보면 문서의 제1조·제2조를 **거의 그대로 복사**한다. gold 사실도 같은 문서에서
나왔으므로, 내용이 조항에 존재하는 것은 사실상 당연 → **recall이 높게 나오는 것은 "내용 보존"이지
"지능적 구조화"가 아니다.** 즉 98.3%는 "AI가 내용을 잃지 않는다"는 증거일 뿐, "정성 항목을 깔끔히
구조화한다"는 증거가 아니다. K-ESG/RBA 항목 단위로 쪼개고 매핑하는 것은 별도 후처리가 필요하다.

### ⚠️ 한계 ② — 벤치마크가 좁고 균질 (일반화 미검증)
15개 전부 한울정밀 데모셋(짧고 깔끔한 규정문서, 일부 합성). 실제 ESG 문서의 다양성(장문 서술형,
복잡한 표, 저품질 스캔)을 포함하지 않는다. 따라서 본 수치(recall 98%, 환각 0%)는 **이 균질한 집합에
대한 값**이며, 다양한 실문서로의 일반화는 아직 검증되지 않았다. → 벤치마크 확대 필요.

### ⚠️ 한계 ③ — self-judge는 신뢰 불가(해소됨)
추출·판정을 같은 gpt-4.1-mini로 하면 자기채점이라 recall 100%가 나온다. v5에서 **사람(장지민) 판정 +
독립 judge(Gemini)** 로 재측정해 이 편향을 제거했다. 사람·독립 LLM 두 판정이 98.3%로 일치하고
self값(100%)과도 근접해, 높은 recall이 편향 때문만은 아님이 확인됐다.

### 📝 한계 ④ — ECE 0.6667 (캘리브레이션 신호 부재)
모든 metric에 상수 confidence=0.75가 부여되어 판별력이 없다. LLM이 confidence를 출력하지 않는
구조적 한계로, "캘리브레이션 불량"이 아니라 "신호 자체가 없음"이다.

### 📝 한계 ⑤ — 라우팅 doc_type 세분류 53%
policy_manual 키워드 부족으로 일부 문서가 `ambiguous_fallback_vlm`로 빠짐. **채널은 비정형으로 정상
라우팅되어 추출은 수행됨** — doc_type 라벨만 부정확. 시그니처 보강으로 개선 가능.

## 결론 및 절단선 권고 (판단이지 확정 아님 — 최종 결정 정민)

### 정성 항목(clauses)
- **내용 충실도는 신뢰 가능**: 사람(장지민)+독립 judge(Gemini) 일치 recall **98.3%**, 환각 **0%**. AI가 원문 내용을
  빠뜨리거나 지어내지 않음이 다른 벤더 모델로도 확인됨.
- **단, 두 조건**: ① 추출이 조 단위 통짜라 **항목 구조화·K-ESG/RBA 매핑은 HITL/후처리 병행**,
  ② 한울정밀 데모셋 국한 → **다양한 실문서로 벤치마크 확대 후 최종 확정.**
- → **"정성 내용 추출 자동화는 유망하되, 항목 구조화 HITL + 벤치마크 확대를 선행 조건"** 으로 권고.

### 정량 항목(metrics)
- `doc_type = safety_minutes`(수치가 실재하는 문서): 정답 4/4, 단위 일치 → **자동화 가능.**
- 규정류: 과추출 39건 + doc_type 라우팅 미스 → **doc_type 기반 metric 억제 필터 도입 전까지 HITL 유지.**

### 잔여 개선 과제
1. 벤치마크 확대(다양한 실 ESG 문서 — 장문·표·저품질 스캔) → 일반화 재확인
2. 추출을 조 단위 통짜 → K-ESG/RBA 항목 단위로 구조화
3. policy_manual 시그니처 보강(라우팅 53% → 목표 80%+)
4. 정량 doc_type 기반 억제 필터(과추출 차단)
5. (선택) 2번째 사람 독립 라벨로 완전한 "2인 독립" 완성
