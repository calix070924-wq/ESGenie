# 후속 검증 기록

이 폴더는 2026-09-21 후속 작업의 작은 실행 기록이다. [결과 보고서](../../HMC_응답서_검증정합성_후속수정결과_2026-09-21.md)의 F1~F9 및 R01~R30 표와 함께 읽는다.

- `review_*`: 원본 `/Users/heojeongmin/Documents/Claude/Projects/ESGenie/outputs/reviews/hmc-integrity-20260921/`에서 복사한 재검토 기록. `review_reproduce.py.txt`는 원문 보관용이며 현재 실행 스크립트가 아니다.
- `reproductions.json`, `failures_before.txt`, `baseline_pytest.txt`: 시작 HEAD 902a37a에서 직접 재현한 수정 전 실패와 기존 전체 테스트 결과.
- `cases_after.json`: 합성 OCR fixture부터 확정 원장·D1·답변까지 실행한 수정 후 핵심 사례. 함수는 `tests/test_hmc_followup.py`의 실행된 회귀와 동일하다.
- `full_tests.txt`: 전체 1,515 pass / 12 skip. 외부 실보고서 PDF 부재 skip은 HMC 필수 입력과 별개다.
- `regression_matrix_tests.txt`: HMC 후속·경계·검증기·적합성·안내·캐시·확정원장·fresh contract의 8개 테스트 파일, 202건의 개별 PASSED 기록.
- `export_validation_tests.txt`: 검증기 계약·출력·인용·글리프 58건. source_identity/page 필수 검사 추가 뒤 실행.
- `compatibility_tests.txt`: 상태·기권·구형 입력·인용·K-ESG·원장·응답서 관련 211건.
- `validation_report.json`, `replay_console.txt`: 실제 PDF/저장 OCR 연결, 실제/통제 출력 151개 검사, 음성 대조군 5개, 실제 10문서 적합성·grounding.
- `ui_apptest.json`, `ui_console.txt`: 동일 ResponseSheet의 production UI 소비 검사. 업로드 전체 UI 자동화나 라이브 LLM 검증이 아니다.
- `isms_corrections.json`: 검색한 2,189개 파일의 경로, 발견한 2개 HMC E-7 혼입 산출물, 원본·정정본 해시 및 전체 전후 문구. 역사적인 취득 오기 실파일은 찾지 못했음을 명시한다.
- `original_before.json`, `preservation.json`: 원본 main·기존 변경·stash·175개 파일과 기존 검증 산출물 보존.
- `output_manifest.json`: Git 제외 최종 파일 36개의 절대 경로/해시/크기. `render_review.json`, `excel_renders.json`은 시각 검토 범위.

재생 명령은 결과 보고서에 있다. 모든 명령은 지정 worktree에서 원본 가상환경 Python으로 실행한다. `scripts/hmc_integrity_validation.py`는 필수 원문·캐시가 없으면 실패한다.

Excel 렌더는 `scripts/hmc_render_excel.mjs`를 사용한다. 실행 환경에 `@oai/artifact-tool`이 필요하다. 이번 실행에서는 `/private/tmp/hmc-artifact-render/node_modules`를 제공된 런타임의 node_modules에 연결한 뒤 스크립트 사본을 실행했다. 원본 xlsx를 import/render만 하며 변경·재수출하지 않는다. 정정본 D54 렌더도 동일 import/render API로 수행했다.
