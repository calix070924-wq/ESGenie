# 출력 일치 검사 (B-1) — 검증 기록 2026-10-06

화면이 읽는 `result.json`과 제출본 Excel·PDF가 **같은 실행의 같은 값**을 보이는지 문항별로 자동 판정한다. 작업지시서 B §2.

- 검사기: `scripts/check_output_consistency.py`
- 주입 하네스: `docs/validation/eval-output-check-20261006/tools/inject_check.py`
- 요약 수집: `docs/validation/eval-output-check-20261006/tools/collect_summary.py`
- 단위 테스트: `tests/test_check_output_consistency.py` (52개)
- 기준 필드: `docs/UI연결용_수치범위_출력계약_2026-10-05.md` §2

## 1. 기준

| 항목 | 값 |
|---|---|
| 기준 main SHA | `d8a0de8a434e4be97143f05d4694c9aa6b5ee14c` |
| 브랜치 | `codex/eval-output-check-20261006` |
| 검사 대상 실행 | `<OUTPUTS_EVAL>/20261006_live_d8a0de8/runs/LIVE_d8a0de8/{initial,followup}` |
| 그 실행을 만든 코드 커밋 | `d8a0de8a434e4be97143f05d4694c9aa6b5ee14c` (`dirty: false`) |
| 그 실행의 모드 | `core_direct` — Upstage·LLM 모두 실호출, 빈 캐시(신규 처리) |
| 그 실행의 LLM 모델 | `gpt-4.1-mini-2025-04-14` |

`<OUTPUTS_EVAL>`은 모든 worktree가 공유하는 결과 루트(`outputs/eval/`, gitignore 대상)다. 결과 원본은 커밋하지 않고 **요약 JSON만** 이 폴더에 둔다.

## 2. 무엇을 검사하는가

두 검사를 **함께** 돌린다. 하나만으로는 구멍이 난다.

| 검사 | 잡는 것 | 비교 수(initial) |
|---|---|---|
| (1) 필드 검사 | 렌더러가 **필드를 빠뜨린** 경우 | 129 |
| (2) 칸 전체 일치 | 칸에 **내용이 더해지거나 바뀐** 경우 | 384 |

대조 필드 6개: `display_value` · `badge` · `comparison_label` · `review_note` · `boundary_label` · `evidence_links[].file_name`

기대 칸 문자열은 **exporters가 실제로 쓰는 함수로** 만든다 — 따로 표기 규칙을 두면 exporters가 바뀔 때 갈린다.

| 칸 | Excel (`exporters/excel.py:123-135`) | PDF (`exporters/pdf.py:291-310`) |
|---|---|---|
| D 답변 | `draft_ready`면 `"\n".join(draft_lines(a))`, 아니면 `a.display_value` | `draft_ready`면 `"<br/>".join(…)`, 아니면 `a.display_value` |
| E 범위 | `render.scope_line(a) or "—"` | 같음 |
| F 신뢰 | `a.badge` (이모지 포함) | `pdf._STATUS_STYLE[a.status][0]` (이모지 없음) |
| G 근거 | `"\n".join(render.note_lines(a)).strip()` | `"<br/>".join(render.note_lines(a, fig_map=…)) or "—"` |

근거 칸만 두 쪽이 다르다 — PDF는 증빙 부록이 있을 때 링크마다 ` → [E#]`를 덧붙인다(`render.py:192-193`).

### 정규화

- 공백·줄바꿈 제거, **숫자 사이** 천 단위 콤마 제거(`(?<=\d),(?=\d\d\d)`). 문장의 일반 콤마는 유지한다.
- PDF 쪽만 `exporters/_fonts.pdf_safe_text`를 추가로 적용한다(번들 폰트에 없는 `→ ÷ × ↔ ≈ …` 14개를 ASCII로, 이모지 제거).
- 수치는 `Decimal`로 비교한다. **반올림 허용 없음** — `0.513216 ≠ 0.5`. 문자열 유사도는 쓰지 않는다.

### 행 맞추기

