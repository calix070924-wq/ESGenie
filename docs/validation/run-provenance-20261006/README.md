# 실행 출처 표시 (B-2) — 검증 기록 2026-10-07

출력물에 **어느 코드로 만들었는지**와 **신규 AI 처리인지 캐시 재생인지**를 남긴다. 작업지시서 B §3.

- 구현: `esgenie/run_info.py`, `esgenie/supplychain/exporters/{excel,pdf}.py`, `scripts/live_numeric_rehearsal.py`
- 계약: `docs/UI연결용_수치범위_출력계약_2026-10-05.md` §3-1
- 검증: 이 폴더 — `CAPTURES.md`(캡처), `run_provenance_summary.json`(요약), `PR_BODY.md`
- 테스트: `tests/test_run_info.py`(27) · `test_run_info_export_identity.py`(6) · `test_check_output_consistency_run_info.py`(18)

## 1. 기준

| 항목 | 값 |
|---|---|
| 기준 main SHA | `d8a0de8a434e4be97143f05d4694c9aa6b5ee14c` |
| 브랜치 | `codex/run-provenance-20261006` |
| **B-1 브랜치 merge 커밋** | **`e5c2966`** — `Merge remote-tracking branch 'origin/codex/eval-output-check-20261006' into codex/run-provenance-20261006` (충돌 없음, rebase 미사용) |
| 입력 세트 | 정상 5건 보강 (`hanwool_bm_normal5_20261007_v1`) — initial 17건 / followup 18건 |
| 공통 LIVE 실행 | `<OUTPUTS_EVAL>/20261007_live_n5_d8a0de8/runs/LIVE_n5_d8a0de8` |

### B-1 커밋이 이 PR에 함께 보인다

B-2가 B-1 검사기를 고쳐야 해서(§5) B-1 브랜치를 merge했다. **PR #73이 main에 병합되기 전까지** 이 PR의 커밋 목록에 B-1 커밋 3개가 함께 나타난다:

```
cbe27bd docs(validation): B-1 출력 일치 검사 실데이터 검증과 주입 하네스 20종
f0f5737 fix(eval): 출력 일치 검사에 칸 전체 일치를 더해 칸에 더해진 내용을 잡기
9d55114 feat(eval): 응답서 JSON·Excel·PDF의 문항별 출력 일치 검사기 추가
```

#73이 먼저 병합되면 사라진다. B-2 고유 커밋은 `cf2cb8c`·`f92f9e2`·`d115253`·`ac6418d`와 그 뒤의 것들이다.

## 2. 처리 방식 판정 — 작업지시서 §3과 다른 점

**§3의 판정식에 Upstage 항을 더했다.** §3은 LLM·OCR 두 캐시만 보지만, 그대로 쓰면 **신규 처리와 캐시 재생이 뒤집힌다.**

`ocr_cache`는 Upstage 응답 캐시가 아니라 **Upstage 결과를 입력으로 받는 VLM 보정 LLM 응답 캐시**다. 코드 순서가 그것을 강제한다:

| `esgenie/ssot/ocr_router.py` | 하는 일 |
|---|---|
| `:1917` `extract_unstructured` | PDF 텍스트를 뽑고, 텍스트 레이어가 없으면 |
| **`:1941`** | `_call_upstage_dp(...)` — **Upstage 호출** |
| `:1977` `_extract_unstructured_text` | 그 텍스트를 받아 |
| **`:2034`** | 그것을 `llm_input`에 넣어 **OCR 캐시 키를 만들고** |
| `:2042` | `ocr_cache.load_response(key)` 조회 |

Upstage 결과가 캐시 키의 입력이므로 순서가 바뀔 수 없다. 그래서 **OCR 캐시가 전부 적중해도 Upstage는 매번 실제로 불린다.** 실측(새 공통 LIVE followup): **OCR 적중 22 · 미스 1인데 Upstage 요청 4건 성공.** Upstage 자체의 재생은 `ocr_cache`가 아니라 `live_numeric_rehearsal.py`의 `UpstageTape`(record/replay)가 맡는다.

새 판정:

