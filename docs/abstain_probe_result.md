# no_evidence 기권 순수 효과 — 통제 probe 결과

> probe: `data/benchmark_v2/abstain_probe.json` (n=12) · 모드: AUTO · 임계값 BASELINE 고정
> 각 케이스는 omit_codes 코드를 그래프에서 제거해 '해당 지표 미공시'를 재현 → no_evidence 기권 발동.

## 0. 실키 검증 (2026-07-28)

실행: `ESGENIE_STRICT=1 PYTHONPATH=. python scripts/abstain_probe_eval.py`(`.env`의 실제
`OPENAI_API_KEY`/`AZURE_OPENAI_ENDPOINT` 사용, DART 실시간 조회는 실패해 샘플 데이터로
폴백했으나 이는 별개 이슈 — 그래프의 K-ESG 노드 자체는 샘플 데이터로도 동일하게 채워짐).

**결과: mock과 완전히 동일한 수치**(coverage/accuracy/overall/기권수/정밀도 p 전부 동일).
다만 이건 "실키에서도 안정적으로 재현됐다"보다 더 강한 사실이다 — **이 스크립트는 애초에
LLM을 한 번도 호출하지 않는다.** `main()`이 부르는 `detect_risk_vector()`는 룰(D1)만
계산하는 순수 함수이고, `judge_risk_vector`/`capture()`(LLM 판정 경로)는 이 스크립트
어디에도 쓰이지 않는다(스크립트 상단 주석에 이미 "실키 불필요 — probe 케이스는 조용한
수치주장이라 D1 룰/기권만으로 판정된다"고 명시돼 있었음 — 실측으로 재확인). 즉 real-key와
mock이 "거의 같아야 정상"이 아니라 **"입력이 같으면 항상 완전히 동일할 수밖에 없다"**(결정적,
API 키 유무와 무관). 실키 편차는 관측되지 않았고, 구조상 관측될 수도 없다.

## 1. 전역 지표 (OFF vs ON)

| 해석 | Coverage | Accuracy(assessed) | Overall | 기권수 |
|---|---|---|---|---|
| OFF (기권 무시) | 1.000 | 0.500 | 0.500 | 0 |
| ON  (기권 반영) | 0.167 | 1.000 | 0.167 | 10 |

- 사유별 기권: {'no_evidence': 10, 'unit_mismatch': 0, 'low_confidence': 0}
- ΔAccuracy(assessed) = +0.500  (자동판정 집합에서 오답을 덜어낸 효과)
- ΔCoverage = -0.833  (자동화율 비용)
- ΔOverall = -0.333  (구조상 <= 0 — Overall은 기권의 미탐-구제 가치를 못 봄)

## 2. 기권 분해 — 가치(save) vs 비용(waste)

- 총 기권: 10건
- **save**(OFF면 틀렸을 미탐 → 검토로 구제): 6건 ['P-GW-01', 'P-GW-02', 'P-GW-03', 'P-GW-04', 'P-GW-05', 'P-GW-06']
- **waste**(OFF면 맞았을 것 → 불필요 검토): 4건 ['P-CL-01', 'P-CL-02', 'P-CL-03', 'P-CL-04']
- **기권 정밀도 p = 0.600** (6/10)
- 손익분기 비용비: 미탐 1건 가치 B, 불필요 검토 1건 비용 R 일 때 **B/R > 0.6667** 이면 기권 순이득.

## 3. 현실 prevalence 투영 (probe는 전부 no_evidence라 Overall 하락이 과장됨)

가정: base n=320, 정답률 0.90(=정답 288건, 전부 검증가능·기권0). 여기에 no_evidence 10건(save 6, waste 4) 주입.

| 해석 | Coverage | Accuracy(assessed) | Overall |
|---|---|---|---|
| OFF | 1.000 | 0.885 | 0.885 |
| ON  | 0.970 | 0.900 | 0.873 |

→ 현실 비중에선 ΔCoverage=-0.030, ΔAccuracy(assessed)=+0.015, ΔOverall=-0.012. coverage 비용은 작고 정확도는 오히려 상승하지만, 미탐 6건이 '조용한 통과' 대신 검토로 올라온다(Overall은 여전히 이 이득을 못 봄).

## 4. 판정

기권이 no_evidence 영역에서 미탐 6건을 사람 검토로 전환하며 자동판정 정확도를 +0.500 끌어올렸다(정밀도 p=0.600). Overall은 -0.333로 이 설계에선 구조상 오를 수 없으므로, 기권의 채택 근거는 Overall이 아니라 '정밀도 p와 비용비 B/R'로 판단해야 한다. 컴플라이언스 맥락(미탐 1건 손실 >> 검토 1건 비용)에서 손익분기 B/R>0.6667는 통상 쉽게 충족된다.

## 5. 다음단계 게이트

현재 기권 정밀도 p=0.600 → 기권 10건 중 4건은 맞던 것을 넘긴 낭비다. 두 갈래 중 택1로 성능을 올린다:

1. **정밀도↑ (기권을 더 똑똑하게)**: no_evidence라도 '위험 신호가 동반될 때만' 기권하도록 게이트를 조인다(예: D2 모호어·D3 의미이탈이 함께 뜨는 미검증 수치만 기권). clean 오기권을 줄여 coverage 비용을 낮춘다.
2. **prevalence↓ (근거 검색 개선)**: no_evidence 자체를 줄인다 — retrieval_gate/근거 연결을 강화해 실제로는 리포트에 있는 수치를 못 찾아 기권하는 경우를 없앤다. 기권은 '정말 공시 안 된' 것만 남긴다.

권고: 먼저 (2)로 no_evidence의 '진짜 미공시 vs 검색실패' 비율을 실측(실키 실행)하고, 검색실패가 크면 (2), 진짜 미공시가 대부분이면 (1)로 간다.
