# 5·6차 검토 후속 수정 검증 기록 — 5차

이 폴더는 2026-09-23 **5·6차 검토**(5차: HEAD `6b55c2a` 대상, 6차: 5차 지적의 미커밋 수정 diff
`39d456e0…` 대상) 후속 수정의 실행 기록이다. 1~4차 기록 폴더
([followup](../hmc-response-integrity-followup-20260921/)·[followup2](../hmc-response-integrity-followup2-20260921/)·[followup3](../hmc-response-integrity-followup3-20260921/)·[followup4](../hmc-response-integrity-followup4-20260921/))는
그대로 보존했다. 검토자 기록(`outputs/reviews/hmc-integrity-fifth-review-20260923/`,
`…-sixth-review-20260923/`)도 읽기만 했다. 결과는 보고서의
[5·6차 검토 후속 수정](../../HMC_응답서_검증정합성_후속수정결과_2026-09-21.md#56차-검토-후속-수정--2026-09-23-5차) 절과 함께 읽는다.

## 이번 회차가 고정한 것

- `reason_and_clause_results.json`, `review6_probes_console.txt`: 6차 probe
  (`probe_reason_and_clause.py`) 원문을 수정 후 코드로 실행한 결과. 수정 전 20건 중 5건 실패
  (MWh 상충 근거 2·쉼표 경계 3), 지금은 **20/20, `failed: []`**.
- `unit_and_tense_results.json`, `review5_probes_console.txt`: 5차 probe(`probe_unit_and_tense.py`).
  수정 전 15건 중 5건 실패(단위 표기 2·연결 어미 3), 지금은 **15/15**.
- `run_review6_probes.py`, `run_review5_probes.py`: 위 두 드라이버. 검토자 원문은 고치지 않고
  출력 폴더 상수만 이 회차 폴더로 치환해 실행한다(치환 실패 시 assert로 중단).
- `combination_results.json`·`remaining_probes.json`·`after_fix_checks.json`(+ 각 console, 드라이버):
  4차 조합 **13/13**, 3차 **14/14**, 2차 정상 기준 **20/20** 재확인.
- `previous_probes/reproductions.json`: 검토자 스크립트가 함께 재생하는 이전 회차 probe 사본(4차 폴더 사본).
- `negative_control_tests.txt`: 새 회귀 24건의 음성 대조.
  - (a) HEAD `6b55c2a` 제품 코드에서 **15건 실패**, 9건 통과.
  - (b) 6차가 검토한 diff의 제품 코드(`git archive HEAD` 사본에 `reviewed_uncommitted.patch` 적용)에서
    6차 회귀 14건 중 **6건 실패**. 검토자 지적 5건과 같은 문장 미취득 1건이다. 정상 대조군 8건은 통과.
- `full_tests.txt`: 전체 **1,585 passed / 12 skipped / 경고 5개**(4차 1,561 + 이번 24).
  skip 12건은 변동 없다(LG화학 5개·현대모비스 7개 실제 보고서 PDF 부재).
- `followup_tests.txt`: `tests/test_hmc_followup.py` **100 passed**.
- `validation_report.json`, `replay_console.txt`: 실제 시연 PDF + 저장 OCR 캐시 재생.
  실제 75건·통제 76건, 실패 0, 음성 대조군 5종 모두 거부.
- `ui_apptest.json`, `ui_console.txt`, `run_ui_apptest.py`: 이번 재생 산출물의 production Streamlit
  렌더 소비 검사 **15건 통과, 예외 0**.
- `output_manifest.json`: Git 제외 산출물 22개의 절대 경로·sha256·크기.
- `preservation.json`: 원본 폴더·검토자 기록·이전 회차 기록·산출물 보존 대조와 최종 diff 해시.

## 4차 산출물과의 동일성

실제·통제 `response_sheet.json`의 sha256이 1~4차와 같다(실제 `11fdb408…`, 통제 `7bed3700…`).
검사 수(75/76)와 `pdf_regions`·`xlsx_rows`도 같다. 실제 자료 값은 0.873988 TJ,
88.397 tCO2eq, 29.3 %, 10.6 %로 유지된다. 이번 수정은 합성 조합 입력의 상충 근거와
인증 문장 판정만 바꾸고, 실제 입력의 확정 결과는 바꾸지 않았다.

## 보존 대조(`preservation.json`)

| 항목 | 결과 |
|---|---|
| 원본 폴더 HEAD / 미커밋 / stash | `fa81817` / 28항목 / 4개 |
| 원본 증빙 PDF·저장 OCR 캐시 | 53개·859개, 읽기만 함 |
| 검토자 기록 `outputs/reviews/` | 62개 파일, 이번 실행 전후 sha256 목록 동일 |
| 1~4차 검증 기록 문서 | 변경 0건 |
| 1~4차 Git 제외 산출물 | manifest 36·22·22·22개 해시 전부 일치 |

묶음 해시(`digest_of_digests`)는 이번 회차부터 `digest_rule`에 계산 규칙을 적는다. 규칙은
"파일별 sha256과 루트 기준 상대경로를 한 줄로 쓰고, 상대경로 순으로 정렬해 줄바꿈으로 이은 문자열의
sha256"이다. 4차까지의 묶음 값은 규칙이 기록되지 않아 이번 값과 비교하지 않는다.

## 재생 명령

`PY=/Users/heojeongmin/Documents/Claude/Projects/ESGenie/venv/bin/python`, 작업 디렉터리는 worktree 루트.

```sh
$PY -m pytest -q -p no:randomly
$PY -m pytest tests/test_hmc_followup.py -v -p no:randomly
$PY scripts/hmc_integrity_validation.py \
  --evidence-dir /Users/heojeongmin/Documents/Claude/Projects/ESGenie/시연증빙세트_한울정밀공업 \
  --cache-dir /Users/heojeongmin/Documents/Claude/Projects/ESGenie/data/_cache/ocr \
  --out-dir outputs/validation/hmc-response-integrity-followup5-20260923 \
  --log-dir docs/validation/hmc-response-integrity-followup5-20260923
$PY docs/validation/hmc-response-integrity-followup5-20260923/run_ui_apptest.py
$PY docs/validation/hmc-response-integrity-followup5-20260923/run_review6_probes.py
$PY docs/validation/hmc-response-integrity-followup5-20260923/run_review5_probes.py
$PY docs/validation/hmc-response-integrity-followup5-20260923/run_review4_probes.py
$PY docs/validation/hmc-response-integrity-followup5-20260923/run_review_probes.py
$PY -O docs/validation/hmc-response-integrity-followup5-20260923/run_second_review_checks.py
```

음성 대조 (a)는 제품 파일 2개를 `git stash push -- esgenie`로 되돌린 상태에서
`-k "review5 or review6"`를 실행해 얻었다. (b)는 별도 사본에서 실행했다. 작업 트리의 제품 diff는
두 실행 전후 해시가 같다.

## 검증 성격

- 실제 추출: 시연 PDF 로컬 텍스트·좌표 → 기존 구조화 파서. 저장 캐시 재생: 새 OCR 호출이 아니다.
- 합성 대조군: 5·6차 probe와 `test_review5/6`의 측정값 입력은 합성 OCR fixture다.
- 모킹 LLM: 인증 초안은 `MagicMock`이 고정 문장을 돌려준다. `evaluate_grounding()`과 `_attempt_draft()`는
  production 코드 그대로다. **라이브 LLM 호출은 하지 않았다.**
- 전 페이지 이미지 검수: 제품 렌더러 변경이 없고 응답 JSON 해시가 같아 반복하지 않았다.
