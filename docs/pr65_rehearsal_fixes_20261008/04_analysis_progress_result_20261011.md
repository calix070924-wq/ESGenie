# 수정 4 — 분석 진행·재시도 후속 수정 결과

작성일: 2026-10-11 (Asia/Seoul)

## 기준과 범위

- 기준: `6f5dd9488b54570b2115345e4a74d35d2aa2b28c`. 착수 시 GitHub PR #65의 최신 head가 이 SHA임을 `gh pr view 65`로 확인했다.
- 최초 제품 수정 SHA: `e6521b7b8cabf6d8bc5b6e7c0d5db4962f749dc5`. PR #79 검토 후 최종 후속 수정 SHA: `2c3f1bc2d303a9dc74576f8164658771f86d3802`. 아래 최신 검증은 이 후속 코드와 동일한 작업 내용에서 실행했다.
- 브랜치: `codex/rehearsal-analysis-progress-20261008`. 기존의 깨끗한 전용 worktree `/private/tmp/ESGenie-rehearsal-analysis-progress-20261008`를 재사용했다.
- PR 대상: `codex/ui-guided-workspace`. main 병합·수정 1/2/3 통합은 이 작업에 포함하지 않는다.
- `web/review.py`, `web/downloads.py`, `supplychain/*`, `ssot/ocr_router.py`, `pipeline.py`, A/B의 시간 계측·평가 도구·라벨을 변경하지 않았다.

## 이전과 이후

이전에는 대기/실행/종료 문구만 저장했고, 약 600초의 AI 요청 시간 초과나 자동 재시도가 진행 안내에 나타나지 않았다. 이제 파일 처리 경계와 실제 `LLMClient.complete()` 요청·재시도 경계에서 실행별 이벤트를 발생시키고, 서버가 이를 저장해 화면에 표시한다.

- 대기 → 회사 답변/증빙 읽기 → 읽은 자료 분석 → 문항별 답변·근거 정리 → 완료/실패.
- 실제 처리 중 파일 목록, 처리 완료 파일 수/대상 수. 전체 비율·예상 종료 시각을 만들지 않는다.
- 실제 요청 대기/재시도 상태, 시도 번호/최대 시도, 서버가 요청한 백오프 시간과 현재 시도 경과 시간. 폴링이 짧은 백오프를 놓쳐도 다음 시도 대기 중에는 최근 재시도 사유를 표시한다.
- 실행·단계 경과 시간과 새로고침 복구. 폴링 연결 실패 시 회전 표시를 멈추고 마지막 수신 상태임을 알린다. 연결 복구 후 서버 상태로 돌아간다.
- 성공/실패/서버 재시작 때 활성 파일·요청을 비우고 종료 시각을 저장한다. 최종 실패 사유는 시간 초과·연결·인증·요청/사용 한도 등 안전한 분류로 표시하고, 이전 결과·담당자 저장값을 보관하며 다시 시도 버튼을 제공한다.

## 선택적 이벤트 계약과 담당 경계

`esgenie.progress.observe(run_id, observer)`는 worker 실행에만 적용되는 ContextVar 관찰 범위다. `emit(kind, **fields)`는 관찰자가 없으면 아무것도 전달하지 않고, 관찰자가 실패해도 분석 예외로 전파하지 않는다. 각 이벤트에는 `run_id`, 단조 증가 `sequence`, UTC ISO `at`를 포함한다. 작업 ID는 해당 작업의 Store 콜백에 고정된다.

| kind | 추가 필드 | 의미 |
|---|---|---|
| `targets` | `total_files` | 실제 엔진의 대상 파일 수 |
| `stage` | `phase`, `stage` | `reading`, `analyzing`, `organizing` 단계 |
| `file_started/finished` | `file_id`, `file_name`, `file_role` | 처리 중 집합과 중복 없는 완료 파일 집합 |
| `request_started` | `request_id`, `provider`, `attempt`, `max_attempts` | 실제 SDK 호출 직전 |
| `request_retry` | 위 필드 + `delay_seconds`, `reason` | 일시 오류 후 다음 시도의 백오프 진입 |
| `request_finished` | `request_id` | 호출 성공 또는 최종 실패 |

누적 상태 계산은 순수 모듈 `esgenie/progress_state.py`를 실행 관찰자와 Store가 함께 사용한다. 기존 웹 모듈은 호환 재수출을 유지한다. 이벤트는 공개 필드만 담은 누적 `snapshot`도 전달한다. 디스크 저장이 실패해도 실행 메모리의 상태는 남아 다음 이벤트 저장에 빠진 완료 수·활성 파일·요청·단계를 복구한다. 이벤트 이력은 최근 100개로 제한하며 snapshot 안에 snapshot을 중첩 저장하지 않는다. `observe(..., initial_state=job)`가 반환하는 누적 상태는 같은 실행의 성공/실패 최종 저장에도 반영하므로 마지막 이벤트 저장 실패도 복구한다. 최종 저장 자체가 불가능한 디스크 장애까지 저장 성공을 보장하지는 않는다.

