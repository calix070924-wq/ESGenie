# 일반 문서 AI 대조군 — 실행기 (작업지시서 B §4.2)

**상태: 실행기 구현 완료(공통 답안 형식 A v1.1 확정, c1bfa67, 2026-10-10) · 유료 API 호출 0건 · 비교 결과 없음.**
**2026-10-13 결정: API 대조군(gpt-4.1-mini·gpt-4.1)은 Azure 크레딧 소진으로 실행하지 않는다.
대신 타 공급자 대조군을 사람이 claude.ai 채팅에서 수동으로 실행한다(§9).**
이 문서는 실행기의 조건과 기록 규칙을 적는다. 결과·비교표는 실제 실행 뒤에 추가한다.

- 실행기: `scripts/baseline_generic_llm.py`
- 테스트: `tests/test_baseline_generic_llm.py` (LLM 은 mock, 네트워크 호출 없음)
- 사례 사전 등록: `data/eval/cases/baseline_cases_v1.csv` + `..._criteria.md` (커밋 `e49e69a`, 수정하지 않는다)
- 프롬프트: `data/eval/prompts/baseline_prompt_v1.md` — **사전 등록됨**(커밋 `2717880`, 2026-10-13,
  sha256 `5b6cd37e429dd83959d5422532533d0493cec6cfc418afce397d8af37cd4a0b9`, 첫 실행 전).
  실행 후에 고치지 않는다. 고쳐야 하면 `baseline_prompt_v2.md` 를 새로 만든다

---

## 1. 비교 조건

| 항목 | 값 |
|---|---|
| 자료 | 팀이 만든 **가상 시연 세트** `한울정밀_촬영세트_정상5건보강_20261007` |
| 문항 | `rba42` 48행 (`Question.qid`·`Question.text` 원문) |
| ESGenie 기준 실행 | `LIVE_n5_d8a0de8` (initial·followup 둘 다) |
| 입력 텍스트 원천 | **ESGenie 가 같은 실행에서 쓴 OCR 결과** — `pipeline.json` 의 `ocr_extractions[].raw_text` |
| 회사 답변(05) | `esgenie/supplychain/claims.py::_extract_text` 로 추출한 PDF 원문 (증빙 OCR 경로 아님) |
| temperature | API 경로 0 / **수동 채팅 경로는 `null`**(채팅 기본값, 사람이 제어 못 함) |
| **실행할 대조군** | **타 공급자 1종 — claude.ai 채팅 수동 실행, 단계당 3회(총 6회)**(§9) |
| 실행하지 않는 대조군 | 동일 모델(`gpt-4.1-mini` 5회)·상위 모델(`gpt-4.1` 3회) — **예산 소진**(Azure 크레딧, 2026-10-13 결정) |
| 사전 등록 | 사례 `e49e69a`·프롬프트 `2717880` **그대로** 쓴다(수정하지 않는다) |
| `dataset_tag` | `virtual` (확정, A c1bfa67, 2026-10-10) |
| `material_kind` | `mixed` (확정, A c1bfa67, 2026-10-10 — B 제시값을 A 가 그대로 받음) |
| 단계별 `data_source` | initial `촬영증빙_17건` / followup `촬영증빙_17건+보완_1건` (확정, A c1bfa67) |

**표현 규칙:** 결과를 쓸 때 "팀이 만든 가상 세트, 특정 프롬프트 기준"을 함께 적는다.
"일반 AI 대비 N배" 같은 일반화는 쓰지 않는다.

## 2. 실행 방법

```bash
# 입력만 만들고 호출하지 않는다(조립·쪽 복원 확인용). 컨텍스트 한도를 몰라도 된다.
python scripts/baseline_generic_llm.py \
  --pipeline-json <run>/initial/pipeline.json --stage initial \
  --framework rba42 --model <모델 식별자> --provider openai \
  --repeats 1 --out outputs/eval/baseline/<run_id> --dry-run

# 실제 실행 — 컨텍스트 한도가 표에 없으면 --max-input-tokens 를 줘야 한다
python scripts/baseline_generic_llm.py \
  --pipeline-json <run>/initial/pipeline.json --stage initial \
  --framework rba42 --model <모델 식별자> --provider openai \
  --repeats 5 --out outputs/eval/baseline/<run_id> \
  --max-input-tokens <확인한 한도>

# 저장한 응답만으로 공통 형식을 다시 만든다(호출 없음)
python scripts/baseline_generic_llm.py --rescore-only outputs/eval/baseline/<run_id>

# 수동 채팅 실행(§9) — API 호출 없음
python scripts/baseline_generic_llm.py --provider manual \
  --pipeline-json <run>/initial/pipeline.json --stage initial \
  --repeats 3 --out outputs/eval/baseline                  # 붙여넣기 입력 내보내기
python scripts/baseline_generic_llm.py --provider manual \
  --import outputs/eval/baseline/MANUAL_claude_initial_rep1  # 사람이 넣은 답 가져오기
```

