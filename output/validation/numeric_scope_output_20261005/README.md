# numeric_scope_output_20261005 — 수치·범위 최종 출력 검증 산출물

검증 기록: `docs/수치범위_최종출력_검증기록_2026-10-05.md`, UI 계약: `docs/UI연결용_수치범위_출력계약_2026-10-05.md`.

저장소에는 도구(`tools/`)·정답 키·요약(검사 결과·환경·통계·측정)만 들어 있다. 원시 응답(`runs/*/*/raw_upstage`)·
LLM/OCR 캐시(`caches/`)·내보낸 Excel/PDF·증빙 사본·실보고서 쪽 이미지는 사용자 프로젝트의 같은 경로에만 있다.

| 경로 | 내용 |
|---|---|
| `00_baseline/` | 정답 키, 기준선 관찰, §5 프로브(main·최종), 보존 해시, 기준선 전체 테스트 |
| `01_replay/` | PR69 저장 응답(새·이전 캐시)과 R0 VLM 응답의 라우터 재생 |
| `02_real_zero_sample/` | 실보고서 표본 선택·정답·실행·측정 |
| `03_tests/` | 최종 테스트 로그·JUnit |
| `03_variant/` | 검증용 가상 변형본(원본 아님) |
| `04_hmc/` | HMC 감사 검증(main·최종) |
| `runs/R0_main_bba2206_live` | main 실제 실행(12→13) |
| `runs/R1_replay_<sha>` | R0 기록 응답으로 최종 코드 재생 |
| `runs/R2_fix_4faabb0_live` · `runs/R2r_replay_<sha>` | 수정 코드 실제 실행 · 같은 응답으로 최종 코드 재조립 |
| `runs/R3_variant_live` · `runs/R3r_replay_<sha>` | 변형본 실제 실행 · 재조립 |
| `superseded/`, `runs/superseded/` | 대체된 실행(삭제하지 않고 보관) |
| `05_followup/` | PR71 검토 보완(R1~R6) 후속 검증 요약 — 실행 통계·출력 검사·오답 주입·검사기 비교(전체 산출물은 사용자 프로젝트 `output/reviews/pr71_20261005/followup_fix_validation/`) |