job의 새 필드는 선택값이다. 기존 `status/stage/error`를 유지하고 `run_id`, `phase`, `queued_at/started_at/stage_started_at/ended_at/event_at`, `sequence`, `total_files`, `completed_files`, `completed_file_ids`, `active_files[]`, `requests[]`, 최근 100개 `events[]`를 저장한다. 새 실행에서는 초기화한다. Store는 실행 ID 불일치·역순·중복·종료 후 이벤트를 거부한다. 이전 worker의 늦은 성공/실패도 새 실행을 덮어쓰지 못한다. 프롬프트·원문·키·SDK 예외 전문을 진행 기록에 저장하지 않는다. 요청의 선택 필드 `last_retry`에는 최근 재시도의 `attempt/at/reason/delay_seconds`를 담아 같은 요청의 다음 시도까지 유지한다. 요청 종료 때 제거하고 다른 요청·새 실행으로 넘기지 않는다.

A/수정 1의 파일을 직접 수정하지 않기 위해 **현재의 순차 소비 경계**를 웹 입력 어댑터에서 관찰한다. `EvidenceFiles.items()`는 실제 `pipeline._collect_ocr_extractions` 루프의 첫 순회만 관찰하고, `CompanyFiles.__iter__()`는 `parse_saq_claims`의 기존 단일 호출을 관찰한다. 소비자가 파일 처리 후 다음 반복을 요청할 때만 완료 이벤트를 보낸다. 청크·LLM 호출은 파일 수를 늘리지 않는다. 파일 내부 예외/중도 종료에는 완료를 보내지 않는다. 추출·게이트·일관성 검증·RBA 태깅의 호출 순서와 파이프라인 내부 시간 계측은 그대로다. 전역 로그 수집·함수 monkeypatch·파일별 파이프라인 재실행은 사용하지 않는다.

05번 회사 작성 답변도 별도 파싱 종료 후 파일 1개로 센다. 총수는 `len(evidence) + len(company_files)`이며, 제외·업로드 오류 파일은 제외한다. 기존 엔진은 이름이 같은 증빙을 dict에서 하나로 처리하므로 이 경우도 실제 처리 대상을 따른다. 여기서 완료는 파일 처리 함수의 종료이며, 증빙 검증 판정이 ‘예’라는 뜻은 아니다. 자료의 partial/failed/limitation 판정은 기존 경로를 유지한다.

현재 엔진은 순차 처리이므로 실제 활성 파일은 한 개다. 저장/UI는 여러 활성 파일과 요청을 지원한다. 향후 A/수정 1이 병렬 처리나 입력 사전 순회를 추가하면 파일 시작/종료 접점을 해당 담당 파일에 명시적으로 옮기고, 새 스레드에 `copy_context()` 등으로 실행 문맥을 전달해야 한다. 다른 스레드에 전역 진행 문맥을 자동 전파하지 않는다.

## 시간 초과와 재시도 정책

