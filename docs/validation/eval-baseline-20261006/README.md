# 일반 문서 AI 대조군 — 실행기 (작업지시서 B §4.2)

**상태: 실행기 구현 완료 · 실제 호출 0건 · 비교 결과 없음.**
이 문서는 실행기의 조건과 기록 규칙을 적는다. 결과·비교표는 실제 실행 뒤에 §7 에 추가한다.

- 실행기: `scripts/baseline_generic_llm.py`
- 테스트: `tests/test_baseline_generic_llm.py` (LLM 은 mock, 네트워크 호출 없음)
- 사례 사전 등록: `data/eval/cases/baseline_cases_v1.csv` + `..._criteria.md` (커밋 `e49e69a`, 수정하지 않는다)
- 프롬프트: `data/eval/prompts/baseline_prompt_v1.md` — **초안. 공통 답안 형식 합의 뒤 별도 커밋한다**
  (이 커밋에 들어 있지 않다)

---

## 1. 비교 조건

| 항목 | 값 |
|---|---|
| 자료 | 팀이 만든 **가상 시연 세트** `한울정밀_촬영세트_정상5건보강_20261007` |
| 문항 | `rba42` 48행 (`Question.qid`·`Question.text` 원문) |
| ESGenie 기준 실행 | `LIVE_n5_d8a0de8` (initial·followup 둘 다) |
| 입력 텍스트 원천 | **ESGenie 가 같은 실행에서 쓴 OCR 결과** — `pipeline.json` 의 `ocr_extractions[].raw_text` |
| 회사 답변(05) | `esgenie/supplychain/claims.py::_extract_text` 로 추출한 PDF 원문 (증빙 OCR 경로 아님) |
| temperature | 0 |
| 단계별 `data_source` | initial `촬영증빙_17건` / followup `촬영증빙_17건+보완_1건` (**A 확인 대기**) |

**표현 규칙:** 결과를 쓸 때 "팀이 만든 가상 세트, 특정 프롬프트 기준"을 함께 적는다.
"일반 AI 대비 N배" 같은 일반화는 쓰지 않는다.

## 2. 실행 방법

```bash
# 입력만 만들고 호출하지 않는다(조립·쪽 복원 확인용)
python scripts/baseline_generic_llm.py \
  --pipeline-json <run>/initial/pipeline.json --stage initial \
  --framework rba42 --model <모델 식별자> --provider openai \
  --repeats 1 --out outputs/eval/baseline/<run_id> \
  --answer-format <A 패키지 루트> --dry-run

# 실제 실행
python scripts/baseline_generic_llm.py \
  --pipeline-json <run>/initial/pipeline.json --stage initial \
  --framework rba42 --model <모델 식별자> --provider openai \
  --repeats 5 --out outputs/eval/baseline/<run_id> \
  --answer-format <A 패키지 루트>

# 저장한 응답만으로 공통 형식을 다시 만든다(호출 없음)
python scripts/baseline_generic_llm.py --rescore-only outputs/eval/baseline/<run_id> \
  --answer-format <A 패키지 루트>
```

`--answer-format` 은 A 가 소유하는 `answer_format.py`(또는 패키지 루트)를 가리킨다.
환경변수 `ESGENIE_EVAL_ANSWER_FORMAT` 로도 준다. 저장소에 `esgenie/eval/answer_format.py` 가
들어오면(A 브랜치 병합) 인자 없이도 찾는다. **못 찾으면 검증을 건너뛰지 않고 실패한다.**

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
| 컨텍스트 초과 | `--max-input-tokens` 를 넘으면 **그 실행을 중단한다.** 자동 분할하지 않는다(`status: aborted_context_limit`) |

### 비용 계산 규칙

```
비용 = (prompt_tokens / 1,000,000) × 입력단가 + (completion_tokens / 1,000,000) × 출력단가
```

