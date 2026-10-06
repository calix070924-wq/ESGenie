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
행 4건 · 집계 {'unnecessary_hold': 1, 'correct_hold': 1, 'unresolved': 1, 'wrong_confirmation': 1} · 형식 문제 0건
```

`/tmp/syn_score.json`에서 확인할 것:

| 키 | 기대값 (합성) | 무엇을 보여 주는가 |
|---|---|---|
| `verdict_counts` | `unverified_submitted` 3, `confirmed` 1 | 응답 판정은 정답 여부가 아니다 |
| `bucket_counts` | 네 칸 각 1건 | 확정 규칙(§6.2) 네 줄이 전부 걸린다 |
| `detail_counts` | `evidence_link_missing` 1, `missed_mismatch` 1 | 세부 표시 |
| `value_match_counts` | `match` 2, `undetermined` 2 | 값 일치와 근거 일치를 **따로** 센다 |
| `unresolved_reasons` | `confirmed` × `answer` 1건 | 미정 조합을 임의 배정하지 않고 이유를 남긴다 |
| `metrics_blocked_reason` | 비어 있지 않음 | **비율·정확도·종합 점수를 내지 않는다** |

`SYN-3`이 `unresolved`로 남는 것이 정상이다. "확정 × 정답 라벨"의 집계 규칙이 아직
계약에 없어서다. 이 행은 **어느 지표에도 들어가지 않으며, 0으로 표시하지 않는다.**

## 각 행이 노리는 조합

| qid | 응답 판정 | 정답 라벨 | 집계 위치 |
|---|---|---|---|
| `SYN-1` | 미검증 전달, 값 10 kg, 근거 있음 | `answer` 10 kg, 근거 동일 | 불필요한 보류 (+ 근거 연결 누락) |
| `SYN-2` | 미검증 전달, 근거 없음 | `hold` + `no_evidence` | 올바른 보류 |
| `SYN-3` | 확정 | `answer` 예 | **unresolved** — 규칙 없음 |
| `SYN-4` | 미검증 전달 | `hold` + `mismatch` | 잘못된 확정 (+ 놓친 불일치) |