- 모델: `gpt-4.1-mini` 유지. 모델·프롬프트·결과 판정·캐시 키를 바꾸지 않았다.
- 설치된 OpenAI Python SDK: 2.32.0. 실제 기본 timeout은 `connect=5`, `read/write/pool=600`초이다. `create_openai_client()`의 `max_retries=0`을 유지해 SDK 재시도와 앱 재시도가 중첩되지 않는다.
- 앱 최대 시도는 3회(첫 요청 포함), 일반 백오프는 약 1초+2초에 각 0~0.1초의 jitter이다. 기존 Retry-After 우선 정책을 유지한다. 동일한 600초 읽기 시간 초과가 매 시도 발생하면 한 completion은 대략 1,803초가 걸릴 수 있다. 이는 전체 분석의 절대 기한이 아니다. SDK timeout은 네트워크 동작별 대기 제한이며 Retry-After 값과 요청 수에도 전체 시간이 달라진다. 전체 실행에 별도 절대 상한은 기존과 같이 없다.
- 408/409/429/5xx·연결·시간 초과는 한도 내 재시도한다. 다른 HTTP 오류와 명시적 `insufficient_quota`/결제 한도 오류는 종료한다. strict 웹 경로에서 모의 성공으로 바꾸지 않는다.
- 600초 기본값을 임의로 낮추지 않았다. 대표/긴 문서의 신규 라이브 성공률·속도 개선은 이번에 측정하거나 주장하지 않았다.
- Upstage OCR 요청의 기존 120초 timeout도 변경하지 않았다. 그 대기는 자료 읽기 단계와 현재 파일·단계 경과로 표시하고, OpenAI 요청처럼 표시하지 않는다.
- 재시도 오류 분류 검토에는 [OpenAI 공식 오류 문서](https://developers.openai.com/api/docs/guides/error-codes)를 함께 확인했다.

## 검증과 제출 상태

최초 검사 산출물은 worktree의 `outputs/rehearsal_fixes_20261008/progress/`, PR #79 후속 재검증 산출물은 그 아래 `pr79_followup_20261011/`에 격리한다. 최초 결과와 검토 재현 기록을 덮어쓰지 않는다. 합성 테스트 PDF와 오류 주입만 사용하며 실제 회사 자료·인증정보를 커밋하지 않는다.

- Python 전용/관련 웹·LLM·클라이언트·파이프라인 종단 회귀: **106 passed** (45.03초). 외부 서비스 모의 응답과 오프라인 설정으로 실행했다. 이 중 진행 전용 검사는 30건이다.
- 검사 내용: 실제 pipeline collector의 입력/추출 결과 동일성, 실제 한글 PDF에서 회사 답변 다중 파일 파싱 동일성 및 비어 있지 않은 주장값·연도·출처·후보·상충 진단, 목표/범위 밖 비율/질문 문장의 제외 진단, LLM 캐시 키/캐시 응답 동일성, 실제 completion 분기의 일시 오류 복구·3회 최종 실패·401/400 즉시 실패·408 복구, quota 비재시도, 관찰자 실패 격리, 파일 완료 저장 1회 실패 후 다음 파일 시작에서 복구, 115개 진행 저장 실패 후 성공/실패 최종 저장에서 정확한 파일 수 복구와 최근 100개 이력 상한, 다음 시도·요청 경계의 재시도 사유 유지/초기화, 여러 활성 파일/요청, 중복·늦은 이벤트, 연속 재분석 초기화, 작업 격리·직렬 대기, 저장 상태 재읽기, 서버 재시작, 이전 결과 보존, 늦은 worker 결과 차단.
- `npm run build`와 변경 프런트 파일 Prettier 형식 검사 통과. 분석 안내만 별도 컴포넌트/스타일로 추가하고 기존 세 단계 디자인을 보존했다.
- 실제 Chrome 전용 오류 주입 시나리오 **4 passed** (1.4분). 파일/재시도/새로고침/완료, 최종 실패/이전 결과/재시도, 백오프 응답을 받지 않은 화면과 새로고침 후 재시도 표시/사유 유지, 폴링 연결 끊김/복구를 확인했다. 390px 좁은 화면에서도 넘침이 없었다. 기존 Chrome 웹 회귀 **4 passed** (8.1초): 저장 보호·검토 완료·실제 Excel/PDF/ZIP 다운로드·자료 교체·재분석·긴 파일명/좁은 화면을 확인했다. 전용 서버는 8784, 기존 회귀 서버는 8771이며 각 데이터 경로를 분리한다.
- 개발 중 Chrome 검사 실패 기록은 `chrome/initial_test_failure`와 `chrome/second_test_failure`에 보관한다. 원인은 검사 대기 시간/존재하지 않는 ‘재분석’ 버튼 선택/innerText와 textContent 비교 방식 차이였으며, 서비스 실패를 모의 성공으로 바꾸지 않았다. 후속 한글 PDF 검사 최초 실행의 5건 실패는 글꼴 추출 공백이 NBSP인 데 대한 테스트 문자열 비교 문제였다. 추출 확인용 공백 비교만 정규화했고, 실제 파서 입력·제품 파싱 로직은 그대로 유지해 5건 모두 통과했다.

**남은 통합 검증:** 수정 1/2/3·A/B를 합친 동일 최종 코드/모델/세트/라벨로 신규 실제 17→18건을 실행하고, 화면의 파일·재시도 이벤트와 실제 서비스 호출 기록을 대조해야 한다. 이번 모의 SDK 오류 주입 Chrome 검사와 오프라인 회귀는 그 최종 라이브 리허설을 대체하지 않는다.


## 재실행 명령

Python(웹 의존성이 설치된 환경):

```sh
python -m pytest tests/test_rehearsal_progress.py tests/test_llm.py tests/test_openai_endpoint.py tests/test_web_workspace.py tests/test_web_review_flow.py tests/test_pipeline_e2e.py -q
```

`frontend/`에서:

```sh
npm run build
ESGENIE_PYTHON=/path/to/python npm run test:progress
ESGENIE_PYTHON=/path/to/python ESGENIE_LOCAL_CHROME=1 npm test
```

전용 Chrome 검사는 실제 Chrome 설치가 필요하며 8784 포트를 사용한다. `ESGENIE_PYTHON`은 fastapi/uvicorn/프로젝트 의존성이 설치된 Python을 지정한다. 기존 웹 회귀는 8771 포트를 사용한다.
