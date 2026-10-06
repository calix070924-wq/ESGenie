# A-1 응답 품질 채점기 — 검증 기록 (2026-10-06)

**이 작업의 범위는 "확정 규칙 기반 채점 로직 + 합성 입력 테스트"다.**
원본 기반 정답 라벨, B 독립 검토, 작업지시서 A §4 공통 답안 형식 합의, 실제 가상 세트
측정은 **모두 미완료**다. 아래 §6에 차단 요인을 정리했다.

이 기록에는 보고용 수치가 없다. 측정을 수행하지 않았다.

## 1. 기준 커밋과 PR #71 병합 확인

| 항목 | 값 |
|---|---|
| 기준 `origin/main` 전체 SHA | `d8a0de8a434e4be97143f05d4694c9aa6b5ee14c` |
| 기준 커밋 일시 | 2026-10-06 13:10:31 +0900 |
| 기준 커밋 제목 | `Merge pull request #71 from calix070924-wq/codex/fix-numeric-scope-output-20261005` |
| 작업 브랜치 | `codex/eval-scoring-20261006` (별도 worktree에서 작업) |
| 측정 대상 제품 코드 SHA | **해당 없음 — 측정을 수행하지 않았다** |

PR #71 병합 확인 근거(2026-10-06, `git fetch origin --prune` 직후):

1. `gh pr view 71 --json state,mergedAt,mergeCommit,baseRefName,headRefName`
   → `state=MERGED`, `mergedAt=2026-10-06T04:10:32Z`,
   `mergeCommit.oid=d8a0de8a434e4be97143f05d4694c9aa6b5ee14c`,
   `baseRefName=main`, `headRefName=codex/fix-numeric-scope-output-20261005`
2. `git rev-parse origin/main` → `d8a0de8a434e4be97143f05d4694c9aa6b5ee14c`
   — 병합 커밋 자체가 `origin/main` HEAD다(squash가 아닌 일반 merge commit).
3. 보조 근거: `git merge-base --is-ancestor origin/codex/fix-numeric-scope-output-20261005 origin/main`
   → 참. 브랜치 HEAD는 `b9f5d4ba3a6a01aa21d5edbc2065bfafac147296`.

조상 여부만으로 판단하지 않고 1·2를 1차 근거로 썼다.

## 2. 변경한 것과 이유

| 파일 | 내용 |
|---|---|
| `esgenie/eval/response_scoring.py` (신규) | 라벨 로더·시스템 판정 매핑·값/근거 비교·구성 검증·집계·CLI |
| `tests/test_eval_response_scoring.py` (신규) | 합성 입력 테스트 66건 |
| `docs/validation/eval-scoring-20261006/README.md` (신규) | 이 기록 |

제품 코드는 고치지 않았다. `esgenie/supplychain/exporters/*`·`schema.py`·`mapping.py`는
건드리지 않았고, 인터페이스 요구사항도 생기지 않았다.

설계에서 지킨 것:

- **정답은 라벨 파일에서만 읽는다.** 회사명·파일명·문항 ID·정답 수치로 분기하지 않는다.
  단계별 48행 구성도 `get_framework(<키>).questions`에서 읽어 넘긴다(`_framework_qids`).
- **확정 규칙만 구현했다.** 확정된 것은 작업지시서 A §6.1(시스템 판정 매핑)과
  §6.2(미검증 전달 행의 집계)뿐이다. 그 밖의 조합은 `unresolved`로 남긴다.
- **비율·정확도·종합 점수를 내지 않는다.** 지표의 분자·분모가 미정이므로
  `ScoreReport`에는 건수만 있고 `metrics_blocked_reason`에 그 이유가 들어 있다.
  `unresolved`가 0건이어도 비율을 내지 않는다.
- **공통 형식 추상화를 만들지 않았다.** §4가 없어 입력 해석(`parse_answers`·`load_labels`)과
  채점(`classify_system`·`compare_*`·`assign_bucket`)만 분리했다. 변환층·플러그인은 없다.

### 2.1 시스템 판정 매핑(§6.1)

`AnswerStatus`는 `esgenie/supplychain/schema.py`의 7개 값이고
`comparison`은 `compared|mismatch|not_comparable|scope_unconfirmed`다.
차단 비교 = `{scope_unconfirmed, not_comparable, mismatch}`.

| 판정 | 조건 |
|---|---|
| `confirmed` | `status==verified` · 값 채워짐 · 차단 비교 없음 |
| `self_reported` | `status==self_reported` · 차단 비교 없음 |
| `hold` | 그 밖(`insufficient`·`hitl_required`·`draft_ready`·`flagged`, 차단 비교, verified이나 값 없음) |
| `not_applicable` | `status==not_applicable` · 차단 비교 없음 |
| `unresolved` | `not_applicable` + 차단 비교 — 계약에 우선순위가 없다(§6.2 6번) |