- 단가는 **공급자 공식 가격 페이지에서 확인해 아래 표에 적는다.** 코드에 상수로 넣지 않는다.
- 표에는 **단가 출처 URL 과 확인 날짜**를 함께 적는다.
- 추정하거나 기억으로 채우지 않는다. 확인하지 않은 칸은 비워 둔다.

| 모델 | 입력단가(USD/1M) | 출력단가(USD/1M) | 출처 URL | 확인 날짜 |
|---|---|---|---|---|
| (동일 모델 — `SETTINGS.openai_model`) | 미확인 | 미확인 | 미확인 | 미확인 |
| (상위 모델) | 미확인 | 미확인 | 미확인 | 미확인 |
| (타 공급자 — `SETTINGS.anthropic_model`) | 미확인 | 미확인 | 미확인 | 미확인 |

**현재 비용: 0 USD.** 이 단계에서 유료 호출을 하지 않았다(호출 0건, 토큰 0).

### `LIVE_n5_d8a0de8` 입력 길이 측정값 (dry-run, 호출 없음)

| 단계 | 입력 문자 수 | 추정 입력 토큰 | 증빙 블록 | 05 구획 | 질문 블록 |
|---|---:|---:|---:|---:|---:|
| initial | 37,726 | 18,864 | 31,036 | 1,010 | 4,134 |
| followup | 38,639 | 19,320 | 31,949 | 1,010 | 4,134 |

분할 규칙은 **쓰지 않는다** — 분할이 필요한 길이가 아니고, 필요해지면 중단 후 사람이 결정한다.

## 7. 공통 답안 형식 변환

판정은 **구조로만** 한다. 모델이 단서를 덧붙여도 판정을 바꾸지 않는다.

| 모델 출력 | 공통 판정 |
|---|---|
| `decision=answer` + `evidence` 1개 이상 | `confirmed` |
| `decision=answer` + `evidence` 없음 | `unverified_submitted` |
| `decision=hold` | `hold` |
| `decision=not_applicable` | `not_applicable` |
| 허용값 밖 / 해당 qid 누락 / JSON 파싱 실패 | `unparsed` |

- `boundary`: 모델 `period` → `boundary.period`, `scope` → `boundary.label`.
  `site`·`target`·`denominator`·`completeness` 는 **`null`**(쪼개서 추정하지 않는다).
- `meta.system` = `baseline_<실제 응답 model>`, `meta.model` = 실제 응답 model,
  `meta.source_ref` = 그 반복의 `response_raw.json` 저장소 기준 상대 경로 + sha256,
  `source.raw` = 그 문항의 모델 출력 원문 조각.
- 파싱 실패를 보류로 바꾸지 않는다. 누락 qid 를 보류로 채우지 않는다.
  중복 qid 는 첫 행만 쓰고 중복 사실을 `decision_basis` 와 집계에 남긴다.
- 내보내기 전에 `validate_document(doc, expected_qids)` 를 통과시킨다.
  실패하면 **고치지 않고** `validate_issues.json` 에 목록을 남긴다.
- 자동 재시도·프롬프트 수정·자동 분할을 하지 않는다.

## 8. 한계

1. **공통 답안 형식이 합의 전이다**(`1.0-draft`). A 브랜치에는 더 나아간 판이 있어 그대로 쓰면
   검증이 깨진다 — 리뷰 기록 `outputs/eval/review/P3-2.md` 에 차이를 적었다.
   형식이 확정되면 `DATA_SOURCE` 상수와 변환 표(§7) 한 곳만 고치면 된다.
2. **`data_source` 값이 A 확인 대기**다. 두 시스템이 같은 문자열을 써야 비교가 선다.
3. 입력 토큰은 호출 전에는 **추정값**이다(tiktoken 없음). 호출 후 API 값으로 대체 기록한다.
4. 자료는 **가상 시연 세트**다. 결과를 실제 기업 자료의 성능으로 읽을 수 없다.
5. 이 문서에는 **결과가 없다.** 실제 호출·채점·비교표는 라벨 확정과 형식 합의 뒤의 일이다.
