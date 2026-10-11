# A-2 작업시간 계측 — 검증 기록 (브랜치 `codex/eval-timing-20261006`)

작업지시서 A §3(작업시간 계측)의 구현과 검증을 한곳에 모았다. **산출물이 없는 항목은
없다고 적는다** — 사람 참가자가 필요한 항목은 미실시로 둔다.

## 0. 현재 상태 (2026-10-11 저녁 갱신 — 정민 보완 지시서 T1~T4 + 추가 지시서 T3-1~T3-3·T1-추가)

재검토 기준 SHA `df0f7bc0a0096b26c341eea24047812ac2049c7c`, 기준 main `d8a0de8`. 이 갱신이 들어간 커밋 SHA는 PR #74 본문 항목표에 적는다. 아래가 현재 상태다. §1 이하는 2026-10-07 기록이며 날짜가 붙은 과거 기록으로 보존한다.

| 항목 | 상태 | 근거 |
|---|---|---|
| T1 동일성 검사기 강화·합성 변경 검출 | **통과.** 정규화(실수 반올림·문자열 시각/경로 치환·키 이름 기준 삭제)를 없애고 명시 경로(최상위 `generated_at`·`timings`)만 뺀다. 옛 검사기는 값 1e-10 차이·인용문 시각·절대경로·업무 필드 `seconds` 변경을 모두 "같다"로 판정했다 | `scripts/verify_timing_noop.py`, `tests/test_verify_timing_noop.py`(21건) |
| T1 공개 샘플 덤프 일치 | **제한적 참고 기록 — 계측 전후 불변 증명 완료로 인정하지 않는다**(추가 지시서 §5). `d8a0de8`(계측 전)과 이 브랜치의 공개 샘플 덤프가 바이트 동일(sha256 `5e4e96aa980c280a7cd5d8b7bfab6f9bc22a7e768acb0f4dd9f7cf0322483d6b`, 차이 0, 뺀 경로 0)했지만 한계가 둘이다. ① 조건 차이: 계측 전 코드 쪽은 git에 없는 로컬 DART 캐시가 없어 네트워크 호출을 시도했다가(차단) 폴백으로 처리됐다. ② 범위: 공개 샘플 요약 덤프이며 initial·followup 전체 `result.json` 비교가 아니다 | `noop_20261011/noop_report_d8a0de8_vs_HEAD.json` (과거 기록으로 보존) |
| T1 통합 후보의 계측 전후 동일성 증명 | **확인 대기.** 10/13~14 통합 후보에서 통합 담당자와 함께 한다. 인정 조건(추가 지시서 §5): 양쪽 전체 SHA·코드 diff(수정 1~4 등 제품 변경은 같고 계측만 다름), 같은 PDF·요청·모델, 같은 저장 OCR/LLM 응답의 격리 캐시 사본(원본·사본 해시와 소비 기록), 캐시 누락·예상 외 live call·mock·폴백 차이가 생기면 해시가 같아도 미충족, initial·followup 전체 `result.json`과 단계별 해시·diff·종료 코드, 실제 제외 경로, 양쪽 import 위치. 10/15 캐시 미사용 성과 측정과는 다른 실행 ID | — |
| T2 CLI | **완료.** `aggregate_worktime.py`·`verify_timing_cache_modes.py`가 저장소 루트를 스스로 import 경로에 넣는다. 저장소 밖 작업 디렉터리에서 `PYTHONPATH` 없이 네 CLI가 종료 코드 0(테스트로 고정) | `tests/test_eval_worktime.py` |
| T3 집계 항목 | **통과(합성 대조)** — 참가자 수, 단계별 합계·겹침, 문항별 중앙값(일괄 구간 미배분), 재사용, 기록 완전성. 2026-10-11 저녁 추가 수정: T3-1 기계 시간 완전·부분 관측·미측정 구분(0초 메움 제거), T3-2 자료 구분·세트·조건별 분리(세션 메타데이터), T3-3 전체 대상 목록 규칙(답안 파일·세션 메타만 전체 목록, worklog qid는 부분 목록) — §0.3 | `esgenie/eval/worktime.py`, `tests/test_eval_worktime.py` |
| ④ 공개 샘플 리허설02 | **실시 — 기록 일부 누락.** 아래 표 | `rehearsal02_20261011/` |
| BM 세트 실제 시간 비교 | **시작 전.** 참가자·일정·요청 범위·기록 방법을 10/12까지 정민과 확정해야 한다. 사람 측정이 불가능하면 자동 처리시간만 측정했다고 쓰고 가치성 성과 수치는 미측정으로 남긴다 | — |

