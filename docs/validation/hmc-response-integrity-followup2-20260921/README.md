# 재검토(ba0ea8c) 후속 수정 검증 기록 — 2차

이 폴더는 2026-09-21 **2차** 후속 수정(R1~R4)의 실행 기록이다. 1차 기록
[hmc-response-integrity-followup-20260921](../hmc-response-integrity-followup-20260921/)은
수정 전 상태의 증거로 그대로 보존하고 덮어쓰지 않았다. 결과는 보고서의
[재검토 후속 수정](../../HMC_응답서_검증정합성_후속수정결과_2026-09-21.md#재검토-후속-수정-r1r4--2026-09-21-2차) 절과 함께 읽는다.

- `reproductions.json`: 검토자의 재현 스크립트(`outputs/reviews/hmc-integrity-second-review-20260921/reproduce.py`) 원문을 **수정 후 코드**로 다시 돌린 관측값. 출력 폴더만 이 폴더로 바꿨다.
- `after_fix_checks.json`, `after_fix_console.txt`: 같은 관측값을 정상 동작 기준 20개로 재검증한 결과. 전부 통과.
- `run_review_probes.py`: 위 두 파일을 만드는 드라이버. 검토자 스크립트의 assert는 **결함이 존재함**을 고정하므로 `-O`로 비활성화하고, 정상 기준은 이 파일이 따로 확인한다.
- `previous_probes/reproductions.json`: 검토자 스크립트가 함께 재생하는 1차 검토 probe. 원본 폴더가 아니라 이 폴더에 떨어진 사본이다.
- `full_tests.txt`: 전체 스위트 **1,536 passed / 12 skipped**. 1차 1,515건에 이번 회귀 21건(parametrize 포함)이 더해진 수다. skip 12건은 변동 없다.
- `followup_tests.txt`: `tests/test_hmc_followup.py` **51 passed**. `test_followup_R1~R4` 21건이 결함의 정상 동작을 고정한다.
- `validation_report.json`, `replay_console.txt`: 실제 시연 PDF와 저장 OCR 캐시를 다시 연결한 재생. 실제 자료 검사 75건, 동일 범위 주장 통제 76건, 실패 0건, 음성 대조군 5종 모두 정상 실패.
- `ui_apptest.json`, `ui_console.txt`: 같은 ResponseSheet의 production Streamlit 렌더 소비 검사 15건 통과. 업로드 전체 UI 자동화나 라이브 LLM 검증이 아니다.
- `run_ui_apptest.py`: `scripts/hmc_integrity_ui_validation.py`가 1차 폴더를 상수로 읽기 때문에, 검증 논리는 그대로 두고 입출력 폴더만 이 회차로 바꿔 실행하는 드라이버.
- `output_manifest.json`: Git 제외 산출물 22개의 절대 경로·해시·크기.

## 재생 명령

지정 worktree에서 원본 가상환경 Python으로 실행한다. `PY=/Users/heojeongmin/Documents/Claude/Projects/ESGenie/venv/bin/python`.

```sh
cd /private/tmp/ESGenie-hmc-response-integrity-20260920
$PY -m pytest -q -p no:randomly
$PY -m pytest tests/test_hmc_followup.py -v -p no:randomly
$PY scripts/hmc_integrity_validation.py \
  --evidence-dir /Users/heojeongmin/Documents/Claude/Projects/ESGenie/시연증빙세트_한울정밀공업 \
  --cache-dir /Users/heojeongmin/Documents/Claude/Projects/ESGenie/data/_cache/ocr \
  --out-dir outputs/validation/hmc-response-integrity-followup2-20260921 \
  --log-dir docs/validation/hmc-response-integrity-followup2-20260921
$PY docs/validation/hmc-response-integrity-followup2-20260921/run_ui_apptest.py
$PY -O docs/validation/hmc-response-integrity-followup2-20260921/run_review_probes.py
```

## 검증 성격

- 실제 추출: 시연 PDF 4종의 로컬 텍스트·좌표 → 기존 구조화 파서. `validation_report.json`의 `inputs`에 경로·해시·페이지가 있다.
- 저장 캐시 재생: 재생에너지 현황 등 저장 OCR 응답 재생. 새 OCR 호출이 아니다.
- 합성 대조군: R1~R4 회귀와 검토자 probe의 입력은 합성 OCR fixture다. 실제 OCR 실행이라고 부르지 않는다.
- 모킹 LLM: R3의 초안 생성은 `MagicMock`이 고정 문장을 돌려준다. **이번 작업에서 라이브 LLM 호출은 하지 않았다.**
- 수동 재구성: 없다. 1차의 Excel 정정본(`legacy_corrections/`)은 이번 회차에서 다시 만들지 않았다.
- xlsx·pdf 해시는 실행마다 달라진다(경로·생성 시각 포함). 내용 동일성 판단은 `response_sheet.json` 해시로 한다.