- Excel: 1열이 `qid`. `▌`로 시작하는 섹션 그룹 헤더 행은 제외.
- PDF: **JSON의 qid 목록을 긴 것부터** 찾는다. PDF는 표 셀을 폭에 맞춰 줄바꿈해 `RBA-C-4-E-6-2`가 쪼개지고, `RBA-C-4-E-6`처럼 접두가 겹치는 qid가 실제로 있다. 정규식으로는 가를 수 없다.
- 응답표 영역은 `제출 전 증빙 체크리스트` 앞까지. 체크리스트 표에도 문항 ID 열이 있고, 증빙 부록은 `{qid} … -> {display_value}`를 다시 싣는다. (`ISSB`는 문항 본문에도 나와 경계로 쓸 수 없다.)
- `(이어서)` 행(근거/비고가 `_ROW_LIMIT = 420pt`를 넘어 쪼개진 행, `pdf.py:299-316`)은 같은 qid 블록으로 **이어 붙인다**.
- 블록 뒤에 붙는 섹션 그룹 헤더와 쪽마다 반복되는 표 머리글(`repeatRows=1`)은 떼어낸다 — 응답 칸의 내용이 아니다.

## 3. 실행 명령

```bash
# (1) 출력 일치 검사
python scripts/check_output_consistency.py \
  --run-dir <OUTPUTS_EVAL>/20261006_live_d8a0de8/runs/LIVE_d8a0de8/initial \
  --out     <OUTPUTS_EVAL>/20261006_b1_live/initial
python scripts/check_output_consistency.py \
  --run-dir <OUTPUTS_EVAL>/20261006_live_d8a0de8/runs/LIVE_d8a0de8/followup \
  --out     <OUTPUTS_EVAL>/20261006_b1_live/followup

# (2) 주입 검사 — 검사기가 변조를 정말 잡는지
python docs/validation/eval-output-check-20261006/tools/inject_check.py \
  --run-dir <OUTPUTS_EVAL>/20261006_live_d8a0de8/runs/LIVE_d8a0de8/initial \
  --out     <OUTPUTS_EVAL>/20261006_b1_inject/initial
python docs/validation/eval-output-check-20261006/tools/inject_check.py \
  --run-dir <OUTPUTS_EVAL>/20261006_live_d8a0de8/runs/LIVE_d8a0de8/followup \
  --out     <OUTPUTS_EVAL>/20261006_b1_inject/followup

# (3) 커밋할 요약만 모으기(개인 절대 경로 → 자리표시자)
python docs/validation/eval-output-check-20261006/tools/collect_summary.py \
  --live      <OUTPUTS_EVAL>/20261006_b1_live \
  --inject    <OUTPUTS_EVAL>/20261006_b1_inject \
  --reference <OUTPUTS_EVAL>/20261006_b1_live/_reference_old_runs \
  --out       docs/validation/eval-output-check-20261006

# (4) 단위 테스트
python -m pytest tests/test_check_output_consistency.py -q
```

검사기 종료 코드: **0** 불일치 없음 / **1** 불일치 있음 / **2** 입력 오류.
주입 하네스 종료 코드: **0** 모두 기대대로 / **1** 기대와 다른 주입 있음 / **2** 입력 오류.

## 4. 결과 — 공통 LIVE 실행

| stage | 종료코드 | 행(JSON/Excel/PDF) | 필드 검사 | 칸 전체 일치 | **불일치** | 검사 불가 | `[E#]` | 이어지는 행 |
|---|---|---|---|---|---|---|---|---|
| initial | **0** | 48 / 48 / 48 | 129 | 384 | **0** | 48 | 4 | 0 |
| followup | **0** | 48 / 48 / 48 | 132 | 384 | **0** | 48 | 4 | 0 |

**실제 불일치는 발견되지 않았다.** 따라서 작업지시서 §2 검증 5(코어를 고치지 않고 정민에게 보고)에 해당하는 사항이 없다.

입력 파일의 sha256은 `consistency_summary.json`에 있다.

## 5. 결과 — 주입 검사 (20종 × 2단계 = 40건)

변조 대상 행은 문항 ID를 적어 넣지 않고 **데이터의 성질로 고른다**(소수 자리가 긴 수치 행, 비율 행, 답변·범위가 모두 빈 행, 근거가 있는 행, 답변 값이 서로 다른 두 행). 고른 행은 `inject_log.json`의 `picked_rows`에 남는다.

