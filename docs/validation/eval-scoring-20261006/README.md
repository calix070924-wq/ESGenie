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
| `esgenie/eval/answer_format.py` (신규) | 공통 답안 형식 v1 정의·검증기 (판정 6값, 1-기준 페이지) |
| `esgenie/eval/esgenie_adapter.py` (신규) | ESGenie `result.json` → 공통 형식. §6.1 판정 매핑과 0→1 페이지 변환 **단일 지점** |
| `esgenie/eval/response_scoring.py` (신규) | 라벨 로더·값/근거 비교·구성 검증·집계. 입력은 공통 형식이며 ESGenie 전용 필드를 모른다 |
| `scripts/eval_response_quality.py` (신규) | 채점 CLI (단일) |
| `scripts/eval_label_sample.py` (신규) | 정답 없는 표본 추출 — 공식 무작위 표본과 유형 전수 |
| `scripts/build_b_package.py` (신규) | B 전달 ZIP 빌더. 금지 검사로 정답·키 혼입을 막는다 |
| `data/eval/examples/` (신규) | 공통 형식 합성 예시 2건 |
| `data/eval/sample/bm_rba42_v1/` (신규) | 공식 무작위 표본 20행 (씨값 20261006, **고정**) |
| `data/eval/sample/bm_rba42_numeric_census/` (신규) | 수치형 전수 12행 (새로 매길 10행) |
| `docs/공통답안형식_v1초안_2026-10-06.md` (신규) | 형식 문서 — **v1 초안 / 합의 대기** |
| `docs/독립라벨링_표본패키지_B_2026-10-06.md` (신규) | B용 라벨링 가이드 |
| `docs/독립성_노출기록_2026-10-06.md` (신규) | 기대값 노출 기록과 독립 검토 인정 판단 절차 |
| `docs/응답품질채점_사용법_2026-10-06.md` (신규) | 채점 CLI 사용법 |
| `docs/지표정의표_2026-10-06.md` (신규) | 지표 정의·분자·분모·확정 여부 |
| `docs/작업시간계측_기존계측조사_2026-10-06.md` (신규) | A-2 기존 계측 조사 (구현 미착수) |
| `tests/test_eval_answer_format.py`·`test_eval_label_sample.py`·`test_eval_response_scoring.py`·`test_build_b_package.py` | 합성 입력 테스트 **148건** |
| `docs/validation/eval-scoring-20261006/README.md` (신규) | 이 기록 |

제품 코드는 고치지 않았다. `esgenie/supplychain/exporters/*`·`schema.py`·`mapping.py`는
건드리지 않았고, 인터페이스 요구사항도 생기지 않았다.

설계에서 지킨 것:

- **정답은 라벨 파일에서만 읽는다.** 회사명·파일명·문항 ID·정답 수치로 분기하지 않는다.
  단계별 48행 구성도 `get_framework(<키>).questions`에서 읽어 넘긴다(`_framework_qids`).
- **확정 규칙만 구현했다.** 확정된 것은 작업지시서 A §6.1(시스템 판정 매핑)과
  작업지시서 A §6.2(미검증 전달 행의 집계)뿐이다. 그 밖의 조합은 `unresolved`로 남긴다.
- **비율·정확도·종합 점수를 내지 않는다.** 지표의 분자·분모가 미정이므로
  `ScoreReport`에는 건수만 있고 `metrics_blocked_reason`에 그 이유가 들어 있다.
  `unresolved`가 0건이어도 비율을 내지 않는다.
  - **이 줄은 이 기록 시점 기준이다. 이후 변경됐다.** 2026-10-06 지민이 Q1~Q8(적용
    대상·분모·예외 처리)을 확정해 M1·M4·M5 비율을 구현했다. `metrics_blocked_reason`은
    없어지고 `metric_scopes[]`가 들어갔다. **종합 점수는 전후 모두 만들지 않는다.**
    확정 내용은 `docs/지표결정요청표_2026-10-06.md` §6, 행별 집계 판정표는 §6.2,
    판정표 이전 단계의 미정은 §6.3.