```
캐시 재생 = LLM live_calls == 0 ∧ OCR misses == 0 ∧ Upstage 실요청 == 0
신규 처리 = LLM hits == 0      ∧ OCR hits == 0   ∧ Upstage 재생 아님
그 밖     = 혼합
```

Upstage 실요청 수를 **모르면(`upstage.counted == false`) `캐시 재생`으로 판정하지 않는다** — 모르는 것을 재생이라고 말하지 않기 위해서다. 나머지 두 조건으로 `신규 처리` 판정은 허용한다. 아무것도 부르지 않은 실행(모두 0)은 ①에 먼저 걸려 `캐시 재생`이 된다.

이 판정이 실데이터에서 둘을 제대로 가른다는 것을 §4가 보인다.

## 3. 실행 명령

두 실행 모두 **`--code-path ../ESGenie-B2`**(`run_info`가 들어가야 한다), 새 증빙 세트, `--stage initial`.

```bash
cd ../ESGenie-B2
PACK="<DATA_PACK>/한울정밀_촬영세트_정상5건보강_20261007"
SETOPT='--initial-dir 01_처음업로드_17건 --followup-dir 02_교육보완때추가_1건
        --manifest 00_안내와정답_업로드금지/구성_원본대조_목록.json'

# (A) 캐시 재생 — 유료 호출 없음
#     새 공통 LIVE의 캐시를 '복사'한 폴더 + 그 실행이 기록한 Upstage 원시 응답
cp -r <OUTPUTS_EVAL>/20261007_live_n5_d8a0de8/caches/LIVE_n5_d8a0de8 \
      <OUTPUTS_EVAL>/20261007_p22/replay/caches/REPLAY_n5
python scripts/live_numeric_rehearsal.py core --stage initial --export \
  --replay-upstage <OUTPUTS_EVAL>/20261007_live_n5_d8a0de8/runs/LIVE_n5_d8a0de8/initial/raw_upstage \
  --run-id REPLAY_n5 --code-path ../ESGenie-B2 --env-file <REPO>/.env \
  --cache-dir <OUTPUTS_EVAL>/20261007_p22/replay/caches/REPLAY_n5 \
  --run-dir   <OUTPUTS_EVAL>/20261007_p22/replay/runs/REPLAY_n5 \
  --pack-dir "$PACK" $SETOPT
# EXIT 0, 130.1초

# (B) 신규 처리 — 유료 실호출 1회
python scripts/live_numeric_rehearsal.py core --stage initial --export --record-upstage \
  --run-id FRESH_n5 --code-path ../ESGenie-B2 --env-file <REPO>/.env \
  --cache-dir <OUTPUTS_EVAL>/20261007_p22/fresh/caches/FRESH_n5 \
  --run-dir   <OUTPUTS_EVAL>/20261007_p22/fresh/runs/FRESH_n5 \
  --pack-dir "$PACK" $SETOPT
# EXIT 0, 510.1초
```

**원본 캐시에는 쓰기가 일어나지 않았다** — 사본만 썼고, 재생 실행 전후 캐시 파일 수가 `{ocr 23, llm 34}`로 같다.

## 4. 결과 — 통과 조건 2

### 4.1 판정

| | 캐시 재생 (`REPLAY_n5`) | 신규 처리 (`FRESH_n5`) |
|---|---|---|
| **`processing.label`** | **`캐시 재생`** | **`신규 처리`** |
| LLM hits / misses / live_calls | **7 / 0 / 0** | **0 / 28 / 28** |
| LLM `from_snapshot` | true | true |
| OCR hits / misses / mode | **22 / 0 / `hit`** | **0 / 22 / `miss`** |
| **Upstage live_requests / replay / counted** | **0 / true / true** | **4 / false / true** |
| Upstage tape | `replayed` 4건 | `recorded` 4건 |
| `run_stats.upstage.requests` | 4 (래퍼가 센 함수 호출 — 네트워크 요청은 0) | 4 |
| mode | `core_replay` | `core_direct` |
| `commit_sha` / `dirty` | `ac6418d661d7…` / `false` | 같음 |
| 실제 응답 model | — (실호출 0) | `gpt-4.1-mini-2025-04-14` (28건) |
| 토큰 | 0 | prompt 54,434 / completion 26,344 / **total 80,778** |
| 실행 시간 | 130.1초 | 510.1초 |

