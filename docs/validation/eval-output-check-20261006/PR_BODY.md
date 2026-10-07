# B-1 출력 일치 검사기 — PR 본문 초안

> 이 파일은 PR 본문에 그대로 붙여 쓸 초안이다. 작업지시서 B §8("각 PR 본문에 기준 main SHA, 실행 명령, 결과 요약, 한계를 쓴다").

## 요약

화면이 읽는 `result.json`과 제출본 Excel·PDF가 **같은 실행의 같은 값**을 보이는지 문항별로 자동 판정하는 검사기를 넣는다. 작업지시서 B §2.

- 기준 main SHA: **`d8a0de8a434e4be97143f05d4694c9aa6b5ee14c`**
- 브랜치: `codex/eval-output-check-20261006`
- 코어 수정 없음 — `esgenie/pipeline.py` · `supplychain/schema.py` · `supplychain/mapping.py` · `ui/**`를 고치지 않았다. `render.py`·`exporters/*`는 **import만** 했다.

## 무엇이 들어오는가

| 파일 | 내용 |
|---|---|
| `scripts/check_output_consistency.py` | 검사기 (CLI) |
| `tests/test_check_output_consistency.py` | 단위 테스트 52개 |
| `docs/validation/eval-output-check-20261006/README.md` | 검증 기록 |
| `docs/validation/eval-output-check-20261006/tools/inject_check.py` | 주입 하네스 (20종) |
| `docs/validation/eval-output-check-20261006/tools/collect_summary.py` | 요약 수집(개인 절대 경로 제거) |
| `docs/validation/eval-output-check-20261006/*.json` | 결과 요약 3개 |

결과 원본은 `outputs/eval/`(gitignore)에 두고 커밋하지 않는다.

## 두 검사를 함께 돌린다

| 검사 | 잡는 것 |
|---|---|
| (1) 필드 검사 | 렌더러가 **필드를 빠뜨린** 경우 |
| (2) 칸 전체 일치 | 칸에 **내용이 더해지거나 바뀐** 경우 |

처음에는 (1)만 만들었고, 검토에서 세 가지를 놓친 것이 드러났다 — PDF 답변 칸의 `—`를 `7`로 바꿔도 `find`가 다음 범위 칸의 `—`를 소비해 통과했고, Excel 근거 칸의 `29.3`을 `29.35`로 바꾸거나 범위 칸 끝에 문구를 덧붙여도 부분 문자열 검사라 통과했다. 그래서 (2)를 더했다.

기대 칸 문자열은 **exporters가 실제로 쓰는 함수로** 만든다(`render.scope_line`·`note_lines`·`draft_lines`, `pdf._STATUS_STYLE`·`_build_evidence_index`, `_fonts.pdf_safe_text`, `excel._HEADER`). 따로 표기 규칙을 두면 exporters가 바뀔 때 갈린다.

수치는 `Decimal`로 비교한다 — **반올림 허용 없음**(`0.513216 ≠ 0.5`). 정규화는 공백·줄바꿈과 **숫자 사이** 천 단위 콤마만 지운다(문장의 일반 콤마는 유지).

## 실행 명령

```bash
# 출력 일치 검사
python scripts/check_output_consistency.py --run-dir <run>/<stage> --out <dir>
#   종료 코드: 0 불일치 없음 / 1 불일치 있음 / 2 입력 오류

# 주입 검사 — 검사기가 변조를 정말 잡는지
python docs/validation/eval-output-check-20261006/tools/inject_check.py \
    --run-dir <run>/<stage> --out <dir>

# 커밋할 요약만 모으기
python docs/validation/eval-output-check-20261006/tools/collect_summary.py \
    --live <dir> --inject <dir> --reference <dir> --out docs/validation/eval-output-check-20261006

# 테스트
python -m pytest tests/test_check_output_consistency.py -q
```

검사 대상 실행: `outputs/eval/20261006_live_d8a0de8/runs/LIVE_d8a0de8/{initial,followup}`
그 실행을 만든 커밋도 `d8a0de8`(`dirty: false`), 모드 `core_direct`(Upstage·LLM 실호출, 빈 캐시), 모델 `gpt-4.1-mini-2025-04-14`.

## 결과

| stage | 종료코드 | 행(JSON/Excel/PDF) | 필드 검사 | 칸 전체 일치 | **불일치** | 검사 불가 |
|---|---|---|---|---|---|---|
| initial | 0 | 48 / 48 / 48 | 129 | 384 | **0** | 48 |
| followup | 0 | 48 / 48 / 48 | 132 | 384 | **0** | 48 |

**발견한 실제 불일치: 없음.** 작업지시서 §2 검증 5(코어를 고치지 않고 보고)에 해당하는 사항이 없다.