- **공통 형식은 A가 새로 작성한 v1 초안이다.** 작업지시서 A §4 원문을 찾은 것이 아니다
  (§6.1 참조). 지민 지시로 A가 초안을 쓰고 B와 합의를 진행한다. 합의 전까지
  `format_version`은 `1.0-draft`이고, 문서 상태는 "v1 초안 / 합의 대기"다.
  구조는 세 겹으로만 나눴다 — 형식 정의·검증(`answer_format`), 시스템별 변환
  (`esgenie_adapter`), 채점(`response_scoring`). 플러그인·배치 프레임워크는 없다.

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
| `unresolved` | `not_applicable` + 차단 비교 — 계약에 우선순위가 없다(이 문서 §6.2.1 6번) |

값 `0`과 `False`는 채워진 값으로 센다(`_is_filled`). `self_reported`는 어떤 경우에도
확정으로 세지 않는다.

### 2.2 페이지 번호 변환

시스템 `evidence_links[].page`는 **0-기준**이다(`esgenie/ssot/audit_trace.py:35`,
UI 출력계약 §2 "0-기준, 화면은 +1"). 공통 형식의 `page`와 라벨 `expected_sources`는
**둘 다 1-기준**이다.

- 변환 지점은 `esgenie_adapter.to_common_page()` **한 곳뿐**이다. 호출자는 어댑터의
  근거 변환(`_links`)뿐이다.
- **채점기에는 변환이 없다.** `response_scoring`은 공통 형식의 `page`를 그대로
  `EvidenceRef.page_1based`에 담는다. 라벨도 1-기준이라 양쪽 모두 변환하지 않는다.
- 이미 1-기준인 대조군 근거는 어댑터를 거치지 않으므로 다시 변환되지 않는다.
- 페이지가 없으면(`None`·bool·파싱 실패) **첫 페이지로 간주하지 않고** `None`으로 남기고,
  근거 비교에서 불일치로 처리하며 이유에 "시스템 근거에 페이지가 없다"를 남긴다.
- 테스트: `test_eval_answer_format.py::test_page_conversion_is_zero_to_one_based`,
  `::test_adapter_converts_page_exactly_once`(이미 변환된 값을 다시 넣으면 4가 되는 것을
  고정해 이중 변환을 드러낸다), `::test_converted_pages_pass_format_validation`,
  `test_eval_response_scoring.py::test_source_page_off_by_one_is_mismatch`,
  `::test_source_without_page_is_mismatch_not_first_page`,
  `::test_answers_come_from_the_common_format_without_page_conversion`.

### 2.3 값·근거 비교

- 수치: 천 단위 구분기호만 지우고 `abs(시스템 − 라벨) <= tolerance`(경계 포함). 기본 0.
- 단위: NFKC·공백·대소문자만 고르고 **환산·별칭을 하지 않는다**. `톤` vs `t`, `톤` vs `kg`는
  불일치로 남긴다. 기간·사업장은 비교 대상에 넣지 않았다(이 문서 §6.2.1 7번).
- 예/아니오: 시스템 값의 자료형(`bool`)으로 형태를 가른다. `"0"`·`"1"`은 예/아니오
  토큰으로 읽지 않는다 — 수치 0을 예/아니오로 뒤바꾸면 안 된다.
- 목록 값(`list`)은 비교 규칙이 없어 `None`(미정)으로 남긴다.
- 근거: 정답 근거 여러 건 중 **하나라도** `(파일명, 1-기준 페이지)`가 맞으면 일치.
- 값 일치와 근거 일치는 각각 `value_match`·`source_match`로 따로 기록·집계한다.

### 2.4 집계 위치(작업지시서 A §6.2) — 확정 규칙

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

### 3.2 빈 라벨 서식 — 정답이 아니다

`data/eval/sample/<집합>/labels_blank.csv`는 **같은 헤더의 빈 서식**이다. `stage`와
`qid`만 채워져 있고 나머지 열은 전부 비어 있다. 채점기는 이 서식을 그대로 넣으면
`LabelError`로 거부한다(`test_eval_label_sample.py::test_blank_form_is_refused_by_the_scorer`).
이 서식은 확정 라벨이 아니므로 `data/eval/labels/`에 두지 않는다.

## 4. 수행한 검증과 결과

환경: Python 3.14.6, worktree `/tmp/esg_eval_scoring`, 기준 `d8a0de8a…`.

