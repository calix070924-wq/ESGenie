# 기권(Abstain) A/B 측정 결과 — Coverage / Accuracy(assessed) / Overall

> 브랜치: `feature/abstention` · 실행 커맨드: `ESGENIE_FORCE_MOCK=1 python scripts/abstain_ab_eval.py`
> 실행 모드: **MOCK(배선 검증용 — 수치는 파이프라인 배선 확인용일 뿐 성능 근거 아님)**.
> 이 배치는 "측정"만 한다 — 게이트·HITL 라우팅은 아직 켜지 않았다.

## 착수 전 재확인한 3가지 사실

1. `esgenie/evaluate.py:111`의 `risk_coverage(rows, cfg, *, steps=...)`는 이미 존재하지만 confidence=|risk_score−threshold| 기반 임계값 디퍼럴이며 `AxisScore.abstain`과는 무관 — 그대로 두고 `abstain_coverage()`를 병렬로 추가했다.
2. `esgenie/benchmark.py`의 `DetectorReport.metrics()`(precision/recall/f1/accuracy만 반환)와 `_flagged(rv, threshold, axis_flag) -> (bool, float)`(이진 flagged) — coverage 개념이 없었다. 이번에 `coverage`/`accuracy_on_assessed`/`overall`/`abstains`를 병렬 추가했다.
3. `scripts/held_out_eval.py`는 dev/test 스플릿 CI를 `capture()`(calibrate.py) → `_case_rows()`/`_prf()`/`bootstrap_ci()`(evaluate.py)로 산출하며 **cfg=BASELINE 고정, test 재튜닝 금지** 원칙을 지킨다 — `scripts/abstain_ab_eval.py`도 동일 원칙·동일 함수를 재사용한다.

**추가로 발견해 고친 배관 문제(사전에 알려지지 않음):** `calibrate.py`의 `capture()`/`_simulate_vector()`가 캐시 저장·복원 시 `rule_score`/`detail`만 보존하고 `AxisScore.abstain`/`abstain_reason`을 버렸다. 고치지 않았다면 `evaluate.py`의 `_case_rows()`가 재구성하는 `RiskVector`는 항상 `abstain=False`가 되어, dev/test A/B에서 ON/OFF가 항상 동일하게 나왔을 것이다(`capture()`가 `rec_axes[n]`에 `abstain`/`abstain_reason`을 추가로 저장하고, `_simulate_vector()`가 이를 복원하도록 수정).

## 측정 정의

- **케이스 단위 기권**: RiskVector에 `abstain=True`인 축이 있고, 그 케이스가 `flagged`(=`_flagged()`가 위험으로 판정)되지 않았을 때만 "기권(deferred)"으로 센다. abstain 축은 설계상 score=0.0로 고정되어 다른 축의 flagged 판정에 위험을 보태지 않으므로, 기존 `flagged` 값이 이미 "다른 축이 위험을 잡았는지"를 그대로 반영한다(별도 재계산 불필요).
- **Coverage** = 기권 안 한 케이스 수 / 전체 케이스 수
- **Accuracy(assessed)** = 기권 안 한 케이스 중 정답 비율
- **Overall** = Accuracy(assessed) × Coverage
- 사유별 분해는 abstained 축의 `abstain_reason`에서 집계(현재 코드상 `low_confidence`는 아직 어디서도 세팅되지 않으므로 항상 0으로 나온다 — LLM 판정 연동은 이번 배치 범위 밖).

## A/B 결과 (MOCK 실행 — 수치 무의미, 배선 검증용)

임계값 고정(BASELINE): trig=0.25 w=0.4 thr=0.25 axf=0.80 (test 재튜닝 안 함)

### DEV — n=50 (튜닝셋, `domain=tuning_synthetic` 전량)

| 조건 | Coverage | Accuracy(assessed) | Overall | 기권수(no_ev/unit/lowconf) | precision | recall | f1 |
|---|---|---|---|---|---|---|---|
| OFF | 100.0% | 0.960 | 0.960 | 0(0/0/0) | 0.913 | 1.000 | 0.955 |
| ON  | 100.0% | 0.960 | 0.960 | 0(0/0/0) | 0.913 | 1.000 | 0.955 |

F1 bootstrap 95% CI — OFF 0.955(0.878~1.000) · ON 0.955(0.878~1.000)