공통 답안 형식 모듈은 인자 없이도 찾는다(§7 해석 순서). 다른 판으로 검증하려면
`--answer-format <경로>` 또는 환경변수 `ESGENIE_EVAL_ANSWER_FORMAT`,
다른 커밋으로 바꾸려면 `--answer-format-ref <git ref>` 를 쓴다.
**못 찾으면 검증을 건너뛰지 않고 실패한다.**

## 3. 저장물

`<out>/<run_id>/`

| 파일 | 내용 |
|---|---|
| `input_manifest.json` | 문서별 쪽 복원 방식·쪽 수·원본 sha256, Upstage 쪽 교차확인, 05 추출 기록, 입력 길이 |
| `company_answer_extracted.txt` | **05 추출 원문 전체** (요약·정제하지 않은 그대로) |
| `rep_<k>/request.json` | 프롬프트 sha256·모델·파라미터·입력 길이·쪽 복원 방식. **키 값은 저장하지 않는다** |
| `rep_<k>/response_raw.json` | 응답 원문 전체 + 호출·완료 시각(KST) |
| `rep_<k>/common_v1_<stage>.json` | A 공통 답안 형식 v1 (JSON 객체 1개 = 한 시스템·한 실행·한 단계) |
| `rep_<k>/run_log.json` | 토큰·시각·파싱 집계·검증 결과·answer_format sha256 |
| `rep_<k>/validate_issues.json` | 검증 실패 목록(있을 때만) |
| `repeat_summary.json` | 반복 간 판정이 바뀐 행 수와 qid 목록 |

반복마다 `meta.run_id` 가 다르다(`<base>_rep<k>`). 한 파일에 여러 실행을 합치지 않는다.

## 4. 쪽 구분 복원

`ocr_extractions[].raw_text` 에는 쪽 구분이 없다. 아래 순서로 복원하고 **1-기준**으로 적는다.

| 방식 | 조건 | 근거 |
|---|---|---|
| `chunk_sources` | `router_meta.chunk_sources` 의 `page`·`line_start`·`line_end` 줄 수 합이 `raw_text` 줄 수와 **정확히 일치**하고 `context_repeated` 가 없을 때 | 비정형 채널은 쪽 단위로 쪼개 호출한다 |
| `single_page_upstage` | `router_meta.dp_response.usage.pages == 1` | 정형 채널(Upstage document-parse). `raw_upstage/*.json` 의 `body.usage.pages`·`body.elements[].page` 로 교차 확인한다 |
| `unrecovered` | 위 둘이 안 될 때 | 블록에 `쪽: 미상` 으로 적고, 그 문서를 인용한 응답의 `page` 는 **`null`** 로 둔다(1 로 채우지 않는다) |

`LIVE_n5_d8a0de8` 에서 측정한 결과(dry-run):

| 단계 | `chunk_sources` | `single_page_upstage` | `unrecovered` | 쪽 블록 수 |
|---|---:|---:|---:|---:|
| initial (16문서) | 12 | 4 | **0** | 26 |
| followup (17문서) | 13 | 4 | **0** | 27 |

복원한 쪽 수는 원본 PDF 쪽 수와 전부 일치했다(09=3, 12=2, 14=3, 15=2, 16=3, 17=2, 18=2, 나머지 1).
정형 4건은 Upstage 가 `usage.pages=1`·`elements[].page=[1]` 로 1쪽임을 확인해 준다.

## 5. 회사 답변(05) 처리

ESGenie 도 05 를 증빙 OCR 경로가 아니라 PDF 텍스트 직접 추출로 **회사 주장**으로 다룬다.
대조군도 같은 함수를 **불러서** 쓴다(다시 구현하지 않는다).

