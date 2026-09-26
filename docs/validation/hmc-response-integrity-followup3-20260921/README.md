# 3차 검토(0fb6df2) 후속 수정 검증 기록 — 3차

이 폴더는 2026-09-21 **3차** 후속 수정(R1-a·R1-b·R3-a·R3-b)의 실행 기록이다. 1차
[hmc-response-integrity-followup-20260921](../hmc-response-integrity-followup-20260921/)과 2차
[hmc-response-integrity-followup2-20260921](../hmc-response-integrity-followup2-20260921/) 기록은
각 회차 수정 전후의 증거로 그대로 보존하고 덮어쓰지 않았다. 검토자가 원본 프로젝트의 Git 제외
경로에 남긴 3차 검토 기록(`outputs/reviews/hmc-integrity-third-review-20260921/`)도 읽기만 했다.
결과는 보고서의
[3차 검토 후속 수정](../../HMC_응답서_검증정합성_후속수정결과_2026-09-21.md#3차-검토-후속-수정-r1-ar1-br3-ar3-b--2026-09-21-3차) 절과 함께 읽는다.

- `remaining_probes.json`, `review_probes_console.txt`: 검토자의 추가 probe(`outputs/reviews/hmc-integrity-third-review-20260921/probe_remaining.py`) 원문을 **수정 후 코드**로 다시 돌린 결과. 이 probe는 **정상 동작을 기대하는 검사**라 수정 전에는 14건 중 10건이 실패했고, 지금은 `failed: []`로 14건 전부 통과한다.
- `run_review_probes.py`: 위 파일을 만드는 드라이버. 검토자 원문은 고치지 않고 출력 폴더 상수만 이 회차 폴더로 치환해 실행한다(치환 실패 시 assert로 중단 — 검토자 기록을 덮지 않기 위한 장치).
- `reproductions.json`, `after_fix_checks.json`, `second_review_checks_console.txt`: 2차 검토의 재현 관측값과 정상 동작 기준 **20개**를 3차 수정 후 코드로 다시 확인한 결과. 20/20 통과, `failed: []`.
- `run_second_review_checks.py`: 2차 폴더의 같은 드라이버를 이 회차 폴더로 옮긴 사본.
- `previous_probes/reproductions.json`: 검토자 스크립트가 함께 재생하는 이전 회차 probe 사본. 원본 폴더가 아니라 이 폴더에 떨어진 결과다.
- `full_tests.txt`: 전체 스위트 **1,549 passed / 12 skipped / 경고 5개**. 2차 1,536건에 이번 회귀 13건(parametrize 포함)이 더해진 수다. skip 12건은 변동 없다.
- `followup_tests.txt`: `tests/test_hmc_followup.py` **64 passed**. 이번에 더한 `test_followup_R1a/R1b/R3a/R3b` 13건이 네 결함의 정상 동작을 고정한다.
- `validation_report.json`, `replay_console.txt`: 실제 시연 PDF와 저장 OCR 캐시를 다시 연결한 재생. 실제 자료 검사 75건, 동일 범위 주장 통제 76건, 실패 0건, 음성 대조군 5종 모두 예상대로 거부.
- `ui_apptest.json`, `ui_console.txt`: 같은 ResponseSheet의 production Streamlit 렌더 소비 검사 **15건 통과, 예외 0**. 업로드 전체 UI 자동화나 라이브 LLM 검증이 아니다.
- `run_ui_apptest.py`: `scripts/hmc_integrity_ui_validation.py`가 1차 폴더를 상수로 읽기 때문에, 검증 논리는 그대로 두고 입출력 폴더만 이 회차로 바꿔 실행하는 드라이버.
- `output_manifest.json`: Git 제외 산출물 22개의 절대 경로·sha256·크기.

## 2차 산출물과의 동일성

이번 제품 변경은 정상 측정값 경로를 건드리지 않았음을 재생 산출물로 확인했다. 실제·통제 두 경로
모두에서 2차 대비 `checks` 항목, 실행 수(75/76), `pdf_regions`, `xlsx_rows`가 동일하고
**`response_sheet.json`의 sha256이 동일**하다. 달라지는 것은 기록된 경로와 xlsx·pdf 해시뿐이다
(둘은 경로와 생성 시각을 파일에 담는다). 실제 자료 값도 그대로다 — 0.873988 TJ, 88.397 tCO2eq,
29.3 %, 10.6 %.

## 재생 명령

지정 worktree에서 원본 가상환경 Python으로 실행한다. `PY=/Users/heojeongmin/Documents/Claude/Projects/ESGenie/venv/bin/python`.

```sh
cd /private/tmp/ESGenie-hmc-response-integrity-20260920
$PY -m pytest -q -p no:randomly
$PY -m pytest tests/test_hmc_followup.py -v -p no:randomly
$PY scripts/hmc_integrity_validation.py \
  --evidence-dir /Users/heojeongmin/Documents/Claude/Projects/ESGenie/시연증빙세트_한울정밀공업 \
  --cache-dir /Users/heojeongmin/Documents/Claude/Projects/ESGenie/data/_cache/ocr \
  --out-dir outputs/validation/hmc-response-integrity-followup3-20260921 \
  --log-dir docs/validation/hmc-response-integrity-followup3-20260921
$PY docs/validation/hmc-response-integrity-followup3-20260921/run_ui_apptest.py
$PY docs/validation/hmc-response-integrity-followup3-20260921/run_review_probes.py
$PY -O docs/validation/hmc-response-integrity-followup3-20260921/run_second_review_checks.py
```

검토자의 추가 probe는 정상 동작을 기대하므로 `-O` 없이 그대로 돌린다. 2차 재현 스크립트는 결함이
존재함을 고정하는 assert를 담고 있어 `-O`로 비활성화하고, 정상 기준 20개는 드라이버가 따로 검사한다.

## 검증 성격

- 실제 추출: 시연 PDF 4종의 로컬 텍스트·좌표 → 기존 구조화 파서. `validation_report.json`의 `inputs`에 경로·해시·페이지가 있다.
- 저장 캐시 재생: 재생에너지 현황 등 저장 OCR 응답 재생. 새 OCR 호출이 아니다.
- 합성 대조군: R1-a·R1-b 회귀와 검토자 probe의 측정값 입력은 합성 OCR fixture다. 실제 OCR 실행이라고 부르지 않는다.
- 모킹 LLM: R3-a·R3-b의 초안 생성은 `MagicMock`이 고정 문장을 돌려준다. 게이트와 `_attempt_draft()`는 production 코드 그대로다. **이번 작업에서도 라이브 LLM 호출은 하지 않았다.**
- 수동 재구성: 없다. 1차의 Excel 정정본(`legacy_corrections/`)은 이번 회차에서 다시 만들지 않았다.
- xlsx·pdf 해시는 실행마다 달라진다(경로·생성 시각 포함). 내용 동일성 판단은 `response_sheet.json` 해시로 한다.