값 `0`과 `False`는 채워진 값으로 센다(`_is_filled`). `self_reported`는 어떤 경우에도
확정으로 세지 않는다.

### 2.2 페이지 번호 변환

시스템 `evidence_links[].page`는 **0-기준**이다(`esgenie/ssot/audit_trace.py:35`,
UI 출력계약 §2 "0-기준, 화면은 +1"). 라벨 `expected_sources`는 **1-기준**이다.

- 변환 지점은 `to_label_page()` **한 곳뿐**이고, 호출자는 `parse_answers()`뿐이다.
- 변환된 값은 `EvidenceRef.page_1based`라는 이름으로만 들고 다녀 재변환을 막는다.
- 페이지가 없으면(`None`·bool·파싱 실패) **첫 페이지로 간주하지 않고** `None`으로 남기고,
  근거 비교에서 불일치로 처리하며 이유에 "시스템 근거에 페이지가 없다"를 남긴다.
- 테스트: `test_page_conversion_is_zero_to_one_based_and_single_point`,
  `test_parse_answers_converts_page_exactly_once`(이미 변환된 값을 다시 넣으면
  4가 되는 것을 고정해 이중 변환을 드러낸다), `test_source_page_off_by_one_is_mismatch`,
  `test_source_without_page_is_mismatch_not_first_page`.

### 2.3 값·근거 비교

- 수치: 천 단위 구분기호만 지우고 `abs(시스템 − 라벨) <= tolerance`(경계 포함). 기본 0.
- 단위: NFKC·공백·대소문자만 고르고 **환산·별칭을 하지 않는다**. `톤` vs `t`, `톤` vs `kg`는
  불일치로 남긴다. 기간·사업장은 비교 대상에 넣지 않았다(§6.2 11번).
- 예/아니오: 시스템 값의 자료형(`bool`)으로 형태를 가른다. `"0"`·`"1"`은 예/아니오
  토큰으로 읽지 않는다 — 수치 0을 예/아니오로 뒤바꾸면 안 된다.
- 목록 값(`list`)은 비교 규칙이 없어 `None`(미정)으로 남긴다.
- 근거: 정답 근거 여러 건 중 **하나라도** `(파일명, 1-기준 페이지)`가 맞으면 일치.
- 값 일치와 근거 일치는 각각 `value_match`·`source_match`로 따로 기록·집계한다.

### 2.4 집계 위치(§6.2) — 확정 규칙

`self_reported`에만 확정 규칙이 있다.

| 정답 라벨 | 집계 위치 | 세부 표시 |
|---|---|---|
| `hold` + `no_evidence` | `correct_hold` | — |
| `hold` + `mismatch`·`not_comparable`·`scope_unconfirmed` | `wrong_confirmation` | `missed_mismatch` |
| `answer` + 값 불일치 | `wrong_confirmation` | `missed_mismatch` |
| `answer` + 값 일치 | `unnecessary_hold` | `evidence_link_missing` |

그 밖은 전부 `unresolved`이며 행별 `bucket_reason`과 `unresolved_reasons` 건수에 남는다.

## 3. 정답 라벨 CSV 스키마 (파일은 아직 만들지 않았다)

`data/eval/labels/hanwool_bm_rba42_v1.csv`는 **실제 라벨을 사람이 작성·검토할 때 생성한다.**
지금 빈 파일이나 AI 초안을 두지 않는다. 헤더(열 순서 그대로)는 다음과 같다.

```csv
stage,qid,expected_decision,hold_reason,expected_value,expected_unit,tolerance,expected_sources,boundary_note,labeler,note
```

| 열 | 정의 | 채점기가 거부하는 값 |
|---|---|---|
| `stage` | `initial` / `followup` | 그 밖의 값 |
| `qid` | 응답서 qid (예: `RBA-C-4-E-6-2`) | 빈 값 |
| `expected_decision` | `answer` / `hold` / `na` | 그 밖의 값 |
| `hold_reason` | hold일 때 `no_evidence`·`scope_unconfirmed`·`not_comparable`·`mismatch`·`needs_human_text` | hold인데 비었거나 목록 외; hold가 아닌데 채워짐 |
| `expected_value` | answer일 때 수치 또는 예/아니오 | answer인데 비었거나, answer가 아닌데 채워짐 |
| `expected_unit` | 정답 단위 | — |
| `tolerance` | 수치 허용 오차, 기본 0 | 음수·비수치 |
| `expected_sources` | `파일명#페이지`, 페이지는 **1-기준**, 여러 건은 `;` | `#` 없음, 페이지 비정수, 페이지 < 1, 파일명 없음 |
| `boundary_note` | 기간·사업장·대상·분모 | — |
| `labeler` | 실제 작성자 ID | — |
| `note` | 판단 메모와 확인 필요 사항 | — |

