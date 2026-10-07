# B-2 실행 출처 표시 — PR 본문 초안

> 작업지시서 B §3. 이 파일은 PR 본문에 그대로 붙여 쓸 초안이다.

## 요약

보고서·시연에 쓴 응답서를 나중에 보면 **어느 커밋으로 만들었는지, AI를 실제로 불렀는지 저장된 캐시를 재생한 것인지** 알 수 없었다. 그걸 출력물 자체에 남긴다.

- 기준 main SHA: **`d8a0de8a434e4be97143f05d4694c9aa6b5ee14c`**
- 브랜치: `codex/run-provenance-20261006`
- **B-1 브랜치(`codex/eval-output-check-20261006`, PR #73)를 merge했다.** #73이 main에 병합되기 전까지 이 PR의 커밋 목록에 B-1 커밋 3개(`9d55114`·`f0f5737`·`cbe27bd`)가 함께 보인다. #73이 먼저 병합되면 사라진다. B-2가 B-1 검사기를 고쳐야 해서(아래 §바닥글) 합쳤고, **rebase는 하지 않았다.**
- 코어 수정 없음 — `esgenie/pipeline.py` · `supplychain/schema.py` · `supplychain/mapping.py` · `esgenie/ui/**`를 고치지 않았다.

## 무엇이 들어오는가

| 파일 | 내용 |
|---|---|
| `esgenie/run_info.py` | **신규** — `build_run_info()`와 표시 헬퍼 |
| `esgenie/supplychain/exporters/excel.py` | 키워드 전용 `run_info=None`. 새 시트 `실행정보`에만 적는다 |
| `esgenie/supplychain/exporters/pdf.py` | 키워드 전용 `run_info=None`. 첫 쪽 하단 한 줄 + 모든 쪽 바닥글 |
| `scripts/live_numeric_rehearsal.py` | 실행 시작 스냅샷 → `run_info` → `result.json` + export 전달 |
| `scripts/check_output_consistency.py` | (B-1 검사기) `run_info` 도장 줄 처리 + 새 검사 2종 |
| `docs/UI연결용_수치범위_출력계약_2026-10-05.md` | §3-1 `run_info` 절 |
| `tests/test_run_info.py` · `test_run_info_export_identity.py` · `test_check_output_consistency_run_info.py` | 테스트 |
| `tests/fixtures/run_info_baseline.json` | `run_info=None` 동일성 기준값(수정 전에 뜬 것) |

## 처리 방식 판정 — 작업지시서 §3과 다른 점

**작업지시서의 판정식에 Upstage 항을 더했다.** §3은 LLM·OCR 두 캐시만 보지만, 그대로 쓰면 **신규 처리와 캐시 재생이 뒤집힌다.**

`ocr_cache`는 Upstage 응답 캐시가 아니라 **Upstage 결과를 입력으로 받는 VLM 보정 LLM 응답 캐시**다. 코드 순서가 그것을 강제한다:

| `esgenie/ssot/ocr_router.py` | 하는 일 |
|---|---|
| `:1917` `extract_unstructured` | PDF 텍스트를 뽑고, 텍스트 레이어가 없으면 |
| `:1941` | **Upstage Document Parse를 호출** |
| `:1977` `_extract_unstructured_text` | 그 텍스트를 받아 |
| `:2034` | 그것을 `llm_input`에 넣어 **OCR 캐시 키를 만들고** |
| `:2042` | 캐시를 조회 |

Upstage 결과가 캐시 키의 입력이므로 순서가 바뀔 수 없다. 그래서 **OCR 캐시가 전부 적중해도 Upstage는 매번 불린다** — P1-0 followup 실측에서 OCR 적중 10 · 미스 1인데 **Upstage 요청 4건이 성공**했다. Upstage 자체의 재생은 `ocr_cache`가 아니라 `live_numeric_rehearsal.py`의 `UpstageTape`(record/replay)가 맡는다.

새 판정:

```
캐시 재생 = LLM live_calls == 0 ∧ OCR misses == 0 ∧ Upstage 실요청 == 0
신규 처리 = LLM hits == 0      ∧ OCR hits == 0   ∧ Upstage 재생 아님
그 밖     = 혼합
```

Upstage 실요청 수를 **모르면(`upstage.counted == false`) `캐시 재생`으로 판정하지 않는다** — 모르는 것을 재생이라고 말하지 않기 위해서다. 나머지 두 조건으로 `신규 처리` 판정은 허용한다. 아무것도 부르지 않은 실행(모두 0)은 ①에 먼저 걸려 `캐시 재생`이 된다.

## 그 밖에 작업지시서와 다르게 한 것

| 다른 점 | 이유 |
|---|---|
| `models.ocr`에 `ocr_cache.model_name()`을 쓰지 않았다 | 그 함수는 **캐시 키용 LLM 모델명**(`SETTINGS.openai_model`)을 돌려준다(`ocr_cache.py:87-90`). 그대로 쓰면 "OCR 모델이 gpt-4.1-mini"라는 틀린 기록이 남는다. `ocr_router.UPSTAGE_DP_MODEL`을 쓰고 `models.ocr_vlm`에 보정용 LLM을 따로 적었다 |
| `keys`에 `upstage` 추가 | 이 제품의 OCR은 Upstage 키로 돌아간다 — 없으면 실행이 불가능하므로 '설정됨/없음'을 남길 값이다 |
| `processing.llm.from_snapshot` 추가 | `llm_cache.stats()`는 프로세스 누적값이다. 스냅샷 없이 부르면 누적값이 들어가는데 같은 모양으로 내보내면 읽는 쪽이 실행 구간 값으로 오해한다 |
| PDF 두 줄에 고정 접두어 `실행 정보:` | 바닥글이 추출 텍스트에 섞인다. 사람이 읽기에도 명확하고, 응답표를 읽는 쪽이 표 내용과 가를 단서가 된다 |
| `processing`에 `successes`·`failures`도 담음 | `stats()`가 주는 값이고 실패가 섞인 실행을 나중에 구분할 수 있어야 한다 |

## 표시 위치

| 출력 | 위치 |
|---|---|
| `result.json` | 최상위 `run_info` 키 |
| 응답서 Excel | **새 시트 `실행정보`**(항목·값 2열). **기존 시트의 셀은 하나도 바뀌지 않는다** |
| 응답서 PDF | 첫 쪽 하단 한 줄(자세히) + **모든 쪽 바닥글**(커밋 앞 7자리 · 처리 방식 · 미커밋 표시) |

`run_info=None`이면 표시가 전부 생략되고 **기존 출력과 셀 값·추출 텍스트가 같다.** 이걸 증명하려고 exporters를 고치기 **전에** 기준값을 떠 뒀다(`tests/fixtures/run_info_baseline.json` — 시트 3개 54셀 + PDF 1쪽).

## PDF 바닥글과 B-1 검사기

B-1 README §9가 예측한 문제가 **실제로 걸렸다.** 바닥글이 각 쪽 추출 텍스트의 **맨 앞**에 오므로(reportlab이 `handle_pageBegin`에서 콜백을 부른다) 쪽을 이어 붙이면 **이전 쪽 마지막 블록의 꼬리**가 되고, `_trim_block`은 표 반복 머리글까지만 자른다. 실측: 근거 링크 없는 행 30개·6쪽 PDF에서 `cell:note` 불일치 2건.

**해소 방식 — 접두어로 줄을 빼지 않는다.** 그러면 응답 칸에 `실행 정보: …`로 시작하는 내용이 들어가도 함께 지워져 검사를 피해 간다. 대신:

- `run_info`로 **기대 문자열을 만들고**(`footer_line`·`summary_line` + `pdf_safe_text`) 페이지 텍스트에서 **정확히 같은 줄만** 제거한다. 제거는 **블록 파싱 전**.
- 새 검사 `run_info_footer`: **(a) 모든 쪽에 바닥글이 정확히 1번** **(b) 1쪽에 요약 줄이 있고 다른 쪽에는 없는가.**
- 새 검사 `run_info_excel`: Excel `실행정보` 시트가 `run_info`와 **칸 전체 일치**하는가.
- **`run_info`가 없으면 동작이 전혀 바뀌지 않는다.**

## 결과

| 검사 | 결과 |
|---|---|
| `tests/test_run_info.py` + `test_run_info_export_identity.py` | **38 passed** |
| `tests/test_check_output_consistency_run_info.py` | **18 passed** |
| `tests/test_check_output_consistency.py` (B-1, 회귀) | **52 passed** — 그대로 |
| B-1 주입 하네스 20종 × 공통 LIVE 2단계 | **20/20 검출** 그대로(run_info 없는 실행이라 변화 없음) |
| 전체 `pytest` | 기준선과 같음 — failed 22 / skipped 27 / errors 6 불변 |

## 한계 · 확인 못 한 것

- **실제 LIVE 실행에 돌린 결과가 없다.** 이 PR은 구현·단위 테스트까지다. 신규 처리 1회 + 캐시 재생 1회 실행(§3 통과 조건 2)과 그 출력에 B-1 검사기를 돌리는 것(통과 조건 3)은 다음 단계에서 한다.
- `PipelineOutput.timings`가 현재 코드에 **없다.** `getattr`로 읽고 없으면 생략하며, 가짜 값으로만 테스트했다. A-2가 넣은 뒤 실제 구조와 맞는지 확인해야 한다.
- Azure 엔드포인트 환경에서의 `llm_provider` 값은 키 없이 돌린 테스트에서 확인하지 못했다.
- 화면 표시는 PR #65에서 붙인다 — 이 PR은 **UI를 건드리지 않는다.**