### 0.0 회귀 (2026-10-11 저녁, 이 브랜치 최종 head)

| 검사 | 결과 |
|---|---|
| `pytest -q -k 'pipeline or timing or worktime'` | **181 passed, 1 skipped** (2026-10-11 저녁, T3-1~T3-3 테스트 33건 추가 후. 같은 날 오후 `df0f7bc` 148 passed, 10-07 기록 114 passed) |
| 전체 회귀 `pytest -q` | **5547 passed, 0 failed, 28 skipped** (2026-10-11 저녁. 오후 `df0f7bc`는 5514 passed — 차이 33건은 추가 테스트) |
| 환경 | Python 3.10.12, Linux aarch64(지민 맥의 작업 VM). `requirements.txt`에서 `sentence-transformers`(torch)만 설치하지 않았다. API 키 변수 제거. 정민 검토(Python 3.13, 5495 passed / 12 skipped)와 환경이 달라 따로 적는다 |
| skip 28건 사유 | BM 원본 PDF 없음 14 · 실보고서 PDF 없음 12 · 이전 시연 증빙 없음 1 · 임베딩 모델 없음 1. **새 skip·xfail 없음** |

### 0.1 공개 샘플 리허설02 재집계 (2026-10-11)

> 2026-10-11 저녁, T3-1~T3-3 수정 뒤 같은 원기록으로 다시 집계했다. 시간·대상 수는 아래 표와 같고, 세션 메타데이터로 자료 구분(공개샘플·절차리허설)이 붙었다. 새 결과는 `rehearsal02_20261011/재집계_T3_20261011/`에 따로 두었고 아래 표의 옛 집계 파일은 보존한다. 원기록 sha256: worklog `5f994c93…`, 답안 `b5a3502f…`(변경 없음).

공개 테스트 PDF 3건·4문항의 **절차 점검 기록**이다. 새 BM 보강 세트의 실제 시간 비교가 아니다.

```bash
python scripts/aggregate_worktime.py data/eval/worklog/세션_리허설02_2026-10-09.csv \
  --answers data/eval/rehearsal_answers/세션_리허설02_2026-10-09.csv --read-run-dirs \
  --out rehearsal02_집계.json        # 종료 코드 0
```

| | manual (회차 1) | esgenie (회차 2) |
|---|---|---|
| 참가자 / 대상 문항 | 1명 / 4문항(답안 파일. worklog qid 4개는 그 부분 목록으로 일치) | 1명 / 4문항(답안 파일 — worklog는 `4문항일괄` 한 줄) |
| 관측 구간: 경과 / 사람 작업 / 대기 / 유휴 | 1:25:54 / 0:59:53 / 0:00:00 / 0:26:01 | 0:22:33 / 0:13:37 / 0:01:00 / 0:07:56 |
| 문항별 중앙값 | 986.5초(유효 4) | **산출 불가** — 일괄 구간만 있고 나누지 않는다 |
| 기록 완전성 | 기억 기반 1행 | **값 옮기기 구간 시각 누락 1행** |
| 재사용(`reused_from`) | 0 | 0 — 두 번째로 수행한 것만으로 재사용으로 세지 않는다 |
| 기계 시간 | 미측정 | **미측정** — 실행 폴더에 `timings.json`이 없다 |