| 검증 | 명령 | 결과 |
|---|---|---|
| 평가 도구 테스트(1차, 채점기만) | `python -m pytest tests/test_eval_response_scoring.py -q` | **66 passed** |
| 평가 도구 테스트(2차, 공통 형식 전환 후) | `python -m pytest tests/test_eval_answer_format.py tests/test_eval_label_sample.py tests/test_eval_response_scoring.py tests/test_build_b_package.py -q` | **148 passed** |
| 전체 스위트(회귀, 1차) | `python -m pytest -q` | **5531 passed, 27 skipped** (73초) |
| 전체 스위트(회귀, 2차) | `python -m pytest -q` | **5613 passed, 27 skipped** (66.6초, skip은 기존 것) |
| 평가 도구 테스트(3차, 독립성·도구 패키지 추가 후) | 위 2차 명령 + `tests/test_eval_independence.py` | **170 passed** |
| 전체 스위트(회귀, 3차) | `python -m pytest -q` | **5635 passed, 27 skipped** (67.8초) |
| 합성 입력 CLI 실행(제품 의존성 없이) | `scripts/eval_response_quality.py --expected-qids …/qids_SYN.txt` | 종료 코드 0, `bucket_counts` 네 종류 각 1건 |

skip·xfail로 실패를 가린 테스트는 추가하지 않았다(새 테스트 170건 전부 실제 통과).
2차에서 늘어난 82건은 공통 형식·어댑터·표본 추출·패키지 빌더 테스트이고, 3차에서 늘어난
22건은 독립 검토 인정 규칙(13건)과 도구 패키지 단계(9건)이다. 기존 통과 건수는 줄지 않았다.

**합성 실행이 통과한 것을 실제 평가 완료로 적지 않는다.** 입력이 전부 합성이다.

### 4.1 §7 필수 테스트 항목 대응

| 요구 항목 | 테스트 |
|---|---|
| verified + 값 있음/없음, 값 0 | `test_verified_with_value_is_confirmed`, `test_verified_without_value_is_hold_not_confirmed`, `test_zero_and_false_are_filled_values_not_empty`, `test_zero_value_is_compared_as_number_not_empty` |
| 세 가지 차단 comparison | `test_blocking_comparison_forces_hold` (verified·self_reported × 3) |
| 작업지시서 A §6.2 미검증 전달 집계 | `test_self_reported_hold_no_evidence_is_correct_hold`, `test_self_reported_hold_mismatch_family_is_wrong_confirmation`, `test_self_reported_answer_value_mismatch_is_wrong_confirmation`, `test_self_reported_answer_value_match_is_unnecessary_hold` |
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
| 미정 행을 `correct_hold`로 처리 | 5 failed |
| 단위를 `톤`→`t` 별칭으로 정규화 | 1 failed |

## 5. 다시 검증하는 방법

```bash
git fetch origin
git worktree add -b verify/eval-scoring-20261006 /tmp/verify_eval_scoring origin/codex/eval-scoring-20261006
cd /tmp/verify_eval_scoring

# 1) 평가 도구 테스트
python -m pytest tests/test_eval_answer_format.py tests/test_eval_label_sample.py \
  tests/test_eval_response_scoring.py tests/test_eval_independence.py \
  tests/test_build_b_package.py -q                           # 170 passed 기대

# 2) 회귀
python -m pytest -q                                          # 5635 passed, 27 skipped 기대

# 3) 표본 재현 (같은 씨값이면 같은 표본)
python scripts/eval_label_sample.py --seed 20261006 --out /tmp/chk_sample
python scripts/eval_label_sample.py --mode census --qtype numeric \
  --overlap-with data/eval/sample/bm_rba42_v1/sample.json --out /tmp/chk_census
diff -r data/eval/sample/bm_rba42_v1 /tmp/chk_sample          # README.md만 차이
diff -r data/eval/sample/bm_rba42_numeric_census /tmp/chk_census

# 4) 합성 입력으로 채점기 실행 (설치 없이, 종료 코드 0)
python scripts/eval_response_quality.py \
  --labels data/eval/examples/synthetic_run/labels_SYN.csv \
  --answers data/eval/examples/synthetic_run/answers_SYN_initial.json \
  --expected-qids data/eval/examples/synthetic_run/qids_SYN.txt \
  --out /tmp/chk_syn.json

# 5) 독립 검토 인정 집계
python scripts/eval_independence_report.py \
  --sample data/eval/sample/bm_rba42_v1/sample.json \
  --sample data/eval/sample/bm_rba42_numeric_census/sample.json \
  --exposures data/eval/independence/exposure_log.csv --labeler A --labeler B

# 6) 파일 해시
shasum -a 256 esgenie/eval/answer_format.py esgenie/eval/esgenie_adapter.py \
  esgenie/eval/response_scoring.py esgenie/eval/independence.py \
  scripts/eval_response_quality.py scripts/eval_label_sample.py \
  scripts/eval_independence_report.py scripts/export_framework_qids.py \
  scripts/build_b_package.py data/eval/framework/rba42_qids.txt
```

