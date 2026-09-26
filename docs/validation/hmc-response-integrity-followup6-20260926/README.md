# 7차 검토 후속 수정 검증 기록 — 6차

이 폴더는 2026-09-26 **7차 검토**(HEAD `80ef542` 대상, P2 1건) 후속 수정의 실행 기록이다. 1~5차 기록 폴더
([followup](../hmc-response-integrity-followup-20260921/)·[followup2](../hmc-response-integrity-followup2-20260921/)·[followup3](../hmc-response-integrity-followup3-20260921/)·[followup4](../hmc-response-integrity-followup4-20260921/)·[followup5](../hmc-response-integrity-followup5-20260923/))는
그대로 보존했다. 검토자 기록(`outputs/reviews/hmc-integrity-seventh-review-20260926/`)은 읽기만 했다. 결과는 보고서의
[7차 검토 후속 수정](../../HMC_응답서_검증정합성_후속수정결과_2026-09-21.md#7차-검토-후속-수정--2026-09-26-6차) 절과 함께 읽는다.

## 이번 회차가 고정한 것

- `denial_forms_results.json`, `review7_probe_console.txt`: 7차 probe(`probe_denial_forms.py`) 원문을
  수정 후 코드(HEAD `0c26e52`)로 실행한 결과. 수정 전 10건 중 2건 실패(`취득하지 않았으며/못했으며`),
  지금은 **10/10, `failed: []`, 종료 코드 0**. `worktree_clean: false`는 Git 제외·미추적 캐시
  (`__pycache__` 등) 때문이며 추적 파일 변경은 없다.
- `run_review7_probe.py`: 위 드라이버. 검토자 원문은 고치지 않고 대상 작업 폴더와 출력 폴더 상수만
  치환해 실행한다(치환 실패 시 assert로 중단).
- `negative_control_tests.txt`: 새 회귀 `test_review7_*` 5건의 음성 대조. HEAD `80ef542` 제품 코드
  (`git archive HEAD` 사본에 이번 테스트 파일만 복사)에서 부정 어미 **3건 실패**, 대조군 2건 통과.
- `full_tests.txt`: 전체 **1,590 passed / 12 skipped / 경고 8개**(5차 1,585 + 이번 5).
- `followup_tests.txt`: `tests/test_hmc_followup.py` **105 passed**.

## 이번 회차에 하지 않은 것

- 5·6차 probe, 이전 조합 probe, 실제 자료 재생, UI AppTest는 재실행하지 않았다. 7차 검토가
  80ef542에서 모두 통과를 확인했고, 이번 변경은 `_cert_denied_nearby()`의 부정 판정 한 곳이다.
- 경고 수가 5차 기록(5개)과 다르다. 수정 전 코드인 검토용 사본(`80ef542`)을 같은 환경에서
  실행해도 **1,585 passed / 경고 8개**였으므로 이번 수정과 무관한 실행 환경 차이다. 이전 기록의
  `venv/bin/python`이 현재 없어 시스템 `python3`로 실행했다.
- 라이브 LLM·새 OCR 호출은 하지 않았다.

## 작업 폴더 복구

임시 작업 폴더 `/private/tmp/ESGenie-hmc-response-integrity-20260920`의 `.git` 연결 파일이 없고 추적 파일
614개가 삭제된 상태였다(수정된 추적 파일 0개). `.git` 연결 파일을 다시 만들고 **삭제된 추적 파일만**
HEAD `80ef542`에서 복원한 뒤 작업했다. 삭제 원인은 확인하지 않았다.