- 같은 사람(P1)이 manual(10/9) 뒤 esgenie(10/10)를 했다. 같은 문항을 두 번째로 본 학습 효과가 섞인다.
- esgenie 실행에는 `safety_policy`가 `missing_api_key` 시연 결과로 들어 있다(답안 파일 note).
- 위 시간은 **관측 구간의 합**이다. 누락·기억 기반 행이 있어 완전한 작업시간이 아니다. **절감률·생산성 효과의 근거로 쓰지 않는다.**

### 0.2 자료 구분

| 구분 | 이 브랜치의 예 | 한 표에 합치지 않는다 |
|---|---|---|
| 공개 샘플(사람 기록) | 리허설02 | ✔ |
| 가상 BM 보강 세트 | 아직 없음(시간 비교 시작 전) | ✔ |
| 합성 프로브 | `verify_timing_cache_modes.py`, 테스트의 시각 | ✔ |
| 신규 API 실행 | `ocr_live/`(2026-10-07, 실제 호출 1건) | ✔ |
| 캐시 재생 | `ocr_live/`의 재생 1건 | ✔ |

### 0.3 추가 지시서 T3-1~T3-3 (2026-10-11 저녁) — 전후 대조

합성 fixture로 수정 전(`df0f7bc`의 `worktime.py`)과 수정 후를 같은 입력에 돌렸다. 생성 스크립트는 저장소 밖 임시 파일이며 같은 사례가 `tests/test_eval_worktime.py`의 `test_t3_*`로 고정돼 있다.

| 항목 | 입력(합성) | 수정 전 | 수정 후 |
|---|---|---|---|
| T3-1 | timings 파일 없음 | machine_total_seconds=(키 없음) · 미측정 — run_dir에 timings.json이 없다(0초로 적지 않는다) | machine_total_seconds=None · 미측정 — <tmp>/a: timings.json 없음 |
| T3-1 | 단계 행만(L0_ocr_extract=12.5) | machine_total_seconds=0.0 · 관측 | machine_total_seconds=None · 미측정 — <tmp>/b: _run_total 행이 없다 — 단계 행만 있다. 단계 합으로 전체를 만들지 않는다 |
| T3-1 | _run_total.seconds 누락 | machine_total_seconds=0.0 · 관측 | machine_total_seconds=None · 미측정 — <tmp>/c: _run_total.seconds가 없거나 유효하지 않다: None |
| T3-1 | 유효 합계 0초 | machine_total_seconds=0.0 · 관측 | machine_total_seconds=0.0 · 완전 관측 — 실행 1건 모두 _run_total 확인 |
| T3-1 | 유효 합계 500초 | machine_total_seconds=500 · 관측 | machine_total_seconds=500.0 · 완전 관측 — 실행 1건 모두 _run_total 확인 |
| T3-1 | 두 실행 중 하나만 500초 | machine_total_seconds=500 · 관측 | machine_total_seconds=None · 부분 관측 — 실행 2건 중 1건만 합계 확인 |
| T3-1 | _pipeline_total 300 + _run_total 450 | machine_total_seconds=450 · 관측 | machine_total_seconds=450.0 · 완전 관측 — 실행 1건 모두 _run_total 확인 |
| T3-1 | 같은 run_dir 3번 | machine_total_seconds=500 · 관측 | machine_total_seconds=500.0 · 완전 관측 — 실행 1건 모두 _run_total 확인 |
| T3-2 | 공개샘플·BM 두 세션(P1·esgenie·회차1, 각 1문항) | 요약 1개: 참가자 1명·대상 2 | 메타 있음 → 요약 2개: 참가자 1명·대상 1 / 참가자 1명·대상 1 / 메타 없음 → 요약 2개(세션별, 자료 미확인) |
| T3-3 | `전체` + `SYN-1;…;SYN-4` 행, 답안 파일 없음 | 대상 None · 미확인 — 일괄 구간이 있으나 대상 문항 목록(답안 파일·';' 나열)이 없다 | 전체 목록 미지정 → 대상 None · 부분 목록 — worklog 구간 qid만 있다. 전체 대상 수 미확인 (일괄 표시 행도 있다) / 세션 메타 target_qids 지정 → 대상 4 · 확인 — 전체 대상 목록(세션 메타데이터) |

