# Table Gate Benchmark

표 복원 캐스케이드(`Tier 0~3`)와 검증 게이트 임계치 보정을 위한 평가셋 폴더.

## 파일

- `cases.jsonl`
  후보 케이스 메타데이터. 한 줄 = 표 1개.
- `gold/<case_id>.csv`
  사람이 확정한 정답 표.
- `raw/<case_id>.json`
  원본 OCR 표 구조, gate 결과, metric preview 스냅샷.
- `summary.json`
  후보 생성 시점 요약.

## 기본 워크플로우

1. `PYTHONPATH=. python3 scripts/build_table_benchmark.py`
2. `outputs/benchmark_tables/review_candidates.xlsx` 에서 `gate_gold`, `review_status` 라벨링
3. `gold/<case_id>.csv` 에 정답 표 저장
4. dev/test split 확정 후 calibration 스크립트에서 임계치 튜닝

## `gate_gold` 기준

- `accept`: 자동 채택 가능
- `escalate`: 더 높은 tier 필요
- `human`: 자동 채택 금지, 사람 확인 필요