확정 규칙: **실제 결과 채점 전에 라벨 파일을 먼저 커밋하고 그 전체 SHA와 파일 해시를
이 문서에 기록한 뒤** 채점한다. 아직 해당 없음.

| 항목 | 값 |
|---|---|
| 라벨 커밋 전체 SHA | **없음 — 라벨 미작성** |
| 라벨 파일 해시 | **없음 — 라벨 미작성** |

### 3.1 합성 fixture와 실제 라벨의 구분

테스트의 라벨·응답은 모두 `tests/test_eval_response_scoring.py` 안에서 만드는
**합성 fixture**이고, qid도 `SYN-1`·`SYN-2`·`SYN-3` 같은 합성 ID다(구성 검증 1건만
`rba42` 양식에서 qid 목록을 읽는다 — 정답 값은 쓰지 않는다). 파일 머리말에 그 사실을
적어 두었다. 실제 정답 수치는 제품 코드·테스트에 없다.

## 4. 수행한 검증과 결과

환경: Python 3.14.6, worktree `/tmp/esg_eval_scoring`, 기준 `d8a0de8a…`.

| 검증 | 명령 | 결과 |
|---|---|---|
| 채점기 테스트 | `python -m pytest tests/test_eval_response_scoring.py -q` | **66 passed** |
| 전체 스위트(회귀) | `python -m pytest -q` | **5531 passed, 27 skipped** (skip은 기존 것, 73초) |

skip·xfail로 실패를 가린 테스트는 추가하지 않았다(새 테스트 66건 전부 실제 통과).

### 4.1 §7 필수 테스트 항목 대응

| 요구 항목 | 테스트 |
|---|---|
| verified + 값 있음/없음, 값 0 | `test_verified_with_value_is_confirmed`, `test_verified_without_value_is_hold_not_confirmed`, `test_zero_and_false_are_filled_values_not_empty`, `test_zero_value_is_compared_as_number_not_empty` |
| 세 가지 차단 comparison | `test_blocking_comparison_forces_hold` (verified·self_reported × 3) |
| §6.2 미검증 전달 집계 | `test_self_reported_hold_no_evidence_is_correct_hold`, `test_self_reported_hold_mismatch_family_is_wrong_confirmation`, `test_self_reported_answer_value_mismatch_is_wrong_confirmation`, `test_self_reported_answer_value_match_is_unnecessary_hold` |
| 허용 오차 경계·단위 불일치 | `test_tolerance_boundary_is_inclusive`, `test_default_tolerance_is_zero`, `test_unit_mismatch_is_not_normalized_away`, `test_notation_only_differences_are_normalized` |
| 다중 정답 근거 OR·1-기준 페이지 | `test_any_expected_source_matching_counts_as_hit`, `test_page_conversion_*`, `test_source_page_off_by_one_is_mismatch`, `test_source_without_page_is_mismatch_not_first_page` |
| 중복·누락 행, 잘못된 라벨 값 | `test_structure_detects_duplicates_missing_and_unexpected`, `test_unmatched_rows_are_reported_not_silently_dropped`, `test_bad_label_values_are_rejected`(14건), `test_missing_label_column_is_rejected` |
| 미정 규칙을 임의로 성공 처리하지 않음 | `test_non_self_reported_combinations_stay_unresolved`, `test_self_reported_needs_human_text_and_na_are_unresolved`, `test_report_refuses_to_publish_metrics`, `test_list_value_rule_is_undetermined` |
| 값·근거 일치 분리 기록 | `test_value_and_source_match_are_recorded_separately` |
| 단계별 48행·전체 96행 | `test_rba42_stage_and_total_row_counts_come_from_the_framework` |

### 4.2 변이 테스트 — 테스트가 비어 있지 않음을 확인

채점기를 일부러 틀리게 바꾸고 테스트가 잡는지 확인했다(확인 후 전부 원복, 원복 뒤 66 passed).

| 변이 | 결과 |
|---|---|
| 값 `0`을 빈 값으로 취급 | 5 failed |
| `self_reported`를 확정으로 집계 | 3 failed |
| 페이지 0→1 기준 변환 생략 | 4 failed |
| 미정 조합을 `correct_hold`로 처리 | 5 failed |
| 단위를 `톤`→`t` 별칭으로 정규화 | 1 failed |