**T3-1 기계 시간 읽기 계약.** 실행 전체 = `timings.json`의 `_run_total.seconds` 하나. `_pipeline_total`은 코어 파이프라인 부분 시간이라 `pipeline_total_seconds`로 따로 적고 실행 전체로 승격하지 않는다. 두 합계 행을 더하지 않고, 단계 행을 더해 합계를 만들지 않는다. 같은 run_dir은 경로 정규화 뒤 한 번만 센다. 모든 run_dir의 합계가 확인될 때만 `machine_total_seconds`를 내고(`완전 관측`), 일부만 확인되면 `machine_total_seconds=null` + `machine_partial`(관측분·관측 실행 수·누락 실행과 사유)(`부분 관측`), 하나도 없으면 `미측정`. 유효 합계 0초는 0초로 둔다. 사람 시간과 `elapsed = human + wait + idle`은 바뀌지 않는다.

**T3-2 세션 메타데이터(서식 1).** raw worklog를 고치지 않고 `--sessions <CSV>`로 연결한다. 열: `session_id,data_kind,set_id,set_version,condition,target_qids`. `data_kind` ∈ {공개샘플, 가상BM, 합성}, `condition` ∈ {신규처리, 캐시재생, 절차리허설}. 요약은 자료 구분·세트·버전·조건·방식·회차가 같은 세션끼리만 묶는다. 메타데이터가 없는 세션은 같은 자료로 추정하지 않고 세션별로 따로 둔다(`identity_status=미확인`). 같은 `session_id`가 다르게 적혔거나 값이 정한 범위 밖이면 **종료 코드 2로 멈추고 결과 파일을 쓰지 않는다**(임시 파일에 쓴 뒤 성공했을 때만 옮긴다). 선택 이유: 기존 raw CSV 서식을 바꾸지 않는 최소 변경이고, 파일명이나 session_id 차이로 세트를 추정하지 않기 위해서다.

**T3-3 대상 목록 규칙.** 세션 전체 대상 목록은 답안 파일 또는 세션 메타데이터의 `target_qids`에서만 인정한다. worklog 각 행의 qid(`;` 나열 포함)는 그 구간의 목록 — 부분 목록이다. 일괄 표시(`전체`·`…일괄`)만으로 대상 수를 만들지 않는다. 전체 목록끼리 다르거나 worklog qid가 전체 목록 밖에 있으면 `충돌`로 표시하고 대상 수를 내지 않는다. 같은 qid는 한 번만 센다. 이전 규칙(worklog `;` 나열을 대상 수로 인정)은 이 규칙으로 바뀌었고, 해당 테스트는 기대값을 바꿔 `test_semicolon_listed_qids_alone_are_a_partial_list`로 남겼다.

명령(저장소 밖에서도 PYTHONPATH 없이 동작 — 테스트로 고정):

```bash
python scripts/aggregate_worktime.py data/eval/worklog/세션_리허설02_2026-10-09.csv \
  --answers data/eval/rehearsal_answers/세션_리허설02_2026-10-09.csv \
  --sessions docs/validation/eval-timing-20261006/rehearsal02_20261011/재집계_T3_20261011/세션메타_리허설02.csv \
  [--read-run-dirs] --out <결과.json>       # 종료 코드 0. 충돌·값 오류는 2
```

## 1. 기준 커밋

| 항목 | 값 |
|---|---|
| 분기 기준(main) | `d8a0de8` |
| 계측 구현 | `6e9fd8d` (§3.1 파이프라인 단계·캐시 델타) |
| 양식·집계·증명 | `2230693` (§3.2·§3.3) |
| 실제 키 OCR 실측 | 이 디렉터리 (§3.3 ②·③) |

`esgenie/supplychain/exporters/*`·`schema.py`·`mapping.py`는 **한 줄도 고치지 않았다.**

