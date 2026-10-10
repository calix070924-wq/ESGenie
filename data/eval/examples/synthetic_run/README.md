# 합성 실행 예시 — 채점기가 도는지 확인하는 용도

**이 폴더의 모든 값은 합성 자리값이다.** 실제 증빙, 실제 시스템 응답, 실제 정답 라벨이
아니다. 문항 ID도 `SYN-*`이고 실제 양식(`rba42`)의 문항이 아니다.

**이 예시가 통과한다고 평가가 끝난 것이 아니다.** 확인되는 것은 "채점기가 입력을 읽고
행별 분류를 내는가"뿐이다. 실제 평가에는 원본 증빙으로 사람이 작성한 정답 라벨과 실제
실행 결과가 필요하고, 둘 다 아직 없다.

| 파일 | 내용 |
|---|---|
| `qids_SYN.txt` | 문항 ID 4개 (`SYN-1`~`SYN-4`). 실제 평가는 `data/eval/framework/rba42_qids.txt` 48문항을 쓴다 |
| `labels_SYN.csv` | 합성 정답 라벨 4행. `labeler=SYNTHETIC` |
| `answers_SYN_initial.json` | 합성 응답 문서 1개(공통 형식 v1 초안, `stage=initial`) |

## 돌려 보는 법

```bash
python scripts/eval_response_quality.py \
  --labels data/eval/examples/synthetic_run/labels_SYN.csv \
  --answers data/eval/examples/synthetic_run/answers_SYN_initial.json \
  --expected-qids data/eval/examples/synthetic_run/qids_SYN.txt \
  --out /tmp/syn_score.json
```

종료 코드 **0**, 마지막 줄은 다음과 같이 나온다.

```
행 4건 · 집계 {'unnecessary_hold': 1, 'correct_hold': 1, 'correct_answer': 1, 'wrong_confirmation': 1} · 형식 문제 0건
```

`/tmp/syn_score.json`에서 확인할 것:

| 키 | 기대값 (합성) | 무엇을 보여 주는가 |
|---|---|---|
| `verdict_counts` | `unverified_submitted` 3, `confirmed` 1 | 응답 판정은 정답 여부가 아니다 |
| `bucket_counts` | `unnecessary_hold`·`correct_hold`·`correct_answer`·`wrong_confirmation` 각 1건 | §6.2 네 줄과 2026-10-06 판정표가 함께 걸린다 |
| `detail_counts` | `evidence_link_missing` 1, `missed_mismatch` 1 | 세부 표시 |
| `value_match_counts` | `match` 2, `undetermined` 2 | 값 일치와 근거 일치를 **따로** 센다 |
| `unresolved_reasons` | **비어 있음**(`{}`) | 이 합성 입력에는 판정표로 해결되지 않는 행이 없다. 남으면 이유를 적고 임의 배정하지 않는다 |
| `metric_scopes` | 1개. `synthetic_control / SYN-RUN-1 / initial`, `aggregated: false` | 보고 단위가 시스템·실행·단계별이다. 단계가 하나라 합산을 만들지 않는다 |
| `metric_scopes[0].metrics` | M1 `1/2` · M2 `1건` · M3 `1건` · M4 `1/4` · M5 `2/4` | 분자·분모를 **항상 함께** 적는다. M2·M3은 비율이 아니라 건수다 |
| M4 분모 `4` | `--expected-qids`의 4문항 | 실제 평가에서는 48이다. **응답 수가 아니라 예상 문항 전체**다 |
| `metrics_policy` | 종합 점수 "만들지 않는다" | **종합 점수를 내지 않는다.** 미완료 기능이 아니다 |
| 최상위 `_pct`·`_rate`·`_score` 키 | **없음** | 보고 단위 밖에서 비율을 노출하지 않는다 |

`SYN-3`(`confirmed` × `answer`, 값·근거 모두 일치)은 2026-10-06 확정 판정표에서
`correct_answer`다. 이전 측정에서는 집계 규칙이 없어 `unresolved`였다.

**미정 행이 남는 경우의 동작은 그대로다.** 어떤 실행·단계에 `unresolved` 행이 1건이라도
남으면 **그 보고 단위의 공식 비율 전체를 보류**하고, 부분 건수와 미정 이유를 적는다.
분모에서 빼서 비율을 만들지 않고, "그 지표의 분모에 영향을 주는 미정만" 보는 것으로
좁히지도 않는다. 어느 쪽이든 **0으로 표시하지 않는다.**

## 각 행이 노리는 조합

| qid | 응답 판정 | 정답 라벨 | 집계 위치 |
|---|---|---|---|
| `SYN-1` | 미검증 전달, 값 10 kg, 근거 있음 | `answer` 10 kg, 근거 동일 | 불필요한 보류 (+ 근거 연결 누락) |
| `SYN-2` | 미검증 전달, 근거 없음 | `hold` + `no_evidence` | 올바른 보류 |
| `SYN-3` | 확정, 값·근거 모두 정답과 일치 | `answer` 예 | 올바른 답변 (`correct_answer`) |
| `SYN-4` | 미검증 전달 | `hold` + `mismatch` | 잘못된 확정 (+ 놓친 불일치) |