기대 해시(3차 기록 작성 시점):

```
89ea4ab87940706e54339fe99eca1767e29caed7a709ea106189d2d754175220  esgenie/eval/answer_format.py
325e1c4af213eb6f6d9878e13ec211d88cf81cb074560752f6e2dcf77100663d  esgenie/eval/esgenie_adapter.py
122c1598552b5b1d56a51b937f41dcfdee9406cfd4e74f12f497a6ee6d9a78e8  esgenie/eval/response_scoring.py
2f1e9d005e2e04e5708c4e9cd54f891d502fa339e315c8940c450c3450913a53  esgenie/eval/independence.py
69a87ef6338ec3eba1635d8f1b7077d545776d272ca2e7481faca4cd9af632ea  scripts/eval_response_quality.py
3b2b1d86840985ab33fdb43aeced5bbd4eed75e512d13b5ec10e6e1b49472165  scripts/eval_label_sample.py
c52871953c7afbd00efd03ba26a544c3348537c431eab883eee7bf944b3c5288  scripts/eval_independence_report.py
ce521a6524af9f41302dee1e6850850219c3d015141b90e7f74e0cf3f8143c59  scripts/export_framework_qids.py
7c6dd0cabc22cf1cc333edc79f01ed9cc6a0974e8b74d7d3b1dd4a77fd9ef01e  scripts/build_b_package.py
30b3a119112dfcc2768259bbe72d3f1cad97527dade72909729d7354707ee5df  data/eval/framework/rba42_qids.txt
```

1차 기록의 해시(`response_scoring.py` `24c6fbe5…`, 테스트 `98e1d320…`)는 공통 형식
전환으로 더 이상 맞지 않는다. 2차 기록의 `eval_response_quality.py` `faef209a…`와
`build_b_package.py` `d486649b…`도 `--expected-qids`·`tools` 단계 추가로 바뀌었다.
위 해시가 현재 값이다.

라벨과 실제 실행 결과가 생기면 채점은 다음과 같이 돌린다(지금은 입력이 없어 실행하지 않았다).
사용법 전체는 `docs/응답품질채점_사용법_2026-10-06.md`.

```bash
python scripts/eval_response_quality.py \
  --labels data/eval/labels/<확정 라벨>.csv \
  --esgenie-result initial=<...>/initial/result.json \
  --esgenie-result followup=<...>/followup/result.json \
  --run-id <실행 식별자> --data-source <자료 출처> \
  --write-common outputs/eval/common/<실행 식별자> \
  --out outputs/eval/scores/<실행 식별자>.json
```

B 전달 ZIP(독립 검토 전 전달본):

