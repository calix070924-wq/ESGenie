# 일반 문서 AI 대조군 — 실행기·사전 등록·수동 채팅 대조군 채점 결과 (작업지시서 B §4.2)

## 요약

- ESGenie 와 **같은 입력**(같은 실행의 OCR 텍스트 + 회사 자가답변 05 원문)을 일반 LLM 에 한 번에 주고 48문항에
  답하게 하는 **대조군 실행기**를 추가했다. 결과는 A 의 공통 답안 형식 v1.1 로 변환된다.
- 사례(`e49e69a`)와 프롬프트(`2717880`)는 **실행 전에 사전 등록**했다. 이후 고치지 않았다.
- Azure 크레딧이 소진돼 API 대조군(`gpt-4.1-mini`·`gpt-4.1`)은 실행하지 않았다(2026-10-13 결정).
  대신 **claude.ai 채팅(Sonnet)에서 사람이 수동으로 단계당 3회** 실행했다. **유료 API 호출은 0건**이다.
- A 채점기(`origin/codex/eval-scoring-20261006` `51e0bf4`)와 A 확정 라벨(96행, `52f03f4`)로 ESGenie
  `LIVE_n5_d8a0de8` 과 대조군 6회를 채점했다. ESGenie 채점값은 A 기록과 같다.
- **팀이 만든 가상 세트, 특정 프롬프트·수동 채팅 기준의 결과다. 같은 모델 비교가 아니다.**
  이 세트에서는 ESGenie 의 잘못된 확정이 단계마다 11건, 대조군은 initial 8~14건·followup 2~13건이었다.
  ESGenie 가 대조군보다 못한 행은 initial 11행·followup 14행이고, 전부 README §10.7 에 적었다.

## 들어오는 파일

| 파일 | 내용 |
|---|---|
| `scripts/baseline_generic_llm.py` | 대조군 실행기. API 경로(`--provider openai`, 이번엔 미사용), 저장 응답 재변환(`--rescore-only`), 수동 채팅 경로(`--provider manual` 내보내기 / `--import` 가져오기) |
| `tests/test_baseline_generic_llm.py` | 62개. LLM 은 mock, 네트워크 호출 없음 |
| `data/eval/cases/baseline_cases_v1.csv` · `baseline_cases_v1_criteria.md` | 사례 48행·함정 유형과 분류 기준. 사전 등록 `e49e69a` |
| `data/eval/prompts/baseline_prompt_v1.md` | 대조군 프롬프트 v1. 사전 등록 `2717880` |
| `docs/validation/eval-baseline-20261006/README.md` | 조건·기록 규칙(§1~§9)·결과(§10) |
| `docs/validation/eval-baseline-20261006/results_summary_20261013.json` | 비교표용 요약. 문항 ID·집계 위치·지표만 있고 라벨 원문·응답 원문은 없다 |
| `docs/validation/eval-baseline-20261006/PR_BODY.md` | 이 글 |

제품 코드(`esgenie/`)는 바꾸지 않았다. A 의 채점기·어댑터·라벨은 이 브랜치에 복사하지 않았고, 고정 SHA 에서
저장소 밖으로 꺼내 실행했다. 실행 결과 원본(`outputs/`)은 커밋하지 않았다.

## 조건

| 항목 | 값 |
|---|---|
| 자료 | 팀이 만든 **가상** 세트 `한울정밀_촬영세트_정상5건보강_20261007` — initial 17건 / followup 18건, 48문항(`rba42`) |
| 대조군 입력 | ESGenie 같은 실행(`LIVE_n5_d8a0de8`)의 OCR 텍스트 + 05 원문(증빙과 분리된 구획, "검증된 증빙 아님" 표시) |
| 프롬프트 · 사례 | v1 `2717880`(sha256 `5b6cd37e…`) · `e49e69a` |
| ESGenie | `LIVE_n5_d8a0de8`, 코드 `d8a0de8`, `gpt-4.1-mini`, 1회(followup 은 캐시 재사용 실행) |
| 대조군 | claude.ai 채팅, 화면 표기 `Sonnet`(세부 버전 미기록), 단계당 3회. 메모리 끔, `계속` 0회 |
| 통제 못 한 것 | temperature·토큰 수·채팅 서비스의 숨은 시스템 지시 |
| 채점 | A 채점기 `51e0bf4`, A 확정 라벨 `52f03f4`(sha256 `a18a87a6…`). B 독립 라벨은 쓰지 않음 |
| 사전 등록 순서 | 사례 10/07 16:28 → 라벨 10/10 20:07 → 프롬프트 10/13 10:35 → 첫 채팅 10/13 13:52(근사) |

## 결과 (A 채점기 값 그대로, 단계별 / 대조군은 3회 최소~최대)