dev 스플릿은 합성 문장(`tuning_synthetic`)이라 D1이 실제 DART 그래프의 "근거 없음/단위 불일치" 조건을 건드릴 일이 거의 없다 — ON/OFF 완전 동일(회귀 확인 겸 기대 결과).

### TEST — n=270 (held-out, esg_report 214 + regulatory_ad 56)

| 조건 | Coverage | Accuracy(assessed) | Overall | 기권수(no_ev/unit/lowconf) | precision | recall | f1 |
|---|---|---|---|---|---|---|---|
| OFF | 100.0% | 0.774 | 0.774 | 0(0/0/0) | 0.720 | 0.434 | 0.541 |
| ON  | 97.4%  | 0.772 | 0.752 | 7(0/7/0) | 0.720 | 0.434 | 0.541 |

F1 bootstrap 95% CI — OFF 0.541(0.431~0.635) · ON 0.541(0.431~0.635)

## 회귀 확인

- **ABSTAIN_ENABLED=0(OFF)에서 Coverage=100%, 신규 지표(accuracy_on_assessed/overall)가 기존 accuracy와 정확히 일치** — dev/test 모두 확인됨. precision/recall/f1은 ON/OFF 완전 동일(설계대로 — abstain은 `flagged`/`pred`를 바꾸지 않고 assessed 여부만 바꾼다).
- 단위 테스트(`tests/test_abstain_metrics.py`, 11건)로 `abstain_coverage()`·`_case_rows()`·`_simulate_vector()`·`DetectorReport.metrics()`의 산식·배관을 별도 검증.

## 해석 (이번 MOCK 실행 기준 — 잠정)

test 스플릿에서 ON일 때 **Coverage가 100%→97.4%로 낮아지고, Overall도 0.774→0.752로 소폭 하락**했다. 사용자가 제시한 해석 가이드(EmeraldMind 기준)대로 읽으면:

> Overall이 떨어지면 기권이 너무 많거나 "옳은 판정까지 기권"했다는 신호 — 다음 단계(게이트 라우팅) 전에 조건 재검토가 필요하다는 뜻이다.

다만 이 결론은 **MOCK 실행 한정**으로 조심해서 읽어야 한다:
- precision/recall/f1이 ON/OFF에서 **완전히 동일**하다는 것은, 기권이 발생한 7건이 애초에 `flagged` 여부 자체를 바꾸지 않았다는 뜻이다(기권 축이 있어도 다른 축/기존 로직으로 이미 같은 `pred`가 나왔던 케이스들). 즉 이 7건은 "억지 판정을 기권으로 바꿔 오탐을 줄인" 사례가 아니라, "원래도 올바르게 판정되던 케이스가 assessed 분모에서만 빠진" 사례일 가능성이 높다 — Overall 하락은 실제 능력 저하가 아니라 **표본이 준 것 자체의 산술적 효과**로 보인다.
- LLM이 mock이라 judge 경로의 실제 신호는 전혀 반영되지 않았다. 실키로 재실행하면 판정 자체가 달라질 케이스가 있을 수 있다.

**결론: 이번 MOCK 결과만으로는 Step 4(게이트 라우팅) 진행 여부를 판단할 수 없다.** 아래 "다음 확인"을 실키로 재실행한 뒤 재평가할 것을 권장한다.

## 다음 확인 (실키 재실행 필요)

```bash
OPENAI_API_KEY=... ESGENIE_STRICT=1 python scripts/abstain_ab_eval.py
```
- 실제 성능 하에서도 Overall이 하락하는지, 하락한다면 어떤 7(+α)건인지 케이스 단위로 확인.
- test 분포상 `unit_mismatch` 사유만 관측됨(`no_evidence`/`low_confidence` 0건) — 실제 DART 그래프에서 "근거 노드 자체가 없는" 경우가 이 스플릿엔 드물다는 뜻일 수 있음(005930 한 종목 그래프로 여러 회사 문장을 검증하는 벤치 구조상 대부분의 코드에 노드가 존재하기 때문으로 추정 — 확인 필요).

## 참고

- 캐시 파일: `outputs/benchmark/{dev,test}_abstain_{off,on}_judge_cache.json` (재실행 시 갱신됨, git 추적 대상 아님)
- 리포트 원본: `outputs/benchmark/abstain_ab.md`