### 주입 검사 — 20종 × 2단계 = 40건 전부 검출

| 항목 | 기대 | 실제 |
|---|---|---|
| 변경 없는 원본 | 통과(불일치 0) | **0** |
| 변경 없는 사본 재검사 | 통과(불일치 0) | **0** |
| 주입 20종 (initial) | 모두 실패 | **20/20 검출** |
| 주입 20종 (followup) | 모두 실패 | **20/20 검출** |

Excel 값·상태·범위 3종은 **실제 파일 사본**을 만들어 변조하고 검사기를 CLI로 돌려 종료 코드까지 확인한다. PDF 변조는 지시대로 **추출 텍스트 단계**에서 한다 — PDF 파일을 다시 쓰면 검사 대상이 달라진다. 변조 대상 행은 문항 ID를 적어 넣지 않고 데이터의 성질로 고른다.

세부 20종 표와 `before`/`after`는 `README.md` §5와 `injection_summary.json`에 있다.

### 테스트

`52 passed`. 외부 PDF나 개인 절대 경로에 의존하지 않는다 — 수제 `ResponseSheet`를 만들어 **실제 exporters**로 `tmp_path`에 Excel·PDF를 생성해 검사한다.

전체 `pytest`는 기준선과 같다: **22 failed / 27 skipped / 6 errors 불변**, `passed`만 새 테스트만큼 늘었다. 기준선 실패는 이번 작업과 무관한 기존 실패(UI 스모크 등)이고 고치지 않았다.

## 참고 — 옛 실행(결과 수치에는 쓰지 않음)

`LIVE_61b6167`·`INJ_61b6167`·`LIVEV_61b6167` 5개 단계에도 참고로 돌렸고 모두 불일치 0이다. `LIVE_61b6167` followup의 필드 검사 수만 133(공통 LIVE는 132)인데, 이는 `RBA-E-6`의 근거 링크 개수 차이(옛 5개 → 새 4개)와 맞는다. **보고서·PR의 결과 수치는 공통 LIVE 실행 것만 쓴다.**

## 재사용한 로직의 출처

| 가져온 것 | 원본 |
|---|---|
| `load`/`check` 분리로 메모리 사본만 바꿔 주입 검사하는 구조 | `probe_output_checker_second_review_29defa0.py` (PR #71 재검토 하네스) |
| `Bundle` 패턴 → `OutputBundle`, Excel 읽기 관례, `pdf_text` | `output/validation/numeric_scope_output_20261005/tools/check_outputs.py:75-93`, `:125-129`, `:99-104` |
| PDF 글리프 보정 | 처음엔 같은 파일 `:69-73`의 `glyph_flat`을 참고했으나 최종본은 `exporters/_fonts.pdf_safe_text`를 직접 쓴다 |

## 한계

- **검사 불가는 `badge`의 이모지 부분(PDF) 하나뿐**이다 — `pdf.py:291`이 `_STATUS_STYLE` 라벨만 그려 PDF에 이모지가 없다. Excel은 이모지까지 완전 일치로 본다.
- **검사 범위 밖**: Excel의 다른 시트(`증빙 체크리스트`·`보완·검토`·`ISSB 보완`), PDF의 응답표 밖 영역(체크리스트 표·보완검토·ISSB·증빙 부록), 보고서 본문·데이터시트.
- **`evidence_links[].page`는 직접 검사하지 않는다.** 다만 근거 칸 전체 일치에 `{파일명} p.{page+1}`가 들어가 **간접적으로 대조된다**(쪽 번호를 바꾸면 검출된다 — 주입 `excel_note_page`).
- **실제 데이터로 못 본 경로 2개**: `(이어서)` 행과 `status == "draft_ready"` 행이 공통 LIVE 두 실행에 0건이다. 둘 다 단위 테스트에서 수제 시트 + 실제 exporters로 재현해 검증했다(`(이어서)`는 블록이 실제로 2개로 쪼개짐까지 확인).
- **이 검사기는 '세 출력이 서로 같은가'만 본다.** 값이 원문과 맞는지(사실 여부)는 `check_outputs.py`가 정답표와 대조한다. 두 검사는 서로를 대체하지 않는다.

## B-2 작업 시 주의

`run_info`의 **PDF 바닥글이 응답표 영역 페이지에 그려지면 마지막 문항 블록의 '블록 끝 잔여'로 잡힐 가능성**이 있다(Excel 메타 칸은 응답표 칸이 아니라 영향 없을 것으로 보인다). **검사기를 미리 바꾸지 않았다** — B-2에서 재실행해 확인하고 실제로 잡히면 그때 처리한다. 지금 추측으로 예외를 넣으면 진짜 불일치도 함께 가려진다.