| 항목 | 값 |
|---|---|
| 추출 함수 | `esgenie/supplychain/claims.py::_extract_text` |
| 쪽 경계 | 반환 문자열의 `\f` → 1-기준 쪽 번호 |
| 입력 위치 | 증빙 블록과 **분리된 별도 구획**. 제목 `회사 자가 답변 초안 — 검증된 증빙 아님` |
| 블록 표지 | `[회사 답변 시작] 파일: … \| 쪽: N` / `[회사 답변 끝] …` (증빙 블록은 `[문서 시작]`·`[문서 끝]`) |
| 넣는 것 | **추출 원문 그대로.** `result.json` 의 `self_reports`·비교 결과 등 ESGenie 판단은 넣지 않는다 |
| `meta.notice` | `05는 회사 자가 답변 원문, 증빙 아님` |
| 모델이 05 를 근거로 인용하면 | **출력을 고치지 않는다.** 그런 행 수를 `run_log.json` 의 `parse.cited_company_answer` 로 집계한다 |

실행기는 `environment.json` 의 `role=company_answer` 입력에서 경로·sha256 을 읽고,
추출 직후 **PDF sha256 이 다르면 중단한다.**

`LIVE_n5_d8a0de8` 기준 측정값(dry-run, 두 단계 동일):

| 항목 | 값 |
|---|---|
| 파일 | `05_한성모터스_자가진단응답초안_2026-09.pdf` |
| PDF sha256 | `564435e19860989885b0cbe063b56c058e5a1c6ed23d0a8c3d99f5f5d9e4c822` |
| 실제 탄 경로 | **`pymupdf`** (pymupdf 1.28.2) — pdftotext 예비 경로가 아니다 |
| 쪽 수 / 문자 수 | 1쪽 / 907자 (1쪽이라 `\f` 0개) |
| 추출 원문 sha256 | `21c10eab8abfc83168416987c10b6e9d7c66264b2c2fe12e5f71fb1dd736f460` |

## 6. 토큰과 비용

**토큰 수는 반드시 기록한다. 단가는 코드에 넣지 않는다.**

| 항목 | 방법 |
|---|---|
| 입력 문자 수 | 정확값. `request.json`·`input_manifest.json` 의 `input_chars` |
| 입력 토큰(호출 전) | **추정값.** `chars / 2.0`. `tiktoken` 은 `requirements.txt` 에 없어 쓰지 않는다(새 의존성 금지). `token_estimate_method` 에 방법을 함께 적는다 |
| 입력·출력 토큰(호출 후) | **API 가 보고한 값.** `run_log.json` 의 `prompt_tokens`·`completion_tokens`·`total_tokens` |
| 컨텍스트 초과 | 한도를 넘으면 **그 실행을 중단한다.** 자동 분할하지 않는다(`status: aborted_context_limit`) |

### 컨텍스트 한도 — 모르면 실행을 거부한다

입력 토큰 한도는 아래 순서로 정한다.

1. `--max-input-tokens <N>` 을 주면 그 값 (`max_input_tokens_source: cli`)
2. 아래 표에 있으면 그 값 (`max_input_tokens_source: table:<확인 날짜>`)
3. **둘 다 없으면 실호출을 거부한다** (종료 코드 2). 한도를 모르는 채로 호출하면 컨텍스트 초과를
   알 수 없다. 추측해 넣지 않는다.

예외는 `--dry-run` 뿐이다 — 호출이 없고, 이 표를 채울 입력 길이를 재는 것이 dry-run 의 목적이다.
그때는 `max_input_tokens_source: dry_run_unchecked` 로 기록한다.

표는 `scripts/baseline_generic_llm.py` 의 `MODEL_CONTEXT_LIMITS` 에 넣는다.
**값은 공급자 공식 문서에서 확인해 출처 URL·확인 날짜와 함께 아래에 적는다.**