## 2. 시간 관계식 — 중복 합산을 막는 규정

```
실제 경과시간(elapsed) = 사람 작업시간(human) + 대기시간(wait) + 유휴(idle)
```

- `human` = 사람이 적은 작업 구간의 **합집합** 길이. 겹치는 구간을 두 번 세지 않는다.
- `wait` = `처리대기` 구간의 합집합에서 **사람 작업 구간을 뺀** 값. 기다리는 동안 다른
  작업을 했으면 그 시간은 `human`에만 들어간다.
- **파이프라인 기계 시간은 `machine_*`로 따로 적고 어디에도 더하지 않는다.** 기계 시간이
  사람이 적은 대기보다 길면 `pipeline_exceeds_wait` 경고를 남긴다(값을 고치지 않는다).
- 산출 불가는 `null`이다. **0으로 적지 않는다.** 절감률 필드는 만들지 않았다.

구현·테스트: `esgenie/eval/worktime.py`, `tests/test_eval_worktime.py`(15건),
`scripts/aggregate_worktime.py`.

## 3. §3.3 통과 조건과 실제 산출물

| 조건 | 산출물 | 상태 |
|---|---|---|
| ① 계측 전후 결과 동일 | `scripts/verify_timing_noop.py` — 계측 전 커밋 worktree 덤프와 HEAD 덤프의 sha256이 **동일**(`1e871436…8f67ee`) | **통과(옛 정규화 검사기 기준).** 2026-10-11에 검사기를 강화해 다시 증명했다 — §0 |
| ② 단계 합 ≤ 전체 경과 | `scripts/verify_timing_cache_modes.py`(공개 DART 샘플 + 합성 프로브), 이 디렉터리의 실제 키 실행 | **통과** |
| ③ 캐시 재생 / 신규 처리 구분 | `ocr_live_summary.json`(실제 API), `timings.cache_{new,replay}.json`(합성 프로브) | **통과 — 실제 API로 확인.** 확인한 경로는 **비정형(L0) LLM 추출 응답 캐시** 한 가지다. **Upstage 등 다른 OCR 제공자 경로는 이 실행의 검증 범위가 아니다** |
| ④ 사람 리허설 1회 | `data/eval/worklog/세션_리허설02_2026-10-09.csv` | **(2026-10-11 갱신) 실시 — 기록 일부 누락.** §0.1. (2026-10-07 당시: 미실시) |

### 3.1 동일성 증명의 유효 범위 — 재수행하지 않은 이유

> **2026-10-07 당시 기록.** 아래 "기존 증명은 최종 코드에도 유효하다"는 이후 정정됐다. 당시 검사기는 정규화로 업무 값 차이를 가릴 수 있었고(T1), 2026-10-11 공개 샘플 덤프 일치도 제한적 참고 기록이다. 계측 전후 동일성은 통합 후보에서 확인 대기 — §0.

증명은 `2230693`에서 수행했다. 그 뒤 **계측이 들어간 모듈이 하나도 바뀌지 않았다**
(`git log -1 -- esgenie/run_timing.py esgenie/pipeline.py` → `2230693`). 이번에 더한 것은
`scripts/verify_timing_ocr_live.py`와 이 디렉터리의 기록뿐이고 제품 실행 경로를 지나지
않는다. 따라서 기존 증명은 최종 코드에도 유효하다. 계측 모듈이 바뀌면 다시 돌린다.

**계측을 끄는 플래그는 만들지 않았다.** 끈 경로는 실제 실행 경로가 아니므로 그것으로
동일성을 보이면 증명이 아니다.

## 4. 실제 API로 돌린 OCR 신규 처리 → 캐시 재생

실행:

```bash
python scripts/verify_timing_ocr_live.py \
  --env-file <키가 든 .env 경로> \
  --cache-dir /tmp/ocr_live_cache_20261007 \
  --out-dir docs/validation/eval-timing-20261006/ocr_live
```