## 5. 다시 검증하는 방법

```bash
git fetch origin
git worktree add -b verify/eval-scoring-20261006 /tmp/verify_eval_scoring origin/codex/eval-scoring-20261006
cd /tmp/verify_eval_scoring

# 1) 채점기 테스트
python -m pytest tests/test_eval_response_scoring.py -q      # 66 passed 기대

# 2) 회귀
python -m pytest -q                                          # 5531 passed, 27 skipped 기대

# 3) 파일 해시
shasum -a 256 esgenie/eval/response_scoring.py tests/test_eval_response_scoring.py
```

기대 해시(이 기록 작성 시점):

```
24c6fbe5c25bfc6d88060424363cd824b179a6baad01a71d3f2a1db4c2b627dc  esgenie/eval/response_scoring.py
98e1d32059c0b639a491e17af4c4baed81b0e8f5708fa0a9daee173291a1f568  tests/test_eval_response_scoring.py
```

라벨과 실제 실행 결과가 생기면 채점은 다음과 같이 돌린다(지금은 입력이 없어 실행하지 않았다).

```bash
python -m esgenie.eval.response_scoring \
  --labels data/eval/labels/hanwool_bm_rba42_v1.csv \
  --result initial=<...>/initial/result.json \
  --result followup=<...>/followup/result.json \
  --framework rba42 \
  --out outputs/eval/<날짜>_<run_id>/score.json
```

`outputs/eval/`은 `.gitignore:25`(`outputs/`)로 무시된다 — 실행 결과는 커밋되지 않는다.

## 6. 필요한 자료와 결정 사항

### 6.1 누락된 명세 — 작업지시서 A §3·§4

| 항목 | 상태 |
|---|---|
| 작업지시서 A 원문 | 저장소에 **없음**. 전체 브랜치 히스토리(`git log --all --diff-filter=A`)에도 추가된 적 없음 |
| A §3 작업시간 계측 상세 | **없음** — 계측 구간, 시간 산식, 사람 작업시간 수집 방식 |
| A §4 공통 답안 형식 | **없음** — 형식 버전, 필수 필드, 단계·qid 식별, 값·근거 표현, 호환성 기준 |
| `docs/AI_ROOKIE_평가항목별_시연과보고서_전략_2026-10-05.md` | 저장소에 **없음** |
| `작업지시서_B_대조군비교_출력일치검사_2026-10-05.md` | 저장소에 **없음** |
| `AGENTS.md` | 파일은 있으나 **내용이 비어 있음**(49바이트, 헤더 한 줄뿐) |
| `docs/UI연결용_수치범위_출력계약_2026-10-05.md` | 있음 — §3 읽음 |

§3이 없어 **A-2(작업시간 계측)는 착수하지 않았다.** PR #71 병합 조건은 충족했으나
계측 명세가 없으면 임의 확정이 되므로 보류했다. 브랜치 `codex/eval-timing-20261006`도
아직 만들지 않았다.

### 6.2 미정 채점 조합·지표 산식

채점기가 `unresolved`로 남기는 것들이다. 임의로 정답·오답에 배정하지도, 집계에서
빼지도 않았다.

1. `self_reported` × `hold(needs_human_text)` — §6.2 표에 없다.
2. `self_reported` × `na` — §6.2 표에 없다.
3. `confirmed` × 모든 라벨 — 집계 규칙 없음.
4. `hold` × 모든 라벨 — 집계 규칙 없음.
5. `not_applicable` × 모든 라벨 — 집계 규칙 없음.
6. `status==not_applicable`인데 차단 comparison이 함께 온 경우의 우선순위
   (§6.1 표에서 "해당 없음"과 "보류" 조건이 겹친다).
7. 목록(`list`) 값의 일치 판정 규칙.
8. 정확성·잘못된 확정·불필요한 보류 지표의 **분자·분모**.
9. `해당 없음`과 `미정` 행을 분모에 넣는지.
10. 값 일치와 근거 일치를 종합 점수에 반영하는 방식.
11. 단위 별칭 계약(`톤` vs `t` 등)과, 기간·사업장·대상 차이를 값 일치 판정에 넣을지.
    현재는 환산·별칭을 하지 않고 단위 문자열 불일치로 남긴다.

**8~10이 정해지기 전에는 정확도·종합 점수를 산출할 수 없다.** 채점기도 내지 않는다.

### 6.3 원본 증빙과 실행 결과