**Upstage 항이 없으면 재생 실행도 '캐시 재생'으로는 맞지만**, OCR 캐시가 적중한 상태로 Upstage를 실제로 부른 실행(새 공통 LIVE followup 같은 경우)이 '캐시 재생'으로 잘못 찍힌다 — §2의 근거.

`run_info` 원문 2건은 `run_provenance_summary.json`에 그대로 있다.

### 4.2 표시 위치 캡처

`CAPTURES.md` — Excel `실행정보` 시트 20행(두 실행 각각)과 PDF 캡처 PNG 6장.

| | 캐시 재생 | 신규 처리 |
|---|---|---|
| PDF 쪽수 | 23 | 23 |
| 바닥글이 모든 쪽에 정확히 1번 | **예** (쪽별 전부 1) | **예** |
| 요약 줄이 1쪽에만 | **예** | **예** |
| Excel `실행정보` 행 수 | 20 | 20 |

PDF 1쪽 하단 실측(신규 처리):
```
실행 정보: 코드 ac6418d | 미커밋 변경 없음 | 생성 2026-10-07T16:45:11+09:00 | 신규 처리 |
           LLM 실호출 28 · 캐시 적중 0 | OCR 미스 22 · 적중 0 | Upstage 실요청 4 |
           모델 gpt-4.1-mini / document-parse
실행 정보: 코드 ac6418d · 신규 처리
```
마지막 쪽(캐시 재생): `실행 정보: 코드 ac6418d · 캐시 재생`

## 5. 결과 — 통과 조건 1·3

### 5.1 `run_info=None` 동일성 (조건 1)

단위 테스트(`test_run_info_export_identity.py` 6개)는 exporters를 고치기 **전에** 떠 둔 기준값(`tests/fixtures/run_info_baseline.json`)과 대조한다.

**실데이터로 한 번 더 확인했다** — 새 공통 LIVE의 `result.json`에서 sheet를 되살려 B-2 exporters로 **`run_info` 없이** 다시 내보내고, 원래 파일(d8a0de8 코드가 만든 것)과 대조했다:

| stage | Excel 시트 | Excel 셀 수 | **셀 차이** | PDF 쪽수 | **쪽 텍스트 차이** | 동일 |
|---|---|---|---|---|---|---|
| initial | `응답서`·`증빙 체크리스트`·`보완·검토`·`ISSB 보완` (이름 동일) | 350 / 218 / 33 / 10 = **611** | **0건** | 23 → 23 | **0건** | **예** |
| followup | 같음 | **611** | **0건** | 23 → 23 | **0건** | **예** |

`실행정보` 시트는 생기지 않았다. 원본은 읽기만 했다(새 폴더에 내보내고 `evidence_base_dir`만 원본 응답서 폴더로 가리켜 `[E#]` 맵을 같게 했다).

### 5.2 B-1 검사기 (조건 3)

B-2 브랜치의 `scripts/check_output_consistency.py`(B-1 merge + 바닥글 검사 반영본):

| 실행 | 종료코드 | **불일치** | 칸 전체 일치 불일치 | 필드 검사 | 칸 전체 일치 | `run_info_present` | **도장 줄 제거** | Excel 실행정보 행 |
|---|---|---|---|---|---|---|---|---|
| 신규 처리 | **0** | **0** | 0 | 150 | 384 | true | **24** | 20 |
| 캐시 재생 | **0** | **0** | 0 | 157 | 384 | true | **24** | 20 |

**도장 줄 24개 = 바닥글 23쪽 + 1쪽 요약 1줄** — 바닥글 검사(`run_info_footer`)와 Excel 검사(`run_info_excel`)가 실데이터에서 실제로 돌았고 통과했다.

### 5.3 주입 하네스 20종 (B-1 회귀)

신규 처리 실행(`FRESH_n5`)에 B-1 주입 하네스를 돌렸다:

| 항목 | 기대 | 실제 |
|---|---|---|
| 변경 없는 원본 | 통과(불일치 0) | **0** |
| 변경 없는 사본 재검사 | 통과(불일치 0) | **0** |
| 주입 20종 | 모두 실패(검출) | **20/20 검출** |

**첫 실행에서 `excel_note_page` 1건이 '적용 실패'였고 하네스를 고쳤다**:

- **원인**: 하네스가 쪽 표기를 `p.1`로 **적어 넣고** 있었다. 새 세트에서 그 자리의 근거 문서가 바뀌어 쪽이 `p.2`가 되자 대상 문구를 찾지 못했다(`applied: false`, `reason: 대상 행·문구를 찾지 못했다`). **검사기가 놓친 것이 아니다.**
- **고친 것**: `excel_bump_page`를 새로 만들어 실제 칸에서 `p.(\d+)` 패턴을 찾아 `p.N → p.N0`으로 늘린다. 원래 값이 부분 문자열로 남으므로 '포함 검사로는 통과, 칸 전체 일치로만 잡히는' 변조를 그대로 재현한다. 세트가 바뀌어도 돈다.
- **결과**: 20/20, 종료 코드 0. (P1-2에서 `excel_swap_answers`를 같은 이유로 고친 것과 같은 성질이다 — 하네스의 데이터 의존.)

## 6. 한계 · 확인 못 한 것

- **`PipelineOutput.timings`가 아직 없다.** `esgenie/pipeline.py:54-75`의 필드 목록에 그 필드가 없다(A-2 작업). `getattr(output, "timings", None)`으로 읽고 없으면 `run_info`에 넣지 않는다 — **두 실행 모두 `timings` 키가 없다.** A-2가 넣은 뒤 실제 구조와 맞는지 확인해야 한다.
- **`anthropic` 키가 없다**(`keys.anthropic = "없음"`). Anthropic 공급자 경로의 `run_info`는 확인하지 못했다.
- **`run_stats.upstage.requests`와 `run_info.processing.upstage.live_requests`가 다를 수 있다.** 전자는 `UpstageCounter`가 센 **함수 호출 수**(재생 모드에서도 함수는 불린다), 후자는 **네트워크 실요청 수**다. 재생 실행에서 4 vs 0으로 갈린다 — 의도된 차이이고 `run_stats`를 바꾸지 않았다.
- **캐시 재생을 `followup`에서는 확인하지 않았다.** 지시가 `initial` 1회였다.
- **화면 표시는 PR #65에서 붙인다** — 이 PR은 UI를 건드리지 않는다. `from_snapshot`·`upstage.counted`가 `false`일 때의 화면 문구는 계약 §3-1에만 적혀 있고 구현을 확인하지 못했다.
- Excel `실행정보` 시트를 **이미지로 렌더하지 못했다** — `requirements.txt`에 Excel 렌더 도구가 없다(새 의존성을 넣지 않는다). 대신 `CAPTURES.md`에 시트의 (항목, 값) 20행을 표로 그대로 실었다.
- 새 증빙 세트에 대한 **ESGenie 판정의 정확성은 보지 않았다**(정답 미열람, 독립 라벨링 진행 중).

## 7. 이 폴더의 파일

| 파일 | 내용 |
|---|---|
| `README.md` | 이 문서 |
| `PR_BODY.md` | PR 본문 초안 |
| `CAPTURES.md` | Excel `실행정보` 시트 표 + PDF 캡처 |
| `pdf_{fresh,replay}_page1.png` | PDF 1쪽 전체 |
| `pdf_{fresh,replay}_footer.png` | 1쪽 하단(요약 줄 + 바닥글) |
| `pdf_{fresh,replay}_footer_lastpage.png` | 마지막 쪽 바닥글 |
| `run_provenance_summary.json` | `run_info` 원문 2건 · 실행 통계 · 검사 요약 · 동일성 · 주입 20종 |

`outputs/eval/`의 결과 원본(실행 폴더·캐시·검사 JSON)은 **커밋하지 않는다**.