- 입력은 저장소의 **공개 테스트 문서** `data/test_docs/safety_policy_2025.pdf`
  (라우팅 `unstructured` / `safety_minutes`)다. **BM 라벨 작업과 무관하고 BM 실제 응답·
  원본 PDF를 쓰지 않았다.** 스크립트가 저장소 밖 경로와 BM 작업 경로를 **거부한다.**
- 캐시는 **새로 만든 빈 격리 디렉터리**(`<cache-dir>/ocr`, `<cache-dir>/llm`)다. 비어
  있지 않으면 멈춘다. **기존 캐시·원본·실행 기록을 건드리지 않았다.**
- mock 폴백을 실측으로 적지 않는다 — 1단계가 mock이거나 라이브 호출이 0이면 **실패로
  멈춘다.**

결과(`ocr_live_summary.json`, 2026-10-07):

| | 신규 처리 | 캐시 재생 |
|---|---|---|
| `ocr_cache` | hit 0 / miss 1 / state `miss` | **hit 1 / miss 0 / state `hit`** |
| 실제 LLM 호출 | **1건** (성공 1 / 실패 0) | **0건** |
| 토큰 | prompt 825 / completion 910 / **합 1,735** | **`null`** (호출이 없어 사용량이 없다) |
| 전체 경과 | 14.1535 s | 0.0156 s |
| 단계 합 | 14.1535 s (단계 밖 0.000043 s) | 0.0155 s (단계 밖 0.000027 s) |

- 모델은 `gpt-4.1-mini-text`, 청크 1건. **키 값은 읽기만 하고 화면·파일·로그에 적지
  않았다.** 기록한 것은 모델명과 호출 수·토큰뿐이다.
- 두 전체 경과를 **그대로** 적었고 **절감률로 바꾸지 않았다.** 한 문서·한 청크의 1회
  측정이며 분포를 말할 수 없다.
- `ocr_cache`는 **비정형(L0) 경로의 LLM 응답만** 담는다. 정형 문서(`kepco_bill` 등)는 이
  캐시를 지나지 않아 신규/재생이 갈리지 않는다 — 스크립트가 그 경우 명시적으로 멈춘다.

### 4.1 합성 검증과 실제 API 실행의 구분

| 기록 | `evidence_kind` | 입력 |
|---|---|---|
| `ocr_live/timings.ocr_live_{new,replay}.json` | `live_api` | 공개 테스트 PDF + **실제 OpenAI 호출** |
| `timings.cache_{new,replay}.json`(스크립트 산출) | `synthetic_probe` | 합성 프로브 — 단계 이름도 `synthetic_` |
| `timings.pipeline_sample.json`(스크립트 산출) | 파이프라인 실측 | 공개 DART 샘플 |

**두 종류를 한 표에 합치지 않는다.** 합성 프로브의 초를 실제 호출 비용으로 읽지 않는다.

## 5. 응답서·출력 단계 시간

`scripts/live_numeric_rehearsal.py`가 응답서 생성·`result.json` 기록·증빙 묶음 복사·
xlsx/pdf 출력을 각각 단계로 재고 실행 폴더에 `timings.json`을 남긴다. 실행 모드(캐시
재생/신규)는 추정하지 않고 **실제 플래그에서** 적는다. **UI 타이머는 만들지 않았다.**

## 6. 남은 것 — 완료로 적지 않는다

| 항목 | 왜 남았는가 |
|---|---|
| BM 세트 실제 시간 비교 | (2026-10-11 갱신) 공개 샘플 리허설02는 실시했다(§0.1). **BM 보강 세트의 실제 시간 비교는 참가자·일정이 정해지지 않았다.** AI가 시간을 만들어 적지 않는다 |
| T1 최종 동일성 증명 | 통합 후보(PR #65 수정 1~4 포함)가 정해진 뒤 |
| BM 실제 응답을 입력으로 쓴 계측 | **라벨 확정·선행 커밋 전까지 열지 않는다.** 검증 입력에도 쓰지 않았다 |

리허설은 **절차 점검용**이다. 절감 효과를 입증한 결과로 표현하지 않는다.