| 자료 | 상태 |
|---|---|
| `한울정밀_촬영세트_BM개편_20260928/` (`01_처음업로드_12건`·`02_보완할때추가_1건`·`00_촬영안내_업로드하지않음`) | **없음.** 홈 디렉터리 전역 검색 무결과 |
| `output/reviews/pr71_20261005/followup_fix_validation/02_final_0b770df/runs/LIVE_61b6167/` | **없음.** `output/reviews/` 디렉터리 자체가 없음 |
| initial·followup `result.json`, `pipeline.json`, `exports/` | **없음** |
| 증빙 구성·해시 목록 | **없음** — 원본이 없어 산출 불가 |
| API 호출 수·토큰 사용량 | **없음** — 측정을 수행하지 않았다. 기존 기록에도 없어 추정치를 적지 않는다 |
| OpenAI·Upstage API 키 | `.env`에 `OPENAI_API_KEY`·`UPSTAGE_API_KEY` 존재(값 미확인·미출력) |

대체 자료로 쓰지 않은 것:

- `시연증빙세트_한울정밀공업/`(규정 PDF 10건) — BM 개편 세트와 다른 세트다.
- `output/validation/numeric_scope_output_20261005/05_followup/LIVE_61b6167_*_checks.json`·
  `*_inject.json`, `runs/*/<stage>/run_stats.json`·`environment.json` — 요약·검사 결과일 뿐
  `result.json`이 아니다. 같은 검증 README가 "전체 산출물은 사용자 프로젝트
  `output/reviews/pr71_20261005/followup_fix_validation/`"라고 적고 있으나 그 경로가 없다.

이 때문에 다음이 불가능하다.

- §5.2 원본 PDF 기준 라벨 작성·사람 확인
- §5.4 교차 확인(C-4 E-6-2의 29.3% 비교 불가, 08 내부 재투입률 92%).
  **출력계약 문서의 값을 원본 확인 없이 정답 라벨로 복사하지 않았다.**
- 실제 가상 세트 initial·followup 1회 측정

### 6.4 B 협업 경로

| 항목 | 상태 |
|---|---|
| B 연락 경로 | **없음** |
| §4 공통 답안 형식 합의 | **미시작.** 합의 완료로 적지 않는다 |
| §5.3 B의 무작위 20% 표본 — 추출 단위·개수·방식·시드 | **미정.** 단계별 추출인지 전체 96행 기준인지 원문·B 합의로 확인해야 한다 |
| B 독립 라벨링·일치율·불일치 협의 | **미시작.** A 라벨 자체가 없다 |

### 6.5 이 프롬프트와 기존 문서의 충돌

1. **페이지 기준이 다르다.** 라벨 `expected_sources`는 1-기준, 시스템
   `evidence_links[].page`는 0-기준(출력계약 §2). 표현 방식 차이로 보고 `to_label_page()`
   한 곳에서 변환했다. 의미를 완화한 것은 없다.
2. **출력계약 §3의 기대값은 교차 확인 대상이다.** 그 값들이 그대로 정답이라는 뜻이 아니므로
   라벨로 복사하지 않았다. 원본 확보 후 §5.4대로 다시 확인해야 한다.
3. `AGENTS.md`가 비어 있어 저장소 규약을 문서에서 확인할 수 없었다. 기존 코드·테스트 관행
   (`esgenie/eval/rag_eval.py`의 dataclass + argparse CLI, `tests/test_*.py`)을 따랐다.

## 7. 실제 완료 범위

| 산출물 | 상태 |
|---|---|
| A-1 채점 로직(확정 규칙) | **완료** |
| A-1 합성 입력 테스트 | **완료** (66건) |
| A-1 가상 세트 정답 라벨 | **미완료** — 원본 증빙 없음, 사람 작성·승인 없음 |
| A-1 라벨 선행 커밋·해시 기록 | **미완료** — 라벨 없음 |
| B 독립 라벨 20% 표본·일치율 | **미완료** — 표본 명세·연락 경로 없음 |
| §4 공통 답안 형식 합의 | **미완료** — 명세 없음, B 동의 없음 |
| A-2 작업시간 계측 도구 | **미착수** — A §3 계측 상세 없음 |
| 가상 BM 개편 세트 initial·followup 1회 측정 | **미완료** — 원본 증빙·`result.json` 없음 |
| 지표 산식·종합 점수 | **미확정** — §6.2의 8~10 미정 |

이번 작업에는 측정 수치가 없다. 도구가 생겼을 뿐이다. 보고서에 쓸 수치는 **2026-10-15
동결 후보 커밋에서 다시 측정**하며, 그때도 라벨을 먼저 커밋한 뒤 채점한다.