| 항목 | 기대 | initial | followup |
|---|---|---|---|
| 변경 없는 원본 | 통과(불일치 0) | **불일치 0** | **불일치 0** |
| 변경 없는 사본 재검사 | 통과(불일치 0) | **불일치 0** | **불일치 0** |
| 주입 20종 | 모두 실패(검출) | **20/20 검출** | **20/20 검출** |

### 주입 20종 (initial 기준)

| # | 이름 | 방식 | 바꾼 것 | 불일치 | 검출 |
|---:|---|---|---|---:|---|
| 1 | `excel_file_value` | **실제 파일 사본** | Excel 답변 칸 값 1개 | 2 | 예 |
| 2 | `excel_file_status` | **실제 파일 사본** | Excel 신뢰 칸 상태 1개 | 2 | 예 |
| 3 | `excel_file_scope` | **실제 파일 사본** | Excel 범위 문구 1개 | 3 | 예 |
| 4 | `excel_note_strip` | 메모리 사본 | 근거/비고에서 검토 사유 제거 | 3 | 예 |
| 5 | `excel_note_number` | 메모리 사본 | 근거/비고 수치 `29.3`→`29.35` | 1 | 예 |
| 6 | `excel_note_page` | 메모리 사본 | 근거 쪽 번호 `p.1`→`p.10` | 1 | 예 |
| 7 | `excel_scope_append` | 메모리 사본 | 범위 칸 끝에 ` · 전사 · 연간` | 1 | 예 |
| 8 | `excel_blank_scope_add` | 메모리 사본 | 비교 판정이 빈 행의 범위 칸에 문구 추가 | 1 | 예 |
| 9 | `excel_badge_blank_row` | 메모리 사본 | 다른 행의 신뢰 칸 변경 | 2 | 예 |
| 10 | `excel_swap_answers` | 메모리 사본 | 값이 서로 다른 두 행의 답변 칸 맞바꿈 | 4 | 예 |
| 11 | `pdf_value` | 추출 텍스트 | 답변 값 `0.513216`→`0.5` | 2 | 예 |
| 12 | `pdf_status` | 추출 텍스트 | 신뢰 `자가신고`→`증빙검증` | 6 | 예 |
| 13 | `pdf_scope` | 추출 텍스트 | 범위 `사용전력량`→`전사합산` | 6 | 예 |
| 14 | `pdf_pct_far` | 추출 텍스트 | 비율 `29.3`→`92` | 3 | 예 |
| 15 | `pdf_pct_near` | 추출 텍스트 | 비율 `29.3`→`29.8` | 3 | 예 |
| 16 | `pdf_evidence_rename` | 추출 텍스트 | 근거 파일명 변경 | 4 | 예 |
| 17 | `pdf_evidence_drop` | 추출 텍스트 | 근거 파일명 삭제 | 4 | 예 |
| 18 | `pdf_answer_dash` | 추출 텍스트 | 답변 칸 `—`→`7` | 1 | 예 |
| 19 | `pdf_body_tail_add` | 추출 텍스트 | 응답표 영역 끝에 문구 추가 | 1 | 예 |
| 20 | `pdf_header_in_row` | 추출 텍스트 | 행 안에 표 머리글형 문구 삽입 | 3 | 예 |

1~3은 작업지시서 §2 검증 2(Excel 사본 3종)를 **실제 파일 사본**으로 이행한 것이다 — `--out` 아래에 run 폴더를 복사해 `openpyxl`로 셀을 바꾸고, 검사기를 그 폴더에 CLI로 돌려 종료 코드까지 확인한다. 11~13은 검증 3(PDF 같은 3종)이며 지시대로 **추출 텍스트 단계**에서 변조한다 — PDF 파일을 다시 쓰면 검사 대상이 달라진다.

어느 셀·문구를 무엇으로 바꿨는지는 `inject_log.json` / `injection_summary.json`의 `target`·`before`·`after`에 있다.

## 6. 참고 — 옛 실행(PR #71 최종 실행)

