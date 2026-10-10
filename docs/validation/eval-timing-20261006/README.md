# A-2 작업시간 계측 — 검증 기록 (브랜치 `codex/eval-timing-20261006`)

작업지시서 A §3(작업시간 계측)의 구현과 검증을 한곳에 모았다. **산출물이 없는 항목은
없다고 적는다** — 사람 참가자가 필요한 항목은 미실시로 둔다.

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
| ① 계측 전후 결과 동일 | `scripts/verify_timing_noop.py` — 계측 전 커밋 worktree 덤프와 HEAD 덤프의 sha256이 **동일**(`1e871436…8f67ee`) | **통과.** 아래 §3.1 참조 |
| ② 단계 합 ≤ 전체 경과 | `scripts/verify_timing_cache_modes.py`(공개 DART 샘플 + 합성 프로브), 이 디렉터리의 실제 키 실행 | **통과** |
| ③ 캐시 재생 / 신규 처리 구분 | `ocr_live_summary.json`(실제 API), `timings.cache_{new,replay}.json`(합성 프로브) | **통과 — 실제 API로 확인.** 확인한 경로는 **비정형(L0) LLM 추출 응답 캐시** 한 가지다. **Upstage 등 다른 OCR 제공자 경로는 이 실행의 검증 범위가 아니다** |
| ④ 사람 리허설 1회 | — | **미실시.** 참가자가 필요하다 |

### 3.1 동일성 증명의 유효 범위 — 재수행하지 않은 이유

증명은 `2230693`에서 수행했다. 그 뒤 **계측이 들어간 모듈이 하나도 바뀌지 않았다**
(`git log -1 -- esgenie/run_timing.py esgenie/pipeline.py` → `2230693`). 이번에 더한 것은
`scripts/verify_timing_ocr_live.py`와 이 디렉터리의 기록뿐이고 제품 실행 경로를 지나지
않는다. 따라서 기존 증명은 최종 코드에도 유효하다. 계측 모듈이 바뀌면 다시 돌린다.

**계측을 끄는 플래그는 만들지 않았다.** 끈 경로는 실제 실행 경로가 아니므로 그것으로
동일성을 보이면 증명이 아니다.

## 4. 실제 API로 돌린 OCR 신규 처리 → 캐시 재생

실행:

```bash
PYTHONPATH=. python scripts/verify_timing_ocr_live.py \
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
| 사람 작업시간 리허설 1회(§3.3 ④) | **참가자와 실제 시간이 필요하다.** AI가 시간을 만들어 적지 않는다. `data/eval/worklog_template.csv`는 지금도 **헤더만** 있다. 기록 파일을 받으면 검증·집계한다 |
| BM 실제 응답을 입력으로 쓴 계측 | **라벨 확정·선행 커밋 전까지 열지 않는다.** 검증 입력에도 쓰지 않았다 |

리허설은 **절차 점검용**이다. 절감 효과를 입증한 결과로 표현하지 않는다.