```bash
python scripts/build_b_package.py --stage pre_review \
  --out outputs/b_package/esgenie_eval_b_pre_review_<날짜>.zip \
  --manifest outputs/b_package/manifest_pre_review.json
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

#### 6.2.1 채점 규칙 — 행 하나를 어디에 넣는가

1. `self_reported` × `hold(needs_human_text)` — 작업지시서 A §6.2 표에 없다.
2. `self_reported` × `na` — 작업지시서 A §6.2 표에 없다.
3. `confirmed` × 모든 라벨 — 집계 규칙 없음.
4. `hold` × 모든 라벨 — 집계 규칙 없음. 라벨과 시스템의 **보류 사유가 다를 때**
   올바른 보류로 세는지도 정해지지 않았다.
5. `not_applicable` × 모든 라벨 — 집계 규칙 없음.
6. `status==not_applicable`인데 차단 comparison이 함께 온 경우의 우선순위
   (작업지시서 A §6.1 표에서 "해당 없음"과 "보류" 조건이 겹친다). 실제 `result.json`에서
   이 조합이 0건이면 결정 자체가 필요 없다.
7. 단위 별칭 계약(`톤` vs `t`, `%` vs `퍼센트` 등)을 표기 차이로 볼지, 그리고
   `boundary_note`(기간·사업장·대상·분모) 대조를 값 일치 판정에 넣을지.
   현재는 환산·별칭을 하지 않고 단위 문자열 불일치로 남기며, 범위는 값 판정에 넣지 않는다.
   **"단위·기간·대상 차이를 정규화로 숨기지 않는다"는 금지 규칙은 작업지시서 A §7에
   이미 명시돼 있고 현재 구현이 이를 지킨다.** 열린 것은 별칭 허용 범위뿐이다.

#### 6.2.2 지표 산식 — 건수를 비율로 바꾸는 방법

**분자 조건은 확정됐다**(2026-10-06 지민 지시). 남은 것은 적용 대상·분모·예외 처리뿐이다.

| 확정 (승인 대상이 아니다) |
|---|
| 답변·근거가 **모두** 맞아야 동시 정답으로 센다 |
| 잘못된 확정·불필요한 보류는 **건수**로 낸다 |
| 자동응답률은 **`confirmed`만** 센다. `self_reported`는 확정 응답이 아니다 |
| **지표를 하나의 종합 점수로 합치지 않는다. 향후 구현 대상도 아니다** |

**8~10은 2026-10-06 지민 승인으로 확정됐고 구현했다**(`docs/지표결정요청표_2026-10-06.md`
§6 Q1~Q8). 이 기록을 쓸 때는 결정 대기였다.

8. M1의 적용 대상 = 라벨 `answer` 행, 분모 = `answer` 라벨 **전체**(누락·파싱 실패 포함).
9. M4 분모 = 실행·단계별 예상 문항 전체. M5 분모 = **값이 채워진** `confirmed`·
   `unverified_submitted` 행.
10. `na`는 M1 제외·M4 포함·M5 추가 제외 없음. 누락·`unparsed`는 M1·M4 분모 포함·분자
    제외. `unresolved`는 **분모에서 빼지 않는다**.

**행별 집계 조합은 2026-10-06에 확정됐다**(같은 문서 §6.2 판정표). 이 기록을 쓸 때는
R1~R8로 남아 있었다. 남은 것은 판정표 **이전 단계**의 미정이며 같은 문서 §6.3에 있다.

**Q7 정정:** 한동안 "지표별 분모에 영향을 주는 미정만 보류"로 좁혀 적었는데 그 해석은
승인되지 않았고 되돌렸다. 승인된 동작은 **`unresolved` 행이 1건이라도 있는 실행·단계의
공식 비율 전체 보류**다.

종합 점수는 전후 모두 **만들지 않는다.**

#### 6.2.3 기존 명세 준수 점검 항목 (새 정책 결정이 아님)

- 라벨 `expected_value`는 작업지시서 A §5.1에서 "answer일 때 **수치 또는 예/아니오**"로
  이미 한정돼 있다. 현재 로더는 그 밖의 자유 문자열도 받아들이므로 **명세대로 거부하도록
  점검·보완할 항목**이다. 새로 정할 정책이 아니다.
- 목록(`list`) 값의 일치 판정은 **이번 rba42 평가 범위의 결정 목록에서 제외한다.**
  rba42 문항 구성은 `yes_no_evidence` 42 + `numeric` 6이고 `option_map`을 쓰는 문항이
  0건이어서 목록 값이 발생하지 않는다(`get_framework("rba42")`로 확인). 다른 양식으로
  범위가 넓어질 때 다시 본다.

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
| B 연락 경로 | **A가 직접 보내지 않는다.** 지민이 전달한다 |
| §4 공통 답안 형식 합의 | **미시작.** A가 v1 초안을 작성해 검토 요청만 했다. 합의 완료로 적지 않는다 |
| §5.3 표본 추출 단위·개수·방식·시드 | **지민 지시로 확정** — 단계별 10문항 × 2단계 = 20행(전체 96행의 20.8%), 유형 층화 후 씨값 20261006. **고정이며 다시 뽑지 않는다** |
| 수치형 전수 추가 검토 | **지민 지시로 추가** — 수치형 6문항 × 2단계, 공식 표본과 겹치는 1문항은 라벨 재사용. 결과는 **분리 보고** |
| B 독립 라벨링·일치율·불일치 협의 | **미시작.** A 라벨 자체가 없다 |
| 기대값 노출과 독립 검토 인정 | `docs/독립성_노출기록_2026-10-06.md`에 기록. **A는 두 문항에 대해 독립 라벨러가 아니다**(아래 6.5) |

### 6.5 A의 기대값 노출 — 숨기지 않고 적는다

A는 PR #71 작업 과정에서 `docs/validation/...`(이 문서)과
`docs/UI연결용_수치범위_출력계약_2026-10-05.md`의 기대값을 보았다. 따라서
`RBA-C-4-E-6-2`와 K-ESG 08 노드(재투입률)에 대해 **A는 독립 라벨러가 아니다.**

- 이 두 문항은 **A가 단독으로 라벨을 확정하지 않는다.** B 또는 제3자의 라벨을 기준으로
  삼고 A 라벨은 참고로만 쓴다.
- 공식 표본 20행에는 이 문항이 들어가지 않았다. 수치형 전수 집합에는 전수이므로 들어간다.
- 노출을 이유로 표본을 다시 뽑지 않는다.
- B의 열람 여부는 **확인되지 않았다.** 라벨 제출 시 열람 확인 3문항으로 받는다.
  "PR을 탐색하지 말라"는 안내만으로 독립성이 확보됐다고 보지 않는다.
- 인정 기준(노출 + 일치 → 독립 일치로 세지 않음 등)은 **제안 상태**이며 지민 확인 대기다.

**전달 전에 찾아 고친 유출 1건.** 공통 형식 초안 §5의 근거 예시에 실제 증빙 문구
(K-ESG 08 노드 재투입률의 값이 들어간 인용문)를 써 두었다. ZIP을 만든 뒤 압축을 풀어
사람이 읽다가 찾았고, 합성 자리값으로 바꾼 뒤 ZIP을 다시 만들었다. **B에게 전달한
ZIP에는 들어가지 않았다**(전달 전에 고쳤고, 최종 ZIP의 sha256으로 구분된다).

- 빌더의 금지 검사는 **경로·키 표식·빈 서식**만 본다. 허용된 문서 **본문에 인용된**
  기대값은 잡지 못했다. 실제 수치를 빌더에 적어 비교하면 그 수치가 저장소에 남으므로
  그렇게 하지 않는다.
- 보완으로 넣은 것: 합성 예시 JSON의 근거 인용문에 합성 표시가 있는지 보는 검사
  (`_unmarked_quotes`, `test_audit_rejects_an_unmarked_evidence_quote`). 이것도
  **문서 본문은 보지 못한다.** 따라서 **전달 전 압축 내용 검토가 절차에 남는다.**
  검토 주체는 구분해 적는다 — 자동검사 / **AI 내용 검토** / 사람 검토.
  1·2차 전달본에 수행된 것은 자동검사와 **AI 내용 검토**이고 **사람 검토는 미수행**이다
  (`docs/B전달기록_2026-10-06.md` §3.1). AI가 읽은 것을 사람 확인으로 적지 않는다.

### 6.6 이 프롬프트와 기존 문서의 충돌

1. **페이지 기준이 다르다.** 라벨 `expected_sources`와 공통 형식 `page`는 1-기준, 시스템
   `evidence_links[].page`는 0-기준(출력계약 §2). 표현 방식 차이로 보고
   `esgenie_adapter.to_common_page()` **한 곳에서만** 변환한다(이 문서 §2.2).
   채점기에는 변환이 없다. 의미를 완화한 것은 없다.
2. **출력계약 §3의 기대값은 교차 확인 대상이다.** 그 값들이 그대로 정답이라는 뜻이 아니므로
   라벨로 복사하지 않았다. 원본 확보 후 §5.4대로 다시 확인해야 한다.
3. `AGENTS.md`가 비어 있어 저장소 규약을 문서에서 확인할 수 없었다. 기존 코드·테스트 관행
   (`esgenie/eval/rag_eval.py`의 dataclass + argparse CLI, `tests/test_*.py`)을 따랐다.

### 6.7 독립 검토 인정 집계 — 지금 세어 둔 것

확정 기준(노출 확인 행은 **일치 여부와 무관하게** 분자·분모에서 제외, 확인 대기는 독립으로
세지 않음)을 구현해 현재 상태를 셌다. 표본은 **다시 뽑지 않았다.**

| 집합 | 라벨러 | 원래 표본 행 | 독립 확인 완료 | 노출 제외 | 확인 대기 |
|---|---|---|---|---|---|
| `bm_rba42_v1` | A | 20 | 0 | 0 | **20** |
| `bm_rba42_v1` | B | 20 | 0 | 0 | **20** |
| `bm_rba42_numeric_census` | A | 12 | 0 | **2** (`RBA-C-4-E-6-2` 두 단계) | 10 |
| `bm_rba42_numeric_census` | B | 12 | 0 | 0 | **12** |

확인 대기의 이유는 하나다: **열람 확인 회신이 없다.** 제외된 A의 2행은 §6.5의 자가 신고
노출 기록에서 나온다. 전체 PR 접근을 이유로 일괄 제외하지 않았다 — 사람·단계·문항별로
적힌 기록만 센다.

**독립 일치율은 산출하지 않는다.** 라벨이 없고 분모가 확정되지 않았다. 0%로 적지 않는다.
제외한 행의 라벨은 생기면 **폐기하지 않고** 비독립 검토 기록으로 보존하며, 최종 정답
라벨로 쓸 수 있는지는 **별개 판단**이다. 기준·기록·재현 명령은
`docs/독립성_노출기록_2026-10-06.md` §5~§7.

## 7. 자료 확보 후 진행 순서

**명세 확보와 원본 확보는 서로 선행 조건이 아니다.** 아래 두 줄기는 독립이며,
확보된 쪽부터 진행한다. 묶어서 기다리지 않는다.

**줄기 ①: 원본 증빙(§6.3) 확보 시 — 명세를 기다리지 않고 진행 가능**

| 순서 | 작업 | 통과 조건 |
|---|---|---|
| 1 | 증빙 구성·해시 기록 | `01_처음업로드_12건`과 `02_보완할때추가_1건` 폴더 분리 유지 |
| 2 | 원본 기반 라벨 초안 96행 작성 | **ESGenie 출력을 보지 않고** 원본 PDF + 문항 정의로만. initial 라벨에 followup 증빙을 섞지 않는다. 문구만으로 갈리는 행은 `note`에 이유를 남긴다 |
| 3 | **출력계약 §3 교차 확인** — C-4 E-6-2의 29.3% 비교 불가, 08 내부 재투입률 92% | **라벨 검토 중, 라벨 확정·선행 커밋 전에** 수행한다. 원본을 다시 확인하고 이유를 `note`에 남긴다. 계약 문서 값을 원본 확인 없이 라벨로 복사하지 않는다 |
| 4 | 지민 확인 — 2·3단계의 `note` 행 판정 | 확인 전 라벨 확정 안 함 |
| 5 | B 독립 검토 — 표본 추출(시드 기록) → B 라벨링 → 일치율·불일치 이유 → 협의 | B에게 A 정답·ESGenie 답변을 주지 않는다. **협의 전 A·B 라벨을 각각 보존한다.** B의 실제 검토 없이 완료로 적지 않는다 |
| 6 | **라벨 확정 및 선행 커밋** | `data/eval/labels/hanwool_bm_rba42_v1.csv` 커밋 후 **전체 SHA·파일 해시를 이 문서에 기록**한다. 그 기록 전에는 7단계로 넘어가지 않는다 |
| 7 | 실제 실행 결과(§6.3)로 채점 | 라벨 커밋 이후에만. **실제 결과를 보고 정답 라벨을 맞추지 않는다.** 고칠 근거가 생기면 라벨 변경을 별도 커밋으로 남기고 변경 이유·전후를 기록한다 |

**줄기 ②: 명세(§6.1) 확보 시 — 원본을 기다리지 않고 진행 가능**

| 순서 | 작업 | 통과 조건 |
|---|---|---|
| 1 | A §3 계측 상세 확보 → **A-2 착수** | 그때의 최신 `origin/main`에서 `codex/eval-timing-20261006` 브랜치·worktree 분기. 제품 코드 변경은 계측 명세에 필요한 최소 범위 |
| 2 | A §4 공통 답안 형식 확보 → B와 형식 합의 | B의 동의 없이 "합의 완료"로 적지 않는다 |
| 3 | 전략 문서 확보 → §6.2.2 지표 산식(8~10) 확정 | **지민 승인 후에만** 코드에 반영. 승인 전에는 비율·점수를 내지 않는다 |
| 4 | §6.2.1 채점 규칙(1~7) 확정 | 같음 — 승인 전 추정 구현 금지 |

줄기 ①의 2~6단계는 줄기 ②와 무관하게 진행할 수 있다. 다만 **줄기 ①의 7단계(채점)로
나온 분류 결과를 지표·점수로 바꾸는 것은 줄기 ②의 3·4단계가 끝난 뒤**다.

이번 측정은 도구 검증용이다. 보고용 최종 수치는 **2026-10-15 동결 후보 커밋**에서
다시 측정하며, 그때도 6→7 순서를 지킨다.

## 8. 실제 완료 범위

| 산출물 | 상태 |
|---|---|
| A-1 채점 로직(확정 규칙) | **구현 검증 완료** |
| 공통 답안 형식 v1 정의·검증기·어댑터 | **구현 검증 완료** / 형식 **합의 미완료** |
| A-1 합성 입력 테스트 | **완료** (170건) |
| 채점 CLI | **구현 검증 완료** / **실제 채점 미실행** |
| 채점 CLI를 제품 의존성 없이 돌리는 경로(`--expected-qids`) | **구현·실행 검증 완료** (합성 입력, 종료 코드 0) |
| 순수 합성 실행 예시 세트 | **작성·실행 검증 완료** — 실제 평가가 아니다 |
| 채점 사용법 문서·지표 정의표 | **작성 완료** / 지표 산식은 **미정** |
| 지표 산식 결정 요청표(§5 Q1~Q8) | **작성 완료** / **결정 대기** — 코드에 적용하지 않았다 |
| 독립 검토 인정 제외 규칙 | **확정 기준 구현 검증 완료** (13건) |
| 독립 검토 인정 집계(원표본·확인완료·제외·대기) | **산출 완료** — §6.7 |
| 독립 라벨링 표본(공식 20행 + 수치형 전수) | **목록·서식·가이드 완료** / **실제 라벨링 미착수** |
| B 전달 ZIP (독립 검토 전 전달본) | **생성 완료** — 정답 없음, 금지 검사 통과. **보존 대상** |
| B 전달 ZIP (채점 도구 패키지) | **생성·재현 검증 완료** — 정답 없음. 전달 기록은 `docs/B전달기록_2026-10-06.md` |
| B 전달 ZIP (최종본) | **미생성** — 형식 합의·확정 라벨이 없어 빌더가 거부한다 |
| A-1 가상 세트 정답 라벨 | **미완료** — 원본 증빙 없음, 사람 작성·승인 없음 |
| A-1 라벨 선행 커밋·해시 기록 | **미완료** — 라벨 없음 |
| B 독립 라벨 일치율 | **미완료** — B 라벨 없음. 집합별로 분리해 산출한다 |
| §4 공통 답안 형식 합의 | **미완료** — 원문 없음, A 초안에 대한 B 동의 없음 |
| A-2 작업시간 계측 도구 | **미착수** — A §3 계측 상세 없음. 기존 계측 조사 기록만 작성 |
| 가상 BM 개편 세트 initial·followup 1회 측정 | **미완료** — 원본 증빙·`result.json` 없음 |
| 지표 분자 조건 | **확정** — 동시 정답은 값·근거 모두 일치, 자동응답률은 `confirmed`만 |
| 지표 적용 대상·분모·예외 처리 | **결정 대기** — §6.2.2의 8~10 |
| 종합 점수 | **만들지 않는다**(확정 요구사항). 미완료 기능이 아니다 |

**"채점기 납품 전체 완료"가 아니다.** CLI 구현·합성 테스트가 끝났을 뿐이고, 지표 산식이
미정이며 실제 산출·측정이 남아 있다.

이번 작업에는 측정 수치가 없다. 도구가 생겼을 뿐이다. 보고서에 쓸 수치는 **2026-10-15
동결 후보 커밋에서 다시 측정**하며, 그때도 라벨을 먼저 커밋한 뒤 채점한다.