| 지표 | initial ESGenie | initial 대조군 | followup ESGenie | followup 대조군 |
|---|---|---|---|---|
| M1 답변·근거 동시 정답률 | 2/7 | 2/7~6/7 | 2/7 | 1/7~6/7 |
| M2 잘못된 확정 수 | 11 | 8~14 | 11 | 2~13 |
| M3 불필요한 보류 수 | 5 | 0 | 5 | 0 |
| M4 확정 제출률 | 13/48 | 14/48~16/48 | 13/48 | 8/48~14/48 |
| M5 자료 연결률 | 13/13 | 모두 100% | 13/13 | 모두 100% |
| M6 값 제출률 | 13/48 | 3/48~14/48 | 13/48 | 2/48~8/48 |
| 올바른 보류 수 | 30 | 32~34 | 30 | 34~39 |

| 항목 | 값 |
|---|---|
| ESGenie 가 대조군(3회 중 1회 이상)보다 못한 행 | initial 11 · followup 14 (README §10.7 에 전부) |
| ESGenie 가 맞고 대조군이 1회 이상 틀린 행 | initial 7 · followup 6 |
| 대조군이 05 를 근거로 쓴 행 | 6회 합계 1행(followup rep3, 증빙과 함께 인용) |
| 대조군 반복 간 `decision_detail` 변동 행 | initial 3 · followup 7 |
| 불러오기 | 6회 모두 48행, 누락·파싱 실패·검증 문제 0 |
| ESGenie 채점 vs A 기록 | 집계 위치·M1~M6·문항 목록 모두 일치 |

함정 유형별 결과는 README §10.6 에 있다. ESGenie 의 틀린 행은 `single_policy_multi_yes`(잘못된 확정 9)와
`normal`(불필요한 보류 3)에 몰려 있다.

## 한계

1. **같은 모델 비교가 아니다.** 차이에는 검증 구조와 모델의 차이가 섞여 있다. 둘을 가를 동일 모델·상위
   모델 대조군은 예산 소진으로 실행하지 않았다.
2. 팀이 만든 가상 세트 1개, 프롬프트 v1 1개, 수동 채팅 기준이다. 일반화하지 않는다. "N배" 표현을 쓰지 않는다.
3. 대조군은 단계당 3회이고, temperature 를 통제하지 못했다. 3회 사이 변동이 크다(M2 followup 2~13).
4. ESGenie 는 1회 실행이고, followup 은 캐시 재사용 실행이다.
5. 라벨은 A 확정 라벨이다. A 기록상 B 독립 검토 요건은 아직 충족되지 않았다.
6. 대조군이 예/아니오 값을 비우거나 단위를 `kg` 로 낸 행은 A 규칙대로 잘못된 확정으로 세었다(규칙 변경 없음).
7. 대조군 모델 세부 버전과 정확한 실행 시각은 기록되지 않았다(화면 표기·사람 기록 근사값).

## 작업지시서와 다른 점

| 다른 점 | 이유 |
|---|---|
| 동일 모델(5회)·상위 모델(3회) 대조군 미실행 | 사람 결정(2026-10-13) — Azure 크레딧 소진 |
| 타 공급자 대조군을 API 가 아닌 claude.ai 수동 채팅으로, 단계당 3회 | 같은 결정 |
| temperature 0 조건 불충족(`null`) | 채팅에서 제어할 수 없다 |
| 프롬프트 앞뒤에 `[시스템 지시]`·`[사용자 입력]` 머리글 | 채팅에 system 역할이 없다. 프롬프트 파일 내용은 그대로 |
| 토큰 수·비용 기록 없음 | 채팅이 알려 주지 않는다. 유료 API 비용 0 USD |

## 검증 결과

| 검증 | 결과 |
|---|---|
| 관련 테스트 `pytest tests/test_baseline_generic_llm.py` | **62 passed** |
| 전체 `pytest -q` (Windows, Python 3.13) | 22 failed / 5505 passed / 27 skipped / 6 errors. **이 브랜치 이전 단계와 실패 목록이 같다**(28건 `diff` 동일). 21건은 asyncio self-pipe ↔ `tests/conftest.py` 소켓 차단 환경 문제, 1건은 `run_demo_hanwool` — 이 PR 과 무관하며 고치지 않았다 |
| 불러오기 6회 | 모두 48행, 누락·파싱 실패·검증 문제 0. 불러오기 전후 응답 파일 sha256 동일 |
| 채점 7회(ESGenie 1 + 대조군 6) | 모두 종료 코드 0, 형식 문제 0, `unresolved` 0 |
| ESGenie 채점 재현 | A 기록(§8)과 집계 위치·M1~M6·문항 목록 일치 |
| 사전 등록 파일 | `git diff e49e69a HEAD -- data/eval/cases/`, `git diff 2717880 HEAD -- data/eval/prompts/` 빈 출력 |
| 제품 코드 | `esgenie/` 변경 없음 |

🤖 Generated with [Claude Code](https://claude.com/claude-code)