기대값을 미리 정하지 않고 나온 그대로 적는다. **보고서·PR의 결과 수치는 공통 LIVE 실행 것만 쓴다.**

| 실행 | stage | 불일치 | 필드 검사 | 칸 전체 일치 |
|---|---|---|---|---|
| `LIVE_61b6167` | initial | 0 | 129 | 384 |
| `LIVE_61b6167` | followup | 0 | **133** | 384 |
| `INJ_61b6167` | initial | 0 | 129 | 384 |
| `INJ_61b6167` | followup | 0 | 133 | 384 |
| `LIVEV_61b6167` | initial | 0 | 129 | 384 |

`LIVE_61b6167` followup의 필드 검사 수가 133인데 공통 LIVE(`LIVE_d8a0de8`) followup은 132다. 차이 1건은 P1-0에서 기록한 `RBA-E-6`의 근거 링크 개수 차이(옛 5개 → 새 4개)와 맞는다.

`INJ_61b6167`은 **파이프라인 입력에 변형 증빙을 넣은 실행**이다. 출력물 사이의 일관성(JSON↔Excel↔PDF)은 그와 무관하게 유지되는 것이 맞으므로 불일치 0이 모순이 아니다. 이 검사기는 '세 출력이 서로 같은가'를 보고 '값이 사실인가'를 보지 않는다 — 후자는 `output/validation/numeric_scope_output_20261005/tools/check_outputs.py`가 본다.

## 7. 재사용한 로직의 출처