| 모델 | 입력 토큰 한도 | 출처 URL | 확인 날짜 |
|---|---|---|---|
| `gpt-4.1-mini` (동일 모델 — `SETTINGS.openai_model`, Azure 배포) | **267,232** | [Microsoft Learn — Foundry Models sold by Azure, GPT-4.1 series 표](https://learn.microsoft.com/en-us/azure/ai-services/openai/concepts/models) | 2026-10-12 |
| `gpt-4.1` (상위 모델, 같은 계열) | **267,232** | 위와 동일 | 2026-10-12 |
| (타 공급자 — `SETTINGS.anthropic_model`) | 미확인 | 미확인 | 미확인(이번 단계 범위 밖 — `ANTHROPIC_API_KEY` 없음) |

**값 계산:** 공식 문서의 "Context window" 는 입력+출력 합계로 세 단계다 — `1,047,576`
(절대 최대) / **`300,000`**(표준·Global Standard 배포) / `128,000`(provisioned·batch).
ESGenie 의 LIVE 실행은 표준(pay-per-token) 배포다(`environment.json` 의
`llm_provider=azure_openai`, 실제 응답 모델 `gpt-4.1-mini-2025-04-14` — 문서의
`gpt-4.1-mini (2025-04-14)` 행과 일치). "Max output tokens" 는 두 모델 모두 `32,768`.
**입력 한도는 가장 보수적으로 "총 한도 − 최대 출력" = 300,000 − 32,768 = 267,232** 로 둔다
(실제 출력은 48행 JSON 한 개라 훨씬 작겠지만, 확인되지 않은 수치를 가정하지 않는다).

단가(USD/1M 토큰)는 **찾지 못했다.** Azure 공식 가격 페이지
(`azure.microsoft.com/…/openai-service/`)가 클라이언트 쪽에서 가격표를 동적으로 그려
정적 HTML·공개 Retail Prices API(`prices.azure.com`, `serviceName=Cognitive Services` 질의
결과 0건)에는 값이 없다. 시도한 방법: ① WebFetch 로 그 페이지를 5회 시도 — 매번 사용한
요약 모델 오류로 실패 ② Azure Retail Prices API 를 두 가지 필터로 curl 직접 조회 — 0건
(Azure OpenAI 토큰 단가는 이 API 에 공개되지 않는 것으로 보인다) ③ 페이지 원본 HTML 을
curl 로 받아 확인 — `gpt-4.1` 문자열이 0건(가격표가 자바스크립트로 실행 후 채워진다).
**이 단계의 범위(조사, 호출 없음)에서는 브라우저 자동화 도구를 쓰지 않았다.**

### 비용 계산 규칙

```
비용 = (prompt_tokens / 1,000,000) × 입력단가 + (completion_tokens / 1,000,000) × 출력단가
```

- 단가는 **공급자 공식 가격 페이지에서 확인해 아래 표에 적는다.** 코드에 상수로 넣지 않는다.
- 표에는 **단가 출처 URL 과 확인 날짜**를 함께 적는다.
- 추정하거나 기억으로 채우지 않는다. 확인하지 않은 칸은 비워 둔다.

#### (가) Azure 실제 청구 단가 — 미확인

| 모델 | 입력단가(USD/1M) | 출력단가(USD/1M) | 출처 URL | 확인 날짜 |
|---|---|---|---|---|
| `gpt-4.1-mini` (동일 모델) | **미확인** | **미확인** | 시도함, 위 사유로 실패 | 2026-10-12 (시도일) |
| `gpt-4.1` (상위 모델) | **미확인** | **미확인** | 시도함, 위 사유로 실패 | 2026-10-12 (시도일) |
| (타 공급자) | 미확인 | 미확인 | 미확인 | 미확인 |

#### (나) 참고값 — 사람 확인, OpenAI 공개 단가 기준, Azure 표준 배포 동급 가정 (2026-10-13)

**위 (가)의 Azure 실제 청구 단가가 아니다.** 사람이 OpenAI 공개 단가를 확인해 준 값이고,
Azure 표준 배포 단가가 이와 같다고 **가정**한다. 예상 비용 계산에만 쓰고, 실제 비용은 호출 후
API 가 보고한 토큰과 확인된 청구 단가로 다시 계산한다.

| 모델 | 입력단가(USD/1M) | 출력단가(USD/1M) | 출처 | 확인 날짜 |
|---|---:|---:|---|---|
| `gpt-4.1-mini` | 0.40 | 1.60 | 사람 확인 · OpenAI 공개 단가 · Azure 동급 가정 | 2026-10-13 |
| `gpt-4.1` | 2.00 | 8.00 | 사람 확인 · OpenAI 공개 단가 · Azure 동급 가정 | 2026-10-13 |

**현재 비용: 0 USD.** 유료 호출을 아직 하지 않았다(호출 0건, 토큰 0).

### P3-3 예상 호출량과 예상 비용 (16호출 계획, 호출 전 추정치)

작업지시서 §4.2 표 그대로: 두 단계(initial·followup) × (동일 모델 5회 + 상위 모델 3회) = **16호출**.

| 항목 | 값 |
|---|---:|
| 단계당 호출 수(5+3) | 8 |
| 총 호출 수(2단계) | **16** |
| initial 1회 입력 추정 토큰 | 18,864 |
| followup 1회 입력 추정 토큰 | 19,320 |
| **총 입력 토큰(추정, 16호출 합)** | **305,472** (= 18,864×8 + 19,320×8) |
| 호출당 출력 토큰 | **8,000 — 가정**(지시값. 실측 아님) |
| **총 출력 토큰(가정, 16호출 합)** | **128,000** |

**세 가지가 추정·가정이다:** ① 입력 토큰은 `chars/2.0` 추정값(호출 후 API 값으로 대체)
② 출력 토큰은 호출당 8,000 으로 **가정**(모델이 48행 JSON 을 실제로 얼마나 쓰는지는 실행해야 안다)
③ 단가는 위 (나)의 **참고값**(Azure 실제 청구 단가 아님).

| 모델 | 호출 수 | 입력 토큰 | 출력 토큰(가정) | 입력 비용 | 출력 비용 | 소계 |
|---|---:|---:|---:|---:|---:|---:|
| `gpt-4.1-mini` (5회 × 2단계) | 10 | 190,920 | 80,000 | $0.076368 | $0.128000 | **$0.204368** |
| `gpt-4.1` (3회 × 2단계) | 6 | 114,552 | 48,000 | $0.229104 | $0.384000 | **$0.613104** |
| **합계** | **16** | **305,472** | **128,000** | $0.305472 | $0.512000 | **$0.817472** |

호출 1회당: `gpt-4.1-mini` initial $0.020346 / followup $0.020528,
`gpt-4.1` initial $0.101728 / followup $0.102640.

**P3-3 예상 비용 ≈ $0.82** (참고 단가·출력 8,000 가정 기준). 실제 비용은 호출 후 `run_log.json`
의 `prompt_tokens`·`completion_tokens` 와 확인된 청구 단가로 다시 계산해 이 문서에 적는다.

### `LIVE_n5_d8a0de8` 입력 길이 측정값 (dry-run, 호출 없음)

| 단계 | 입력 문자 수 | 추정 입력 토큰 | 증빙 블록 | 05 구획 | 질문 블록 |
|---|---:|---:|---:|---:|---:|
| initial | 37,726 | 18,864 | 31,036 | 1,010 | 4,134 |
| followup | 38,639 | 19,320 | 31,949 | 1,010 | 4,134 |

분할 규칙은 **쓰지 않는다** — 분할이 필요한 길이가 아니고, 필요해지면 중단 후 사람이 결정한다.

## 7. 공통 답안 형식 변환 — A **v1.1**(확정, c1bfa67, 2026-10-10)

쓰는 모듈: A 소유 `esgenie/eval/answer_format.py`. 저장소에 복사하지 않고 아래 순서로 찾는다.

1. `--answer-format <경로>` 2. 환경변수 `ESGENIE_EVAL_ANSWER_FORMAT`
3. 저장소 `esgenie.eval.answer_format`(A 브랜치 병합 후) 4. **고정 git ref**
   `c1bfa6730bb3f77bfeefd74405f293a1db8acbb0` (`origin/codex/eval-scoring-20261006`,
   2026-10-12 확인 — `FORMAT_VERSION = "1.1"`, B 회신 C-1~C-6 반영. 이전 핀
   `f6d2c576…`(`1.1-draft`) 과의 diff는 `FORMAT_VERSION` 문자열과 주석뿐이고 정의는
   바뀌지 않았다)의 `esgenie/eval/answer_format.py` — `git cat-file` 로 꺼내 임시 로딩

못 찾거나 `decision_for_detail`·`DETAIL_TO_DECISION`·`new_document`·`new_answer`·
`validate_document` 중 하나라도 없으면 **검증을 건너뛰지 않고 실패한다.**
어느 판으로 검증했는지는 `run_log.json`·`request.json` 의 `answer_format`
(경로·sha256·origin·git commit·git blob·`format_version`)에 남는다.

판정은 **구조로만** 한다. 모델이 단서를 덧붙여도 판정을 바꾸지 않는다.
모델 출력은 그대로 3값(`answer`/`hold`/`not_applicable`)이고, 변환기는 `decision_detail`
**만** 정한다. 외부 `decision` 은 A 모듈의 `DETAIL_TO_DECISION` 표가 계산한다
(`new_answer(decision_detail=…)` 안에서). **표를 이 저장소에 다시 쓰지 않는다.**

| 모델 출력 | `decision_detail` | `decision` (A 표 계산) |
|---|---|---|
| `decision=answer` + `evidence` 1개 이상 | `confirmed` | `answer` |
| `decision=answer` + `evidence` 없음 | `unverified_submitted` | `answer` |
| `decision=hold` | `hold` | `hold` |
| `decision=not_applicable` | `not_applicable` | `na` |
| 허용값 밖의 `decision` 값 | `unparsed` | `null` (필드를 **명시**한다) |

- **`decision=answer` 라는 이유만으로 확정으로 세지 않는다.** 확정은 `decision_detail == "confirmed"` 뿐이다.
- **누락 qid 는 행을 만들지 않는다**(v1.1 계약). 보류로 채우지 않고 `run_log.json` 의
  `parse.missing_qid` 에 목록을 남긴다. 검증기가 `missing_qid`·`row_count` 로 드러내면
  그 실패를 **고치지 않고** `validate_issues.json` 에 남긴다.
- 중복 qid 는 첫 행만 쓰고 중복 사실을 `decision_basis` 와 `parse.duplicate_qid` 에 남긴다.
- `meta.dataset_tag` = **필수**, **확정** `virtual` (상수 `DATASET_TAG`, A c1bfa67 2026-10-10).
- `meta.material_kind` = **확정** `mixed` (상수 `MATERIAL_KIND`, A c1bfa67 2026-10-10 —
  B 가 제시한 값을 A 가 그대로 받았다. 형식 문서 §9.1: "A 가 원본 구성을 따로 확인한 값은
  아니다"). 이전 `photo_evidence`에서 변경. `dataset_tag` 와 다른 축이므로 섞어 적지 않는다.
- `meta.data_source` = 단계별 설명 문자열. v1.1 에서 **선택** 필드이고 **집계에 쓰지 않는다**.
  B 제시값을 A 가 그대로 받아 확정됐다.
- `boundary`: 모델 `period` → `boundary.period`, `scope` → `boundary.label`.
  `site`·`target`·`denominator`·`completeness` 는 **`null`**(쪼개서 추정하지 않는다).
  v1.1 은 `boundary` 를 §4 원문 요구사항이 아닌 **A 확장**으로 정정했다. B 회신 C-4 로
  **유지**가 확정됐다(모르는 칸은 `null` 보존이라는 조건 그대로).
- `meta.system` = `baseline_<실제 응답 model>`, `meta.model` = 실제 응답 model,
  `meta.source_ref` = 그 반복의 `response_raw.json` 저장소 기준 상대 경로 + sha256,
  `source.raw` = 그 문항의 모델 출력 원문 조각.
- 반복 간 변동은 **`decision_detail` 로 센다** — 외부 3값은 `confirmed` 와
  `unverified_submitted` 를 모두 `answer` 로 묶어 변동을 가린다.
- 자동 재시도·프롬프트 수정·자동 분할을 하지 않는다.
- 대조군에는 제품 고유 지표가 없으므로 `meta.product_metrics` 를 **넣지 않는다**(0 으로 적지 않는다).

### 골격 검증 결과 (2026-10-12, c1bfa67 확정판, 호출 없음)

`LIVE_n5_d8a0de8` 두 단계로 문서를 만들어 A v1.1 확정판(c1bfa67) `validate_document` 에 넣었다.
(2026-10-09 에 `f6d2c57` 의 `1.1-draft` 판으로도 같은 결과를 확인했다 — 정의가 바뀌지
않았으므로 당연한 결과다.)

| 경우 | 결과 |
|---|---|
| 응답 없는 골격(`answers: []`) | `meta`·`format_version` 문제 **0건**. `missing_qid` 48 + `row_count` 1 만 남는다(의도된 노출) |
| 48행을 채운 **합성** 응답(다섯 상태 모두) | **문제 0건.** `decision` 이 전 행에서 `decision_detail` 의 표 값과 일치 |

합성 응답의 분포(두 단계 동일): `confirmed` 10 / `unverified_submitted` 10 / `hold` 10 /
`not_applicable` 9 / `unparsed` 9 → `answer` 20 / `hold` 10 / `na` 9 / `null` 9.
**합성 자리값이고 실제 응답도 정답도 아니다.**

## 8. 한계

1. **공통 답안 형식이 v1.1 로 확정됐다**(A c1bfa67, 2026-10-10 — B 회신 C-1~C-6 반영, `-draft`
   를 뗐다). `dataset_tag=virtual`·`material_kind=mixed`·`data_source` 값도 함께 확정됐다.
   남은 것은 §6 의 `boundary` 유지(C-4, 확정: 유지) 외에 추가 변경이 없다는 점을 A 커밋
   diff 로 직접 확인했다(`FORMAT_VERSION` 문자열 외 변경 없음).
2. **컨텍스트 한도는 공식 문서로 확인했지만(§6), Azure 실제 청구 단가는 확인하지 못했다.**
   Azure 가격 페이지가 클라이언트 쪽에서 동적으로 그려져 조사 도구(WebFetch·curl·Retail Prices
   API)로는 값을 얻지 못했다(§6에 시도 내역 기록). `--max-input-tokens` 가 필요한 모델은
   `gpt-4.1-mini`·`gpt-4.1` 둘 다 더 이상 없다(표에 있음). 그 밖의 모델은 여전히
   `--max-input-tokens` 를 요구한다.
3. 입력 토큰은 호출 전에는 **추정값**이다(tiktoken 없음). 호출 후 API 값으로 대체 기록한다.
4. **예상 비용 ≈ $0.82 는 실행하지 않은 API 대조군의 참고값이다**(§6 (나)). 2026-10-13 결정으로
   API 대조군을 실행하지 않으므로 이 비용은 발생하지 않는다. **현재 유료 API 비용 0 USD**(호출 0건).
5. 자료는 **가상 시연 세트**다. 결과를 실제 기업 자료의 성능으로 읽을 수 없다.
6. 이 문서에는 **실제 응답 결과가 없다.** 채점·비교표는 수동 실행·라벨 확정 뒤의 일이다.
7. **동일 모델 대조군을 실행하지 않는다(예산 소진).** 작업지시서 §4.2 표의 세 대조군 중 "동일
   모델(ESGenie 와 같은 `gpt-4.1-mini`) — 차이가 구조에서 나오는지"와 "상위 모델(`gpt-4.1`) —
   더 강한 모델이면 함정을 피하는지"가 빠진다. 그래서 **ESGenie 와 대조군의 차이가 검증 구조에서
   왔는지, 모델이 달라서 왔는지를 이 비교만으로는 가를 수 없다.** 남는 것은 타 공급자 1종이다.
8. **수동 채팅 실행이라 통제하지 못하는 것이 있다**(§9).
   - **temperature**: 채팅 기본값이고 사람이 제어하지 못한다(기록값 `null`). 작업지시서 §4.2 의
     temperature 0 조건을 만족하지 않는다.
   - **토큰 수**: 채팅 화면이 알려 주지 않는다(기록값 `null`). 입력은 `chars/2.0` 추정값만 있다.
   - **숨은 시스템 지시**: claude.ai 채팅 서비스가 붙이는 자체 시스템 지시를 볼 수도 끌 수도 없다.
     우리 §1 시스템 지시는 `[시스템 지시]` 머리글을 단 **사용자 메시지 안의 글**로 들어간다(채팅에
     system 역할이 없다) — API 의 system 메시지와 같은 무게로 읽힌다는 보장이 없다.
   - **모델 식별자**: API 응답의 정확한 `model` 대신 사람이 화면에서 읽은 이름이다.
   - **입력 길이**: 약 3만 8천 자를 붙여 넣는다. 채팅 화면이 긴 글을 "붙여넣은 텍스트" 상자로
     바꿔 보일 수 있다(같은 글이다).
   - **출력 끊김**: 답이 끊기면 `계속` 을 한 번 보내 이어 받는다. 이어 붙인 자리에서 JSON 이 깨지면
     파싱 실패로 남는다(고치지 않는다). 이어받음 여부는 `meta.txt`·`response_raw.json`·`run_log.json`
     에 남는다.
9. **사전 등록한 사례(`e49e69a`)·프롬프트(`2717880`)는 그대로다.** 수동 실행이 더하는 글자는
   §9 의 이어 붙이기 머리글(`[시스템 지시]`·`[사용자 입력]`)뿐이고, 프롬프트 파일 내용은 바뀌지 않았다.

## 9. 수동 채팅 실행 (타 공급자 대조군, 2026-10-13~)

API 를 부르지 않는다. 실행기가 **API 경로에서 보낼 system·user 문자열을 한 글자도 바꾸지 않고**
파일로 쓰고, 사람이 그것을 claude.ai 새 채팅에 붙여 넣어 실행한 뒤, 답을 파일로 돌려준다.
사람용 순서는 `outputs/eval/baseline/수동실행_안내.md` 에 있다.

### 9.1 붙여넣기 입력 형식

채팅에는 system 역할이 없으므로 둘을 한 덩어리로 붙인다. **이 형식이 수동 실행에서 유일하게 더해지는
글자다.**

```
[시스템 지시]\n<system>\n\n[사용자 입력]\n<user>
```

- `<system>` = 프롬프트 §1(시스템 프롬프트) 그대로, `<user>` = §2 템플릿을 같은 실행 입력으로 채운 것
  그대로 — API 경로의 `call_model(…, system, user)` 에 넘기는 문자열과 같다(테스트가 바이트 단위로
  확인한다).
- 파일은 UTF-8, 줄바꿈 변환 없이 쓴다(`\n` 그대로, Windows `\r\n` 변환 안 함).

### 9.2 폴더와 파일

`outputs/eval/baseline/MANUAL_claude_<stage>_rep<k>/` (stage = initial·followup, k = 1~3, 모두 6개).
**폴더 이름이 그대로 `run_id` 다.**

| 파일 | 누가 | 내용 |
|---|---|---|
| `paste_input.txt` | 실행기(내보내기) | 붙여 넣을 글 전체 |
| `request.json` | 실행기(내보내기) | 프롬프트·사례 sha256, system·user·paste sha256, 문자 수, 추정 토큰, `temperature: null` + 사유 |
| `response.txt` | **사람** | 채팅 답 원문 그대로. `계속` 으로 이어 받았으면 두 조각을 이어 붙인 것 |
| `meta.txt` | **사람** | `모델: …` / `실행 시각: …` / `이어받음: 예\|아니오` (세 줄 필수) |
| `response_raw.json` | 실행기(가져오기) | 답 원문 + meta + `temperature: null` + 토큰 `null` |
| `common_v1_<stage>.json` | 실행기(가져오기) | A 공통 답안 형식 v1.1 문서 |
| `run_log.json`·`validate_issues.json` | 실행기(가져오기) | 파싱 집계·검증 결과(실패 시) |

### 9.3 가져오기 규칙

- `meta.system` = `baseline_<meta.txt 모델 이름을 소문자·하이픈으로>`
  (예: `Claude Opus 4.1` → `baseline_claude-opus-4-1`), `meta.model` = `meta.txt` 값 **그대로**,
  `meta.run_id` = 폴더 이름.
- 토큰 수 `null`(채팅이라 모름). temperature `null`, 사유 "채팅 기본값, 사람이 제어 못 함".
- `meta.notice` 에 수동 채팅 실행이라는 사실과 통제하지 못하는 항목을 적는다.
  `meta.adapter.provider = "manual"`.
- `meta.txt` 가 없거나 세 줄 중 하나라도 빠지거나 `이어받음` 값이 `예`·`아니오` 가 아니면
  **아무 파일도 쓰지 않고 거부한다.** `response.txt` 가 없어도 거부한다.
- `response.txt` 는 BOM 만 벗기고(메모장 저장 흔적) 내용은 고치지 않는다. 파싱 실패·누락 qid 는
  API 경로와 같은 규칙이다 — 행을 만들지 않고 기록하며, 검증 실패를 고치지 않는다.
- 내보내기는 사람이 넣은 `response.txt`·`meta.txt`·`response_raw.json` 이 있는 폴더를 **덮어쓰지 않는다.**

### 9.4 내보낸 입력 (2026-10-13, `LIVE_n5_d8a0de8`)

| 단계 | paste_input.txt sha256 | 바이트 | 문자 | 추정 토큰 |
|---|---|---:|---:|---:|
| initial (rep1~3 동일) | `1f189c6ca4e233e9fd413f6456c2aa86b20d37ecce4126481689ccea9b01c839` | 70,145 | 37,746 | 18,864 |
| followup (rep1~3 동일) | `5f7b9185dce80de02d0a8c367cb17c22399aeacd76a5216ed57414b1ab8c5668` | 71,765 | 38,659 | 19,320 |

같은 단계의 rep1~3 은 sha256 이 같다. 각 파일은 `build_inputs()` 로 따로 다시 만든 API 경로
메시지와 바이트 단위로 같다. system 부분은 사전 등록한 §1 의 sha256(`6f184caf…`)과 같다.
