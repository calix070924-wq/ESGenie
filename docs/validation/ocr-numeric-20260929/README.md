# 수치 인식 오류 수정·검증 결과 (2026-09-29)

작업지시서: `docs/작업지시서_수치인식오류_별도브랜치_수정검증_2026-09-29.md`
브랜치: `codex/fix-ocr-numeric-extraction-20260929` (main `387999b`에서 분기)

## 0. 요약

| 구분 | 상태 | 근거 |
|---|---|---|
| 코어 수정 | 필수 수치 6개·오인식 3건·위치 정밀도 표시 통과, 회귀 테스트 통과 | §3, §4 |
| 실제 분석 확인(코어 단독) | 12건·13건 모두 판정 12개 통과 | §5.1 |
| 웹 통합 확인(PR #65 + 코어 수정, 임시 통합) | 화면·Excel·PDF 값·단위·상태·출처 일치 | §5.2 |
| **PR 검토 보완 R1~R5** (`89f34aa`) | R1~R5 수정·재현·테스트·실제 분석·HMC·새 웹 통합(`cd66424`) 확인 완료. 남은 한계는 §10.7 | §10 |

가상 증빙의 검증 항목 **29개 중 통과 27개, 미통과 0개, 미검증 2개**(§7, 최초 수정 `1dea0c5` 기준 기록 — 검토 보완
후에도 그대로 둔다). 검토 보완의 테스트 수·검증은 §10에 따로 적었다(최초 집계와 섞지 않음). 미검증 2개는 작업지시서 필수
항목 밖이다. 이 결과는 BM 개편 가상 증빙 1세트와 검토용 가상 변형 입력의 수치 인식 범위에 한정되며, 전체 분석 제품이나
시연 준비가 완료됐다는 뜻이 아니다.

## 1. 커밋

| SHA | 내용 |
|---|---|
| `ec0bb4e` | fix(ocr): 고지서·위탁명세 표 셀에서 사용량·총량을 머리글·단위와 함께 읽기 (+ 회귀 53개, 재생 픽스처) |
| `688445a` | chore(rehearsal): 코드 경로·저장 경로를 명시적으로 받는 수치 재검증 도구 |
| `1dea0c5` | fix(ocr): 본문에서 고정한 비율(%)의 위치 정밀도를 사실대로 남기기 (+ 회귀 4개) |
| `08f1b27` | chore(rehearsal): 재검증 도구에 완료 상태·이어 받기·화면 확인용 작업 공간 옵션 |

`1dea0c5`는 `ec0bb4e` 실제 분석에서 29.3%의 위치가 문단 전체 bbox이면서 정밀도 표시가 없던 것을
발견하고 추가한 수정이다(작업지시서 4.3 “행·표 단위 위치임을 표시”).

### 원인

- Upstage가 복원한 표 셀(`ExtractedTable`)이 수치 추출에 전혀 쓰이지 않았다(02·03·07 수치 0개, 04 총량·재활용량 누락).
- 템플릿 키워드가 `당월 전력 사용량`처럼 띄어 쓴 머리글을 놓쳤고, 가스 MJ·수도 ton 기본 단위를 원문 확인 없이 붙였다.
- 인접 숫자 폴백이 제목 번호(`3. 현장 안전과 교육`)까지 값으로 받아 재활용량 3 ton이 생겼다.
- 라우팅이 `폐기물`·`인계` 키워드만으로 회사 소개(01)·규정(06)을 폐기물 대장으로 보냈다.
- 본문 비율 고정(`_pin_rates_from_raw`)이 값을 품은 텍스트 요소 전체 bbox만 붙이고 정밀도를 남기지 않았다.

## 2. 수정 전후 비교표

- **수정 전(기존 리허설)**: 2026-09-28 리허설, PR #65 코드 `6bf9750` + Upstage. 수정 대상 코어 파일 4개는
  `6bf9750`과 main `387999b`가 동일하다(`git diff 387999b 6bf9750 -- <4개 파일>` 빈 출력) — 즉 최신 main의 Upstage 경로 결과와 같다.
- **수정 전(main, pymupdf 경로)**: `387999b`, 모의 모드 규칙 경로 `logs/before_pymupdf_path_snapshot.json`.
- **수정 후**: `1dea0c5` 실제 분석(`live/core_1dea0c5_initial_summary.json`).
- 위치 정밀도: `pdf_text` = OCR 표/텍스트 요소 bbox 안에서 PDF 문자 좌표로 좁힌 값 글자 위치. 모든 페이지는 내부 0 = 화면 “1쪽”.

| 문서 | 지표 | 원문 정답 | 수정 전(기존 리허설) | 수정 전(main pymupdf) | 수정 후 | 원 단위·변환값 | 기간·사업장 | 페이지·위치 정밀도 | 통과 |
|---|---|---|---|---|---|---|---|---|---|
| 02 전기 | 사용량 | 142,560 kWh (=(50,586−48,210)×60) | 추출 0개 | 청구금액 25,938,000 원만 | 142,560 kWh, E-4-1, 지침 검산 match | kWh 그대로 → 답변 E-4-1 0.513216 TJ(×3.6 MJ/kWh), E-3-1 68.158 tCO2eq(기존 전력 계수) | 2026-04-01~30·월간·제1공장 | 1쪽, pdf_text(당월 사용량 칸, 전월 지침 칸 아님) | 통과 |
| 03 가스 | 사용량 | 8,420 m³ (=40,000−31,580) | 추출 0개 | 31,580 MJ·E-4-1(전월 지침+가짜 단위) | 8,420 m³, 코드 없음, 지침 검산 match | 원문 `m3`→m³. MJ·TJ·배출량 미생성 | 2026-04-01~30·월간·제1공장 도장·건조라인 | 1쪽, pdf_text(숫자만, 단위 글자는 bbox 밖) | 통과 |
| 07 수도 | 사용량 | 680 m³ (=13,000−12,320) | 추출 0개 | 12,320 ton·E-5-1(전월 지침+가짜 단위) | 680 m³, E-5-1, 지침 검산 match | m³ | 2026-04-01~30·월간·제1공장 | 1쪽, pdf_text | 통과 |
| 04 폐기물 | 총 위탁량 | 18,400 kg | 누락 | 누락 | 18,400 kg, E-6-1 | kg → 답변 18.4 톤 | 2026-04-01~30·월간·제1공장 | 1쪽, pdf_text | 통과 |
| 04 폐기물 | 재활용량 | 5,400 kg | 누락 | 누락 | 5,400 kg, 코드 없음 | kg | 동일 | 1쪽, pdf_text | 통과 |
| 04 폐기물 | 재활용률 | 29.3% (5,400÷18,400=29.3478%) | 29.3%, 정밀도 없음(문단 bbox) | 29.3% | 29.3%, E-6-2, 반올림 검산 match·구성 합계·명세 합계·재활용 명세 합계 match | % | 동일 | 1쪽, pdf_text(`29.3%` 글자, 근거 출처 text_span) | 통과 |
| 06 규정 | 수량 없음 | 수량 생성 금지 | 재활용량 3 ton·E-6-2, waste_ledger | 3 ton, waste_ledger | 수치 0개, policy_manual | — | — | — | 통과 |
| 01 회사 현황 | 대장 오분류 금지 | waste_ledger 아님 | waste_ledger | waste_ledger | ambiguous_fallback_vlm | — | — | — | 통과 |
| 08 스크랩 | 내부 재투입률 분리 | 92%는 E-6-2가 아님 | 92% (E-2-2 추정) | — | 92% (E-2-2 추정), E-6-2 답변은 04의 29.3% | kg | 2026-04 월간 | bbox 없음(VLM) | 통과 |

이전 세트(`시연증빙세트_한울정밀공업`, 별도 회귀 입력): 재활용량 5,400 **ton → kg**(단위 오류 수정), 가스
`8,420 m³`가 새로 추출됐고 기존 `360,772 MJ`(E-4-1)·전력 142,560 kWh·총 위탁량 18.4 ton은 그대로다
(`test_old_set_values_are_preserved`). 360,772 MJ를 이번 세트의 가스 정답으로 옮기지 않았다.

### 이미지

- `bbox/{02,03,04,06,07}_bbox_before_after.png`: 빨강 = 기존 리허설, 초록 = `1dea0c5` 실제 분석. 원본 PDF 위에 표시.
  04는 기존 29.3% 문단 bbox(빨강)와 수정 후 `29.3%` 글자 bbox(초록)가 구분된다. 06은 수정 전 3 ton, 수정 후 추출 없음.
- 웹 화면 원문 강조: `live/ui_screens/E-4-1_source_page.png`(142,560 칸), `live/ui_screens/E-6-1_source_page.png`,
  `live/ui_screens_d92732b/E-6-2_source_page.png`(`29.3%` 글자만 강조).

## 3. 테스트

| 실행 | 코드 | 결과 | 기록 |
|---|---|---|---|
| 기준선 전체 | `387999b` | 1590 passed, 12 skipped | `logs/baseline_full_pytest.txt` |
| 새 회귀(최초 50개) 수정 전 | `387999b` 제품 코드 | 40 failed, 10 passed | `logs/new_tests_before_fix.txt` |
| 새 회귀 53개 수정 전 | `387999b` | 42 failed, 11 passed | `logs/new_tests_final53_on_main_387999b.txt` |
| 새 회귀 57개 비율 수정 전 | `ec0bb4e` | 3 failed, 54 passed | `logs/rate_precision_tests_before_fix_ec0bb4e.txt` |
| 새 회귀 57개 | `1dea0c5` | 57 passed | (전체 테스트에 포함) |
| 관련 15개 파일(OCR·표·좌표·단위·기간·근거) | `1dea0c5` | 282 passed, 12 skipped | `logs/related_tests_final.txt` |
| 전체 | `1dea0c5` | 1647 passed, 12 skipped | `logs/final_full_pytest.txt` |
| 웹 통합 전체 | `d92732b` | 1661 passed, 12 skipped | `logs/web_integ_d92732b_full_pytest.txt` |

- main에서 이미 통과한 11개는 기존 동작 보존 확인용이다: 02·03·04·07 라우팅 유지(4), 배율 누락 시 미가정,
  금액·세금·날짜 잡음, 명세 행만으로 총량 미생성, kepco 제목 번호, 긴 대장 본문 라우팅(2), 01 재생 결과 없음.
- 건너뜀 12개: `tests/test_table_structure.py` — 실보고서 PDF(`data/real_reports/*.pdf`)가 작업 폴더에 없음. 기준선과 같다.
- `ec0bb4e` 시점 전체 실행은 1643 passed, 12 skipped였다(같은 로그 파일을 최종 실행으로 덮어씀).
- 재생 픽스처(`tests/fixtures/ocr_numeric_hanwool_bm/*.json`)의 입력은 리허설 `pipeline.json`에 남은 **Upstage
  Document Parse 단계 출력**(요소 text·HTML 표 셀)이다. 리허설이 원시 응답을 저장하지 않아 표 밖 요소의 bbox는 없다.
- 변형 입력(유형별 3개 이상, 테스트 함수 기준): 전기 10, 가스 5, 수도 4, 폐기물 5 + 제목 번호·라우팅·단위·표 상충 공통 테스트. 함수 48개 중 일부가 매개변수화되어 57개로 실행된다.
- 판정의 부정 항목 6개(03 MJ/TJ/GJ, 06 3, 04 18.4 ton, 01 waste_ledger)는 `found: false`가 통과다.
- HMC 회귀(`scripts/hmc_integrity_validation.py`, 모의 LLM, 기존 OCR 캐시 읽기만): `hmc/{before,after,final}/validation_report.json`
  모두 `failed: []`(actual 75·controlled 76 검사). 답변 수치 5개의 값·단위·D1 상태(`unavailable`)는 불변. 달라진 것:
  그래프에 가스 8,420 m³ 노드 추가, 재활용량 5,400 ton→kg, E-3-1 신뢰도 0.874→0.855·E-4-1 0.92→0.9(표 셀 산출물 신뢰도 0.9 채택),
  E-3-1 범위 표시의 사업장·항목 나열 순서 변경(구성 요소는 같음). 모두 `ec0bb4e`에서 생겼고 `1dea0c5`는 29.3% 근거의 위치 정밀도만 바꿨다.

## 4. 수정 내용

- `esgenie/ssot/ocr_table_metrics.py`(신규): Upstage 표 셀·마크다운 표·좌표 줄을 격자로 복원 → 머리글 역할
  (사용량·지침·배율·총량·재활용)로 칸 선택. 단위는 셀 → 머리글 괄호에서만 정하고, 없으면 값을 만들지 않고 검토로 남김.
  명시 사용량을 (당월−이전)×배율로 검산, 불일치 시 두 값 보존 + HITL. 명시값이 없으면 입력이 모두 있을 때만 계산
  (전기 배율 필수, 가스 환산 계수 없음). 폐기물 구성 합계·명세 합계·재활용률 반올림 검산. 표/텍스트 요소 bbox를 PDF 문자 좌표로 좁힘.
- `esgenie/ssot/ocr_router.py`: 표 산출물 우선 병합, 제목 번호·비값 이웃 차단, 긴 본문에 수량 단위가 없으면 정형 유형 제외,
  backfill은 단위가 항목 정의와 다르면 코드 미부착(가스 m³→E-4-1 금지), 본문 비율 고정에 `text_block` 정밀도.
- `esgenie/ssot/evidence_graph.py`: 표 셀 근거를 경계 provenance(`table_cell`, 본문 비율은 `text_span`)로 연결.
- `esgenie/rag_gates/units.py`: `m3·m³·㎥` 부피 별칭(질량·에너지와 비호환), `㎏`.
- 회사명·파일명·정답 수치·해시 분기 없음. D1/D6/HMC 임계값·승인 상태, 원본 PDF, 모델 변경 없음.

## 5. 실제 분석·웹 통합 검증

입력: `output/pdf/한울정밀_촬영세트_BM개편_20260928` 최초 12건 + 보완 1건(13_). 05만 회사 답변, 00_ 요청서 제외.
모든 실행에서 입력 sha256이 구성 목록(`구성_검산_목록.json`, version 2026-09-28)과 일치했다(`environment.json`의 `inputs`).
기존 리허설 기록은 해시 대신 크기만 남겨, 12·13건 모두 크기가 현재 입력과 같음을 확인했다.
모델 gpt-4.1-mini(Azure OpenAI), OCR Upstage Document Parse, `force_mock=False`, `strict_llm=True`. 비밀키는 설정 여부만 기록.

### 5.1 코어 단독 (`scripts/live_numeric_rehearsal.py core`, 웹 계층 없음)

| 실행 | 코드 | 캐시 | 시간 | LLM 실호출/성공/실패 | LLM 캐시 적중 | VLM 캐시 적중/누락 | Upstage 요청/성공/실패 | 판정 |
|---|---|---|---|---|---|---|---|---|
| 최초 12건 | `688445a`(=`ec0bb4e` 제품 코드) | 새 빈 캐시 | 135.4 s | 13/13/0 | 0 | 0/7 | 4/4/0 | 12/12 |
| 보완 13건 | `688445a` | 위 캐시 이어 씀 | 37.1 s | 3/3/0 | 4 | 7/1 | 4/4/0 | 12/12 |
| 최초 12건 | `08f1b27`(=`1dea0c5` 제품 코드) | 위 캐시 사본 | 49.1 s | 0 | 6 | 7/0 | 4/4/0 | 12/12 |

- `1dea0c5` 재실행은 LLM·VLM 입력이 같아 캐시로 재생했고(유료 반복 방지), 수정이 영향을 주는 Upstage OCR 단계만 실제 호출했다.
  캐시 적중 자체가 LLM 입력이 바뀌지 않았다는 근거다.
- 판정 12개(`check_live_outputs.py`): 필수 값 6개 존재 + 03 MJ/TJ/GJ 없음 + 06 3 ton 없음 + 04 18.4 ton 없음 + 01 waste_ledger 아님.
- 답변(rba42): E-4-1 0.513216 TJ, E-3-1 68.158 tCO2eq, E-6-1 18.4 톤, E-6-2 29.3%. 모두 `self_reported`, 범위 `2026-04-01~2026-04-30 · 월간 · 제1공장 · … · 부분`,
  범위 확인 필요 — 연간·전사로 확대하지 않았다. E-4-1은 전력만(가스는 환산 계수가 없어 미포함). E-6-2의 92%는 05 회사 답변(`self_reports`)으로 따로 표시된다.
- 기록: `live/core_run*/{initial,followup}/{environment,run_stats}.json`, `live/core_*_summary.json`, `logs/live_core_*_console.txt`.

### 5.2 웹 통합 (임시 통합 폴더 `/private/tmp/ESGenie-web-integ-20260929`)

PR #65가 main에 미병합이라 PR #65 확인 커밋 `6bf9750`에 코어 수정을 cherry-pick한 임시 폴더를 썼다. PR #65 원본 브랜치·작업 폴더는
변경하지 않았다(`6bf9750`, 미커밋 0줄). 프런트엔드는 임시 폴더에서 빌드(PR #65의 node_modules 사본 사용).

| 실행 | 통합 SHA | 시간 | LLM 실호출/성공/실패 | VLM 적중/누락 | Upstage 요청/성공/실패 | 판정 |
|---|---|---|---|---|---|---|
| rev13 (12건) | `fe811b2` = `6bf9750`+`ec0bb4e` | 45.0 s | 4/4/0 | 7/0 | 4/4/0 | 12/12 |
| rev14 (13건) | `fe811b2` | 9.5 s | 0 | 8/0 | 4/4/0 | 12/12 |
| rev13 (12건) | `d92732b` = `fe811b2`+`1dea0c5` | 37.9 s | 6/6/0 | 7/0 | 4/4/0 | 12/12 |
| rev14 (13건) | `d92732b` | 6.7 s | 0 | 8/0 | 4/4/0 | 12/12 |

- LLM 프롬프트에 프로젝트 ID가 들어가 새 프로젝트마다 일부 LLM 호출이 새로 발생했다(코어 캐시 사본 사용).
- 화면: E-4-1·E-3-1·E-6-1·E-6-2 응답 초안의 값·단위·`확인 필요`·범위 문구가 결과와 같고, 출처는 02/04 “1쪽”.
  원본 페이지 보기에서 142,560 칸, 18,400 kg 칸, `29.3%` 글자가 강조된다. E-6-2는 회사 답변 92%와 자료 값 29.3%를 나란히 표시.
- Excel(`응답서` 시트)·번들 PDF(`실사응답서_rba42_…pdf`)가 같은 값·단위·상태·범위를 담는다(`live/web_drive*/downloads/*/report_text.txt`).
- 보완 업로드 직후 이전 초안 내려받기는 409로 막혔다(`followup_stale_download_check.json`).
- 번들 evidence_pack에는 답변이 인용한 파일만 들어가 03·05·07은 빠진다(기존 동작).
- 기록: `live/web_server*/environment.json`, `live/web_server*/runs/*/{run_stats,summary}.json`, `live/web_drive*/`, `live/ui_screens*/`, `logs/web_*`.
  `web_drive_console.txt` 끝의 “최초 분석 실패”는 도구가 완료 상태를 `done`만 기다린 오류(실제 `complete`)이며, `08f1b27`에서 고치고
  `--resume-project-id`로 같은 프로젝트를 재분석 없이 이어 받았다.

## 6. 보존 확인

- 원본 PDF(`output/pdf/한울정밀_촬영세트_BM개편_20260928`), 기존 리허설(`output/rehearsal`), 기존 OCR 캐시(`data/_cache`),
  이전 세트(`시연증빙세트_한울정밀공업`): 오늘 수정된 파일 0개. 구성 목록 14개 파일 해시 불일치 0개.
- 루트 작업 폴더: main `387999b`, 기존 미커밋 3개(`app.py`, `docs/…v2.md`, `esgenie/ui/tabs.py`) 그대로. 기존 리허설 도구는 실행하지 않았다.
- 새 캐시·결과는 이 폴더에만 저장. 캐시·작업 공간·zip(원본 사본 포함)·원시 실행 덤프·HMC 렌더는 `.gitignore`로 커밋 제외(로컬 보존).

## 7. 검증 항목 집계

| # | 항목 | 결과 |
|---|---|---|
| 1–4 | 전기 142,560 kWh / 가스 8,420 m³ / 가스 MJ·TJ·배출량 미생성 / 수도 680 m³ | 통과 |
| 5–8 | 폐기물 18,400 kg / 5,400 kg / 29.3% 반올림 정합 / 08 내부 재투입률과 분리 | 통과 |
| 9–10 | 06 제목 번호 3톤 미생성 / 01 대장 오분류 없음 | 통과 |
| 11–14 | 월간 기간 보존·연간 미확대 / 제1공장 보존·전사 미확대 / 원 단위·변환 분리 / 전기 지침 검산 | 통과 |
| 15–18 | 표 값 5개 문자 좌표 위치 / 29.3% 위치 정밀도 사실 표시 / 채택 위치가 지침·금액 아님(이미지) / 페이지 내부·표시 일치 | 통과 |
| 19–22 | 새 회귀 수정 전 실패·후 통과 / 유형별 변형 3개 이상 / 관련·전체 테스트 / HMC 값·경계 불변 | 통과 |
| 23–24 | 수정 코드 import 환경의 실제 AI 분석(12건) / 13건 보완 재분석(캐시 재사용·새 결과 반영) | 통과 |
| 25–27 | 화면·Excel·PDF 일치 / 보완 후 이전 초안 차단 / 원본·기존 캐시·기존 결과 불변 | 통과 |
| 28 | Upstage 원시 응답(표 밖 요소 bbox 포함) 오프라인 재생 — 리허설이 원시 응답을 저장하지 않음 | 미검증 |
| 29 | rba42 외 프레임워크(HMC)의 BM 세트 실제 분석 — HMC는 이전 세트 오프라인 회귀만 수행 | 미검증 |

**29개 중 통과 27개, 미통과 0개, 미검증 2개.** 28·29는 작업지시서 필수 항목 밖이다.

## 8. 알려진 한계와 후속 과제

- 03 가스 bbox는 숫자 `8,420`만 가리키고 단위 글자 `m3`는 포함하지 않는다(값 위치로는 정확).
- pymupdf(Upstage 없음) 경로의 29.3%는 줄 bbox + `text_block` 정밀도로 남는다. PDF 문자 좌표 축소는 Upstage 경로에만 적용.
- 02 템플릿의 `청구금액 원` 지표가 남아 있다(코드 없음, 사용량으로 쓰이지 않음).
- 화면은 위치 정밀도 문구를 표시하지 않는다(강조 위치만 표시). 화면 재설계는 범위 밖.
- 01이 폐기물 대장에서 빠지면서 VLM이 인원 수치를 추출한다. 근거 그래프에서 전사 `총 인원 73명`에 사업장 `제1공장`이
  붙는 경계 추론이 관찰됐다(답변 수치로는 쓰이지 않음, 기존 경계 규칙). 인사 지표 추출은 범위 밖 — 별도 확인 필요.
- 05 회사 답변 범위 파서·충돌 설명 개선, 08 VLM 경로의 코드 추정(E-2-2)은 범위 밖.
- Upstage 응답은 캐시되지 않아 실행마다 재요청된다(이번 검증 총 28회, 실패 0).
- `ocr_consistency`의 waste.method_split·recycle_rate·disposal_complement 규칙이 `skipped 입력값 추출 실패`로 남는다(수정 전부터).
- 작업지시서 C(고지서 OCR 정렬)와 겹치는 파일: `ocr_router.py`의 본문 고정 규칙, 위치 정밀도, 단위 처리. 병합 순서 조율 필요.
- 실제 분석은 rba42 프레임워크만 실행했다.

## 9. 재현

```bash
# 코어 단독 실제 분석(새 빈 저장 경로 필요 — 기존 리허설 경로·비어 있지 않은 경로는 거부)
python scripts/live_numeric_rehearsal.py core --stage initial --code-path <코어 작업 폴더> \
  --env-file <.env> --cache-dir <새 캐시> --run-dir <새 결과> --pack-dir output/pdf/한울정밀_촬영세트_BM개편_20260928
# 판정 요약
python docs/validation/ocr-numeric-20260929/check_live_outputs.py <결과>/initial --json <요약.json>
# bbox 전후 이미지
python docs/validation/ocr-numeric-20260929/render_bbox_overlays.py <수정 전 pipeline.json> <수정 후 pipeline.json> <pack> <출력>
# 규칙 경로 스냅샷(작업 폴더의 esgenie 사용)
ESGENIE_FORCE_MOCK=1 python docs/validation/ocr-numeric-20260929/snapshot_pymupdf_path.py <출력.json>
```

## 10. PR 검토 보완 R1~R5 (2026-09-29)

작업지시서: `docs/작업지시서_PR68_검토보완_5건_2026-09-29.md`. 같은 브랜치·같은 Draft PR #68에 후속 커밋으로 추가(기존 커밋 재작성·되돌림 없음).
기록은 모두 `review-r1/`에 있다(기존 로그·결과 덮어쓰지 않음). 이 절의 수치는 §3·§7의 최초 집계와 별개다.

### 10.1 커밋

| SHA | 내용 |
|---|---|
| `89f34aa` | fix(ocr): R1~R5 수정 + 회귀 46개(`tests/test_pr68_review_r1_r5.py`) + 재현 픽스처(`tests/fixtures/ocr_numeric_review_r1/review_cases.json`), 기존 테스트 1개 기대값 갱신 |
| (이 문서 커밋) | docs: 검토 보완 검증 기록(`review-r1/`)과 이 절 |

웹 통합(임시): `/private/tmp/ESGenie-web-integ-r1-20260929` = PR #65 `6bf9750` + cherry-pick `ec0bb4e`→`ea5aa6f`, `1dea0c5`→`7dd7f01`, `89f34aa`→**`cd66424`**.
이전 통합 `d92732b`·이전 지표는 수정 후 검증으로 쓰지 않았다(비교 기준으로만 인용).

### 10.2 R1~R5

재현 스크립트: `review-r1/before/repro.py`(검토 파일의 변형 표, 외부 API 없음). 수정 전 `50b8d72` → `before/repro_on_50b8d72.json`,
수정 후 `89f34aa` → `after/repro_89f34aa.json`, 요약 대조 `after/repro_before_after.json`.

| ID | 수정 전 재현 | 원인·수정 | 추가 테스트 | 최종 답변·출력 확인 | 결과·근거 경로 |
|---|---|---|---|---|---|
| R1 지침 단위 | 전월 1 MWh→당월 2 MWh, 사용량(kWh) 칸 비어 있음 → **1 kWh**로 계산. 명시 1 kWh를 **match**로 판정. 1,000 kWh→2 MWh 혼합 → `index_decreased`로 계산 보류 | 지침 차를 단위 없이 뺐다. `_compute_from_index`가 지침 칸 단위를 읽어 이전 지침을 당월 단위로, 결과를 사용량 칸 단위로 환산(`computed_unit`·`input_units`·`conversion` 기록). 지침 단위 없음 → `index_unit_missing`, 비호환(kWh↔m3) → `index_unit_incompatible`로 보류. 배율 없음은 계속 보류(값을 채우지 않음) | R1 9개: MWh→1,000 kWh, 명시 1 → mismatch / 1,000 → match, 혼합 단위 감소 아님(1 MWh), 단위 없음·비호환 보류, 가스 m³ 지침이 MJ가 되지 않음, 배율 미보충, 사용량 0 | 수정 후 재현: 1,000 kWh(computed_only), 명시 1 kWh → mismatch + 검토 노트, 혼합 → 1.0 MWh. BM 실제 분석 값 불변(142,560 kWh, `computed_unit` kWh) | `after/repro_before_after.json`, `logs/new_tests_*.txt`, §10.4 |
| R2 검산 불일치 전파 | 명시 150,000 kWh ≠ 지침 계산 142,560 kWh는 OCR 검토 목록(`explicit_vs_index_mismatch`)에만 있고, DataPoint는 `estimated`·`scope_unconfirmed`, 답변은 `self_reported` — 불일치가 답변·출력에 보이지 않음(`before/mismatch_answer.json`) | OCR 검토 결과가 근거 그래프 경계로 넘어가지 않았다. `merge_ocr_extraction`이 불일치 노트(파일·쪽, 두 값, 산식, 사유)를 `Boundary.review_notes`에 붙이고, 파생 배출량(E-3-1)이 이를 물려받는다. `finalize_ledger`가 노트를 범위 노트로 옮기며 `source_conflict` 플래그 → 기존 비교 경로에서 `mismatch`·`unverified` | R2 5개: 답변에 두 값·산식·사유, 파생 배출량 상속, 일치 값은 경고 없음, 무관 문서(수도·폐기물) 미표시, Excel·PDF 표시 | 가상 변형(02 사용량 칸만 150,000) 웹 분석: E-4-1 0.54 TJ·E-3-1 71.715 tCO2eq 모두 `실제 불일치`, 화면 상세·Excel·PDF에 `150,000 kWh ≠ 142,560 kWh [(50,586 − 48,210) × 배율 60]`, 신뢰 정보 `source_conflict` | `live/web_variant_R2_cd66424/`, `live/ui_screens_cd66424/R2/`, `variant_inputs/` |
| R3 가스 MJ | 머리글 `사용량(MJ)`의 360,772 → **추출 0개**(가스 사용량 역할이 m³만 허용) | 사용량 칸 단위가 열량(MJ·GJ·TJ)이면 `heat` 역할로 읽는다(`_unit_role`: 머리글·셀·키-값 공통). 칸이 실제 수량으로 읽힐 때만 템플릿 역할을 차지. 허용 밖 단위는 `unit_not_allowed_for_role` 검토 | R3 6개: 머리글 MJ·셀 MJ → 360,772 MJ E-4-1, m³는 부피로 유지(코드 없음), 단위 없는 값 미생성, 빈 칸이 역할을 차지하지 않음, 부피+열량 동시 기재 시 이중 합산 없음(E-4-1 = 0.360772 TJ) | BM 03 가스는 8,420 m³·코드 없음·MJ/TJ/GJ 없음(판정 12개 불변). m³→MJ 환산·발열량 보충 없음 | `after/repro_before_after.json`, §10.4 판정 |
| R4 행 라벨·중복 | 사업장별 한 행 표 2개(김해 제1공장·양산 제2공장 각 1,000 kWh) → **1개로 합쳐짐**(행 라벨 버림, 값·단위만으로 중복 제거). 4월·5월도 1개. 행 라벨을 살린 뒤에는 개발 중 E-4-1이 5월·제2공장, E-3-1이 4월·제1공장을 골라 범위가 갈라지는 것을 관찰(별도 로그 없음, 테스트로 고정) | 한 행 표의 행 라벨을 항상 보존하고 머리글로 축(`사업장`→site, `기간`→period)을 기록. 중복 제거는 같은 역할·값·단위·행 라벨이면서 **같은 원문**(같은 쪽·bbox 포함, bbox 없으면 같은 머리글·원문)일 때만. 같은 쪽 다른 칸 반복은 `repeated_cells`로 남김. 선택: 파생 E-3-1 후보를 E-4-1 대표 노드의 범위 순으로 정렬 | R4 8개: 사업장·월 라벨 보존, 여러 행 표, 다른 쪽 같은 값 2개, 같은 칸(표+텍스트) 1개, 같은 범위 다른 값은 상충 검토, 최종 답변이 한 범위만 쓰고 합산 안 함(사업장·월 2가지) | 가상 변형(사업장별 한 행 표 2개) 웹 분석: 근거 노드 2개(행 라벨 김해 제1공장·양산 제2공장, 축 site). 답변 E-4-1 0.0036 TJ(합산 아님)·E-3-1 0.478 tCO2eq 모두 `제1공장`, `사업장 범위 미상 또는 상이 — 합산 보류`. BM 세트 답변 범위 `2026-04-01~30 · 월간 · 제1공장` 불변 | `live/web_variant_R4_cd66424/`, `live/ui_screens_cd66424/R4/` |
| R5 비율 자릿수 | 재활용 29.3 kg / 합계 100 kg, 원문 `29%` → **mismatch + HITL**(29.0과 29.3을 소수 첫째 자리로 비교) | 비교 규칙: 재계산값(Decimal)을 **원문 표시 소수 자릿수**로 사사오입(ROUND_HALF_UP)한 값 == 원문 수치. 원문 문자열·자릿수(`reported_text`·`display_decimals`·`computed_rounded`·`rounding`)를 기록. 본문 고정 비율은 bbox가 없어도 원문 문자열을 보존. 원문 문자열 없음 → `precision_unknown`(불일치 아님, 한계 노트). 총량·재활용량의 행 라벨이 다르면 검산하지 않고 `rate_check_scope_differs` | R5 18개: 반올림 매개변수 15개(29%·29.0%·29.3%·29.30%·BM 29.3478%·0%·100%·100.0%·0.0%·경계 0.05/29.25/29.5 등), 원문 문자열 보존, 자릿수 미상, 범위 상이 | 수정 후 재현 `29%` → match. BM 실제 분석 29.3% → match(`reported_text` 29.3%, 자릿수 1). 답변 E-6-2 29.3%, 회사 답변 92% 별도 표시 불변 | `after/repro_before_after.json`, `live/core_89f34aa_*_summary.json` |

### 10.3 테스트

| 실행 | 코드 | 결과 | 기록 |
|---|---|---|---|
| 새 회귀 46개 수정 전 | `50b8d72` 제품 코드(임시 작업 폴더에 새 테스트·픽스처만 복사) | 38 failed, 8 passed | `logs/new_tests_before_fix_50b8d72.txt` |
| 새 회귀 46개 수정 후 | `89f34aa` | 46 passed | `logs/new_tests_after_fix.txt` |
| 전체 | `89f34aa` | 1693 passed, 12 skipped | `logs/full_pytest_after_fix.txt` |
| 웹 통합 전체(PR #65 venv) | `cd66424` | 1707 passed, 12 skipped | `logs/web_integ_cd66424_full_pytest.txt` |

- 수정 전 통과 8개는 보존 확인용이다: 배율 미보충, 일치 값 무경고, 가스 m³ 부피 유지, 단위 없는 가스 값 미생성, 부피+열량 이중 합산 없음,
  여러 행 표 사업장 보존, 같은 칸 1개, 같은 범위 다른 값 상충 검토.
- 건너뜀 12개: `tests/test_table_structure.py` — 실보고서 PDF(`data/real_reports/*.pdf`) 없음. 기존과 같다.
- 기존 테스트 변경 1개: `test_rate_without_location_has_no_precision_claim` — bbox 없는 본문 비율도 원문 문자열(`raw_text` 29.3%)은 남기고
  정밀도·셀 주장은 하지 않는지 확인하도록 기대값 갱신(R5).
- 이전 세트 5,400 kg이 같은 쪽 두 칸에 있어 엄격한 중복 제거 후 2개가 되던 것을 같은 쪽·같은 범위 반복(`repeated_cells`)으로 처리 —
  `test_old_set_values_are_preserved` 통과.
- 새 테스트는 외부 API를 호출하지 않는다(키 조회 함수를 None으로 고정). 재현 입력은 저장소 픽스처에 있다.

### 10.4 실제 분석(코어 단독, `89f34aa`)

| 실행 | 캐시 | 시간 | LLM 실호출 | LLM 캐시 적중 | VLM 적중/누락 | Upstage 요청/성공/실패 | 판정 |
|---|---|---|---|---|---|---|---|
| 최초 12건 | `core_cache_1dea0c5` 사본 | 43.6 s | 0 | 6 | 7/0 | 4/4/0 | 12/12 |
| 보완 13건 | 위 캐시 이어 씀 | 20.8 s | 0 | 6 | 8/0 | 4/4/0 | 12/12 |

- 환경(`live/core_run_89f34aa/*/environment.json`): 커밋 `89f34aa`(미커밋은 `review-r1/`뿐), azure_openai gpt-4.1-mini, Upstage Document Parse,
  키 설정 여부 true(값 미기록), `force_mock=False`, `strict_llm=True`, 입력 sha256 전부 구성 목록(version 2026-09-28)과 일치.
- 캐시와 재실행 구분: LLM·VLM 캐시 키는 프롬프트 전문과 실제 입력 텍스트 전체의 sha256이다(`esgenie/llm_cache.py`, `esgenie/ssot/ocr_cache.py` — 이번 수정에서 변경 없음).
  적중은 입력이 그대로라는 뜻이고, 수정한 표 해석·환산·경계·검토 정보 전달·답변·출력은 매번 새로 계산했다(Upstage OCR 실호출 포함).
  입력이 바뀌면 키가 바뀌는지는 `tests/test_llm.py`·`tests/test_ocr_cache.py` 24개(`test_key_changes_when_any_field_changes`,
  `test_preprocessing_change_invalidates_cache` 등) 통과로 확인(`logs/cache_key_tests_89f34aa.txt`). 실제 실행에서도 웹 rev13은 프롬프트에
  새 프로젝트 ID가 들어가 캐시 3건을 놓치고 실호출했다(§10.5).
- 요약(`live/core_89f34aa_{initial,followup}_summary.json`)은 `1dea0c5`(최초)·`688445a`+`1dea0c5` 비율 위치(보완)와 경로 외 동일.
- 답변: E-4-1 0.513216 TJ, E-3-1 68.158 tCO2eq, E-6-1 18.4 톤, E-6-2 29.3%(회사 답변 92% 별도). 모두 `self_reported`·범위 확인 필요,
  `2026-04-01~30 · 월간 · 제1공장`.
- HMC 회귀(`ESGENIE_FORCE_MOCK=1`, 이전 세트·기존 OCR 캐시 읽기만): `hmc/after/validation_report.json` `failed: []`(actual 75·controlled 76 검사).
  답변 값·단위·상태·비교·노트 불변. 달라진 것: 이전 세트 전력 지표 이름이 원문 라벨 열을 반영해 `사용전력량 (유효전력)`이 되어 E-4-1/E-3-1
  근거 인용 문구·노드 ID가 바뀜, 기록 필드 추가(`computed_unit`·`input_units`, 5,400 kg `repeated_cells`, `display_decimals`).

### 10.5 웹 통합(`cd66424`, 포트 8795, 새 프로젝트)

| 실행 | 입력 | 시간 | LLM 실호출/성공/실패 | LLM 캐시 적중 | VLM 적중/누락 | Upstage 요청/성공/실패 | 판정 |
|---|---|---|---|---|---|---|---|
| rev13 | BM 최초 12건 | 43.9 s | 3/3/0 | 4 | 7/0 | 4/4/0 | 12/12 |
| rev14 | + 보완 1건(13건) | 8.0 s | 0 | 7 | 8/0 | 4/4/0 | 12/12 |
| R2 변형 | `R2변형_02_…사용량칸150000.pdf` 1건 | 7.9 s | 0 | 2 | — | 1/1/0 | — |
| R4 변형 | `R4변형_…사업장별표2개.pdf` 1건 | 7.4 s | 0 | 2 | — | 1/1/0 | — |

- 서버 환경(`live/web_server_cd66424/environment.json`): 통합 커밋 `cd66424`, dirty false, 캐시는 `web_cache_d92732b` 사본, `force_mock=False`,
  `strict_llm=True`, 키 설정 여부 true. 원래 `esgenie.web.engine.run_analysis`를 변경 없이 관찰.
- 정상 세트: 판정 12개가 `d92732b` 웹 통합과 항목별 동일(`runs/*_rev1[34]/summary.json`). 번들 PDF(`실사응답서_rba42_…pdf`) 본문 텍스트가
  최초·보완 모두 `d92732b`와 **동일**(`downloads/*/report_text.txt` diff 빈 출력). Excel `응답서` 시트 E-4-1·E-3-1·E-6-1·E-6-2 행의 값·단위·범위·상태 동일.
  화면(`ui_screens_cd66424/normal_followup/`) 값 0.513216 TJ·68.158 tCO2eq·18.4 톤·29.3 %(회사 답변 92 % 나란히), 범위 `제1공장`.
- 보완 업로드 직후 이전 초안 내려받기 **409**(`web_drive_cd66424/followup_stale_download_check.json`).
- R2·R4 화면 확인: BM 세트에는 검산 불일치·다중 사업장이 없어, 원본을 건드리지 않고 `variant_inputs/make_variants.py`로 가상 변형을 만들었다
  (쪽 머리에 `검토 보완 검증용 변형본(가상) — 원본 아님` 표시, 원본·변형 sha256은 `variants.json`). 각 변형을 별도 프로젝트로 업로드·분석해
  화면 목록·상세(`ui_screens_cd66424/{R2,R4}/`, 캡처 스크립트 `variant_inputs/ui_snap_r1.mjs`), Excel, 번들 PDF(`web_variant_*/report_text.txt`)를 확인.
  - R2 PDF: `150,000kWh`·`142,560kWh`·`원측정값상충`·`배율60` 각 10회, `실제불일치` 4회.
  - R4 PDF: `0.0036TJ`, `합산보류` 4회, `제2공장` 0회(제2공장 값은 답변에 쓰지 않고 보완 대상으로만 남음).

### 10.6 보존 확인

- 루트 작업 폴더: main `387999b`, 기존 미커밋 3개(`app.py`, `docs/…v2.md`, `esgenie/ui/tabs.py`) 그대로.
- PR #65 폴더 `outputs/ui_redesign_workspace`: `6bf9750`, 미커밋 0줄(그 `.venv/bin/python`만 절대 경로로 사용).
- 원본 PDF·기존 OCR 캐시·기존 리허설·이전 세트: 오늘 수정된 파일 0개, 구성 목록 14개 해시 불일치 0개. 기존 `live/`·`logs/`·`hmc/` 기록 덮어쓰지 않음.
- 새 캐시·작업 공간·zip·원시 덤프·HMC 렌더는 `review-r1/.gitignore`로 커밋 제외(로컬 보존).
- D1/D6/HMC 임계값, 판정 체계, 모델 변경 없음. 회사명·파일명·정답 수치·해시 분기 없음. 배율·발열량 보충, m³→MJ 환산 없음.

### 10.7 남은 한계(범위 안에서 확인됨, 수정하지 않음)

- 답변 검토 문구에 불일치 노트가 두 번 나온다(`실제 불일치: <노트> · <노트> · …` — 비교 사유와 범위 노트를 잇는 기존 표시 경로).
- 화면 상세의 제목 아래 문구가 기존 비교 불일치 공통 문구 `같은 기준으로 비교한 회사 답변과 근거의 값이 다릅니다`라, 같은 문서 안 검산 불일치에는
  맞지 않는다. 목록 행에는 `확인 필요`만 보이고 `실제 불일치`는 상세·Excel·PDF에 보인다. 화면 문구 변경은 범위 밖.
- 지침으로 계산한 MWh 사용량은 파생 배출량(E-3-1)을 만들지 않는다(기존 파생은 kWh·MJ만 처리).
- R4에서 두 사업장 중 답변에 쓰는 범위는 노드 순서로 정해진다(합산하지 않고 다른 범위는 `합산 보류`·보완 대상으로 표시). 어느 사업장을 쓸지 사용자가 고르는 기능은 없다.
- LLM·VLM이 읽은 비율처럼 원문 문자열이 없는 값은 자릿수 검산을 `precision_unknown`으로 남긴다.
- R2·R4 화면 확인은 가상 변형 입력 1건씩이다. R4 변형은 생성한 단순 표 양식이며 BM 양식이 아니다.
- HMC는 이전 세트 오프라인(모의 LLM) 회귀만 실행했다. 실제 분석은 rba42만.
- 범위 밖으로 분리: 전사 73명 인원에 사업장이 붙는 경계 추론, 회사 답변 범위 파서, 교육 참석자 통합(§8).
