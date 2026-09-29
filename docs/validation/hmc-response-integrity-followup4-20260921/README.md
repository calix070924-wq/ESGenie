# 4차 검토(6351381) 후속 수정 검증 기록 — 4차

이 폴더는 2026-09-21 **4차** 후속 수정(A·B·C)의 실행 기록이다. 1차
[hmc-response-integrity-followup-20260921](../hmc-response-integrity-followup-20260921/), 2차
[hmc-response-integrity-followup2-20260921](../hmc-response-integrity-followup2-20260921/), 3차
[hmc-response-integrity-followup3-20260921](../hmc-response-integrity-followup3-20260921/) 기록은
각 회차 수정 전후의 증거로 그대로 보존하고 덮어쓰지 않았다. 검토자가 원본 프로젝트의 Git 제외
경로에 남긴 4차 검토 기록(`outputs/reviews/hmc-integrity-fourth-review-20260921/`)도 읽기만 했다.
결과는 보고서의
[4차 검토 후속 수정](../../HMC_응답서_검증정합성_후속수정결과_2026-09-21.md#4차-검토-후속-수정-abc--2026-09-21-4차) 절과 함께 읽는다.

## 이번 회차가 고정한 것

- `combination_results.json`, `review4_probes_console.txt`: 검토자의 조합 probe
  (`outputs/reviews/hmc-integrity-fourth-review-20260921/probe_combinations.py`) 원문을 **수정 후
  코드**로 다시 돌린 결과. 이 probe는 **정상 동작을 기대하는 검사**라 수정 전에는 13건 중 7건이
  실패했고(A 4건·B 2건·C 1건), 지금은 `failed: []`로 13건 전부 통과한다.
- `run_review4_probes.py`: 위 파일을 만드는 드라이버. 검토자 원문은 고치지 않고 출력 폴더 상수만
  이 회차 폴더로 치환해 실행한다(치환 실패 시 assert로 중단 — 검토자 기록을 덮지 않기 위한 장치).
- `negative_control_tests.txt`: 새로 넣은 회귀 12건을 **수정 전 제품 코드**로 실행한 결과.
  6건 실패(A의 MWh 대표 3건·B 1건·C 2건), 6건은 정상 대조군으로 통과한다. 즉 새 검사가 실제로
  세 결함을 잡는다.
- `remaining_probes.json`, `review_probes_console.txt`: 3차 검토 probe **14건**을 이번 수정 후
  다시 확인한 결과. 14/14 통과, `failed: []`.
- `reproductions.json`, `after_fix_checks.json`, `second_review_checks_console.txt`: 2차 검토의
  재현 관측값과 정상 동작 기준 **20개**를 이번 수정 후 다시 확인한 결과. 20/20 통과, `failed: []`.
- `run_review_probes.py`, `run_second_review_checks.py`: 위 두 검사의 드라이버(3차 폴더 사본,
  출력 폴더만 이 회차로 교체).
- `previous_probes/reproductions.json`: 검토자 스크립트가 함께 재생하는 이전 회차 probe 사본.
- `full_tests.txt`: 전체 스위트 **1,561 passed / 12 skipped / 경고 5개**. 3차 1,549건에 이번
  회귀 12건(parametrize 포함)이 더해진 수다. skip 12건은 변동 없다(실제 보고서 PDF가 없는
  LG화학 5개·현대모비스 7개).
- `followup_tests.txt`: `tests/test_hmc_followup.py` **76 passed**. 이번에 더한
  `test_review4_A/B/C` 12건이 세 결함의 정상 동작을 고정한다.
- `validation_report.json`, `replay_console.txt`: 실제 시연 PDF와 저장 OCR 캐시를 다시 연결한
  재생. 실제 자료 검사 75건, 동일 범위 주장 통제 76건, 실패 0건, 음성 대조군 5종
  (필수 문항 제거·범위 삭제·값 변경·잘못된 페이지·체크리스트 누락) 모두 예상대로 거부.
- `ui_apptest.json`, `ui_console.txt`, `run_ui_apptest.py`: 같은 ResponseSheet의 production
  Streamlit 렌더 소비 검사 **15건 통과, 예외 0**. 업로드 전체 UI 자동화나 라이브 LLM 검증이 아니다.
- `output_manifest.json`: Git 제외 산출물 22개의 절대 경로·sha256·크기.
- `preservation.json`: 원본 폴더·이전 회차 기록·이전 회차 산출물 보존 대조.

## 3차 산출물과의 동일성

이번 제품 변경은 실제 자료의 정상 경로를 건드리지 않았음을 재생 산출물로 확인했다. 실제·통제 두
경로 모두에서 3차 대비 `checks` 항목, 실행 수(75/76), `pdf_regions`, `xlsx_rows`가 동일하고
**`response_sheet.json`의 sha256이 동일**하다(실제 `11fdb408…`, 통제 `7bed3700…` — 1차부터 네
회차 모두 같다). `pipeline.json`과 렌더한 PDF 15쪽 PNG 해시도 3차와 같다. 달라지는 것은 기록된
경로와 xlsx·pdf 해시뿐이다(둘은 경로와 생성 시각을 파일에 담는다). 실제 자료 값도 그대로다 —
0.873988 TJ, 88.397 tCO2eq, 29.3 %, 10.6 %.

## 보존 대조(`preservation.json`)

| 항목 | 결과 |
|---|---|
| 원본 폴더 HEAD / 미커밋 / stash | `fa81817` / 28항목 / 4개 — 4차 검토 기준과 동일 |
| 원본 증빙 PDF·저장 OCR 캐시 | 53개·859개, 읽기만 함 |
| 1·2·3차 검증 기록 문서 | 이번 회차 변경 0건 |
| 1·2·3차 Git 제외 산출물 | manifest 36·22·22개 해시 전부 일치 |
| 작업 worktree | 브랜치 `codex/fix-hmc-response-integrity-20260920`, main 대비 22개 커밋 위에 이번 수정 |

## 재생 명령

지정 worktree에서 원본 가상환경 Python으로 실행한다. `PY=/Users/heojeongmin/Documents/Claude/Projects/ESGenie/venv/bin/python`.

```sh
cd /private/tmp/ESGenie-hmc-response-integrity-20260920
$PY -m pytest -q -p no:randomly
$PY -m pytest tests/test_hmc_followup.py -v -p no:randomly
$PY scripts/hmc_integrity_validation.py \
  --evidence-dir /Users/heojeongmin/Documents/Claude/Projects/ESGenie/시연증빙세트_한울정밀공업 \
  --cache-dir /Users/heojeongmin/Documents/Claude/Projects/ESGenie/data/_cache/ocr \
  --out-dir outputs/validation/hmc-response-integrity-followup4-20260921 \
  --log-dir docs/validation/hmc-response-integrity-followup4-20260921
$PY docs/validation/hmc-response-integrity-followup4-20260921/run_ui_apptest.py
$PY docs/validation/hmc-response-integrity-followup4-20260921/run_review4_probes.py
$PY docs/validation/hmc-response-integrity-followup4-20260921/run_review_probes.py
$PY -O docs/validation/hmc-response-integrity-followup4-20260921/run_second_review_checks.py
```

검토자의 조합 probe와 3차 probe는 정상 동작을 기대하므로 `-O` 없이 그대로 돌린다. 2차 재현
스크립트는 결함이 존재함을 고정하는 assert를 담고 있어 `-O`로 비활성화하고, 정상 기준 20개는
드라이버가 따로 검사한다. 음성 대조군 기록은 제품 파일 3개를 `git stash`로 되돌린 상태에서
`-k review4`만 실행해 만들었다.

## 검증 성격

- 실제 추출: 시연 PDF 4종의 로컬 텍스트·좌표 → 기존 구조화 파서. `validation_report.json`의
  `inputs`에 경로·해시·페이지가 있다.
- 저장 캐시 재생: 재생에너지 현황 등 저장 OCR 응답 재생. 새 OCR 호출이 아니다.
- 합성 대조군: `test_review4_A/B`의 측정값 입력과 검토자 조합 probe는 합성 OCR fixture다.
  실제 OCR 실행이라고 부르지 않는다.
- 모킹 LLM: C의 초안 생성은 `MagicMock`이 고정 문장을 돌려준다. `evaluate_grounding()`과
  `_attempt_draft()`는 production 코드 그대로다. **이번 회차에도 라이브 LLM 호출은 하지 않았다.**
- 수동 재구성: 없다. 1차의 Excel 정정본(`legacy_corrections/`)은 이번 회차에서 다시 만들지 않았다.
- 전 페이지 이미지 검수: 제품 렌더러 변경이 없고 PDF 15쪽 PNG 해시가 3차와 같아 반복하지 않았다.
- xlsx·pdf 해시는 실행마다 달라진다(경로·생성 시각 포함). 내용 동일성 판단은
  `response_sheet.json` 해시로 한다.