| 가져온 것 | 원본 |
|---|---|
| `load`/`check` 분리로 메모리 사본만 바꿔 주입 검사하는 구조 | `probe_output_checker_second_review_29defa0.py` (PR #71 재검토 하네스) |
| `Bundle` 데이터클래스 패턴 → `OutputBundle` | `output/validation/numeric_scope_output_20261005/tools/check_outputs.py:75-93` |
| Excel 읽기 관례(시트명 `응답서`, `min_row=5`, 1열을 qid로) | 같은 파일 `:125-129`. 단 그룹 헤더 행은 우리가 명시적으로 제외 |
| `pdf_text` → `extract_pdf_text` | 같은 파일 `:99-104`. 페이지 번호가 필요해 페이지별 리스트로 변형 |
| PDF 글리프 보정 | 처음엔 같은 파일 `:69-73`의 `glyph_flat`을 참고했으나, **최종본은 `esgenie/supplychain/exporters/_fonts.pdf_safe_text`를 직접 쓴다** — 실제 렌더 함수라 표기가 갈리지 않는다 |
| 기대 칸 문자열 | `esgenie/supplychain/render.py`(`scope_line`·`note_lines`·`draft_lines`), `exporters/pdf.py`(`_STATUS_STYLE`·`_build_evidence_index`), `exporters/excel.py`(`_HEADER`) — 모두 **import만** 하고 고치지 않았다 |
| `COMPARISON_LABEL` | `esgenie/ssot/boundary.py:670-675` (라벨 문자열 하드코딩 회피용으로 확인) |

이 PR은 `esgenie/pipeline.py`·`esgenie/supplychain/schema.py`·`esgenie/supplychain/mapping.py`·`esgenie/ui/**`를 **고치지 않는다**.

## 8. 한계

### 검사 불가 — 구조상 비교할 수 없는 것

| 항목 | 이유 | 행 수(48행 기준) |
|---|---|---|
| `badge`의 **이모지 부분**(PDF) | `pdf.py:291`이 `_STATUS_STYLE` 라벨만 그린다 — PDF에 이모지가 애초에 없다. Excel은 이모지까지 완전 일치로 검사한다 | 48 |

이것이 유일하다. "JSON 값이 빈 필드"는 이제 **칸 전체 일치가 '그 칸에 아무것도 더 찍히지 않았는지'로 검사**하므로 검사 불가가 아니다(이전 판에서는 건너뛰어 214건이 사각지대였다).

### 검사 범위 밖

- **Excel의 다른 시트**: `증빙 체크리스트` · `보완·검토` · `ISSB 보완`. 대조 필드가 `응답서` 시트에 있어 범위를 거기로 한정했다.
- **PDF의 응답표 밖 영역**: `제출 전 증빙 체크리스트` 표, `보완·검토`, `ISSB 보완`, `증빙 부록 — 원본 대조`. 응답표 영역(`제출 전 증빙 체크리스트` 앞)만 본다.
- **`evidence_links[].page`**: 단계 지시의 대조 필드 목록에 없어 직접 검사하지 않는다. 다만 **근거 칸 전체 일치에 `{파일명} p.{page+1}` 표기가 그대로 들어가므로 간접적으로는 대조된다**(쪽 번호를 바꾸면 `excel_note_page` 주입처럼 검출된다).
- 출력 계약 §2의 나머지 필드(`self_reports[]` · `comparisons[]` · `flags[]` · `confidence_flags` · `scope_notes` · `boundary` · `completeness` · `comparison` · `comparison_reason` · `reference_links[]`). 다만 `flags`·`rationale`·`reference_links`는 `note_lines`를 통해 **근거 칸 전체 일치에 포함**된다.
- 보고서 본문(`.md`/`.pdf`)과 데이터시트(`ESG_DataSheet_*.xlsx`). 이쪽은 `check_outputs.py`가 본다.

### 실제 데이터로 확인하지 못한 경로

- **`(이어서)` 행**: 공통 LIVE 두 실행 모두 0회다(근거/비고가 `_ROW_LIMIT = 420pt`를 넘는 행이 없었다). 단위 테스트에서 긴 `flags`를 가진 시트를 만들어 **실제 exporters로 블록이 2개 이상으로 쪼개짐을 확인하고**, 정상이면 칸 전체 일치까지 통과하고 이어지는 조각을 변조하면 검출되는 것까지 검증했다(`test_continued_row_*`).
- **`status == "draft_ready"` 행**: 공통 LIVE 두 실행 모두 0건. 단위 테스트에서 `draft_text`가 있는 시트를 만들어 답변 칸 기대값이 `draft_lines`임과 Excel·PDF 양쪽 변조가 검출되는 것을 확인했다(`test_draft_ready_*`).

### 이 검사기가 말하지 않는 것

**세 출력이 서로 같은가**만 본다. 값이 원문과 맞는지(사실 여부)는 보지 않는다 — 그쪽은 `output/validation/numeric_scope_output_20261005/tools/check_outputs.py`가 정답표와 대조한다. 두 검사는 서로를 대체하지 않는다.

## 9. B-2(실행 출처 표시) 작업 시 주의

`esgenie/run_info.py`의 `run_info`를 Excel 메타 칸과 **PDF 바닥글**에 넣을 때:

- Excel 메타 칸은 응답표 칸(D·E·F·G)이 아니므로 **영향이 없을 것으로 보인다.**
- **PDF 바닥글은 페이지 텍스트에 들어간다.** 바닥글이 응답표 영역 안의 페이지에 그려지면, 마지막 문항 블록의 꼬리로 들어가 **'블록 끝 잔여'로 잡힐 가능성이 있다**(`cell:note` 불일치).

**검사기를 미리 바꾸지 않았다.** B-2에서 `run_info`를 붙인 뒤 이 검사기를 반드시 재실행해 확인하고(작업지시서 §3 통과 조건 3), 실제로 잡히면 그때 바닥글을 제외하는 처리를 넣는다. 지금 추측으로 예외를 넣으면 진짜 불일치도 함께 가려진다.

## 10. 이 폴더의 파일

| 파일 | 내용 |
|---|---|
| `README.md` | 이 문서 |
| `PR_BODY.md` | PR 본문 초안 |
| `consistency_summary.json` | 공통 LIVE 두 단계 검사 요약(입력 sha256 포함) |
| `injection_summary.json` | 주입 20종 × 2단계 결과 |
| `reference_old_runs_summary.json` | 참고용 옛 실행 5개 검사 결과 |
| `tools/inject_check.py` | 주입 하네스 |
| `tools/collect_summary.py` | 요약 수집(개인 절대 경로 제거) |

결과 원본(`consistency.json`·`consistency.md`·`inject_log.json`·Excel 사본)은 `outputs/eval/` 아래에 있고 **커밋하지 않는다**.
