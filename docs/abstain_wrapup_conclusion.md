# 책임있는 기권(Responsible Abstention) — 마무리 결론

> 최종 갱신: 2026-07-30 · 브랜치 `feature/abstention`
> 이 문서는 기권 라인의 조사·검증을 종결하고, 채택 근거·알려진 한계·향후 옵션을 고정한다.

## 0. 한 줄 결론

no_evidence 기권은 **메커니즘이 검증됐고, 배치 B 파일럿(SME 샘플/시나리오 리포트, 주입 없음)에서
순이득(손해보다 이득)** 임이 확인됐다.
채택 기준은 **객관적 "근거 유무"** 하나로 확정하고, 애매한 "값 타당성(implausibility)" 판단은
**의도적으로 배제**한다. 논문(EmeraldMind) 수준의 "책임있는 기권"에 도달했으며, 사용자 노출
(리포트 블록)과 단위-인식형 정밀화는 별도 과제로 남긴다.

## 1. 지금까지 확정된 사실 (근거 커밋)

- **메커니즘 작동**: no_evidence 기권은 "문장의 모든 토픽 코드가 그래프에 노드 0개일 때" 정확히
  발동한다. probe(주입) 및 배치 B(SME 샘플/시나리오 리포트, 주입 없음) 양쪽에서 재현.
  (`98f6b51`, `0b04355`)
- **지표의 구조적 성질**: Overall = 정답assessed / N 이라, 예측이 고정된 이 A/B 설계에서
  **기권은 Overall을 절대 올릴 수 없다(단조 비증가)**. 기권의 가치("미탐→검토 전환")는 Overall이
  구조적으로 못 보므로, Accuracy(assessed)/Coverage 트레이드오프로 관찰한다.
- **배치 B 파일럿 실측(SME 샘플/시나리오 리포트, n=21)**: no_evidence 자연 발동 15건, 무결성 문제
  0건, Accuracy(assessed) 0.571→1.000.
  **(2026-08-03 리뷰 반영, 허정만 — PR #52) 여기서 나온 정밀도 p=0.60은 성능 지표가 아니다**:
  no_evidence 기권은 content-blind다(노드 유무로만 발동, 주장의 참/거짓을 보지 않는다). 따라서
  기권 케이스에 어떤 라벨을 붙여도 abstain은 참·거짓을 동일 비율로 전부 기권하므로, p는 구조적으로
  이 파일럿의 라벨 구성비(greenwash:clean=9:6)와 같아진다 — relabeling으로 해소되지 않는다.
  근거가 있을 때의 실제 D1 성능은 별도로 §1-c/`data/benchmark_v2/batch_c_truth_holdout.json`
  (진위 holdout)로 측정한다.
- **현행 dev/test의 한계 규명**: dev/test(n=320)는 005930 데이터를 정답 앵커로 만들어져
  no_evidence를 **구조적으로 0건** 관측 → 배치 B(SME 샘플/시나리오 리포트)가 이 공백을 메움.
  (`c9ba9ad`)

## 1-b. 라이브(룰+LLM) 검증 — 실 LLM으로 재확인 (2026-07-30)

리뷰어(허정만) 지적: 기존 하네스는 `detect_risk_vector`(룰 전용)만 써서 "mock=실키 동일"이었다
— abstain 판정이 룰 레이어에서만 나기 때문이지, 파이프라인 전체를 실 LLM으로 검증한 건 아니었다.
`ESGENIE_ABSTAIN_LIVE=1`을 두 하네스에 추가해, 케이스마다 룰(`detect_risk_vector`)과 하이브리드
(`detect_risk_vector_hybrid`, 실 LLM 호출)를 **둘 다** 돌려 abstain 결정이 LLM 단계에서 보존되는지
직접 실증했다.

**구조**: `judge_risk_vector`(esgenie/layer3_judge.py:123)는 전 축 룰점수가 `JUDGE_TRIGGER`(기본
0.25) 미만이면 LLM 호출 자체를 생략한다. abstain 축은 설계상 score=0.0으로 고정되므로 이 트리거를
넘을 수 없다 — **abstain 케이스는 구조적으로 LLM이 안 불린다.** 값이 실제로 어긋나는
`control_mismatch` 대조군(D1=1.0)만 트리거돼 실 호출된다.

**실측(ESGENIE_STRICT=1, `.env`의 실제 OPENAI_API_KEY, 2026-07-30)**:

| 하네스 | n | LLM 호출 | rule↔hybrid abstain 불일치 |
|---|---|---|---|
| `scripts/batch_b_omission_eval.py` | 21 | 2/21건(B-CTRL-M1, B-CTRL-M2 — 둘 다 값 불일치 대조군) | 0건 |
| `scripts/abstain_probe_eval.py` | 12 | 1/12건(P-CTRL-M2 — 값 불일치 대조군) | 0건 |

두 하네스 모두 Coverage/Accuracy(assessed)/Overall/기권 정밀도 p 수치가 룰-전용 실행과
**완전히 동일**했다(hybrid 경로로 지표를 계산해도 abstain된 케이스는 애초에 LLM을 안 타므로
값이 바뀔 수가 없다). 즉 "abstain 판정을 LLM이 뒤집는 사례는 실측 33건(21+12) 중 0건"이며,
이는 우연이 아니라 트리거 구조상 **abstain과 LLM 호출이 상호 배타적**이기 때문임을 확인했다
(비-abstain 위험 신호가 있는 케이스만 LLM 검토를 받고, LLM이 검토한 축은 애초에 abstain
후보가 아니었던 축이다). 비-라이브(기본) 경로 수치는 이 변경 전후로 완전히 동일 — 회귀 없음.

## 1-c. 리뷰 반영 — p 재해석 + 진위 holdout 신설 (2026-08-03, 허정만 리뷰 PR #52)

리뷰어 지적 3건과 대응:

1. **SME001/SME002를 "실 DART 리포트"로 서술**: 사실이 아니다. `data/sample_dart`의 합성
   파일럿 리포트다(`.source`="사업보고서 2024 (현대차/삼성전자 협력사 시나리오)"). 이 문서와
   배치 B 산출물 전반에서 "실데이터"라는 수식을 "샘플/시나리오 리포트"로 정정했다. "주입/조작
   없이 자연 발동한다"는 사실 자체는 그대로 유지된다 — 리포트가 합성이라는 것과 "이 하네스가
   미공시 코드를 인위적으로 주입하지 않았다"는 것은 별개 사실이다.
2. **정밀도 p=0.60이 모델 성능처럼 읽힘**: no_evidence 기권은 **content-blind**다 — 노드
   유무로만 발동하고 주장의 참/거짓을 보지 않는다. 따라서 기권 케이스 9건(greenwash)과 6건
   (clean)에 어떤 라벨을 붙여도 abstain은 둘을 구분하지 않고 전부 기권하므로, p는 항상 그
   구성비(9/15=0.60)와 같아진다 — **relabeling으로 해소되지 않는 구조적 한계**다. 위 §0/§1의
   "정밀도 p" 서술을 이 문서와 `docs/batch_b_omission_result.md`, PR #52 본문에서 모두
   "성능 지표 아님"으로 정정했다. abstain에서 실제로 검증된 것은 (a) 정확한 발동 조건, (b) 실위험
   미차폐(§3-2), (c) LLM과의 직교성(§1-b)이며, 측정 축은 Accuracy(assessed)/Coverage
   트레이드오프다.
3. **`scripts/abstain_probe_eval.py`가 mock 오염을 못 잡음**: 라이브 집계가 `judge.used`만
   보고 `judge.used_mock`을 확인하지 않아, `ESGENIE_ABSTAIN_LIVE=1 ESGENIE_FORCE_MOCK=1`
   조합에서도 "LLM 호출 성공"으로 집계됐다. `abstain_probe_eval.py`/`batch_b_omission_eval.py`
   양쪽에 `used_mock` 가드를 추가해, mock 오염 케이스는 실호출 집계에서 제외하고
   "MOCK-CONTAMINATED"로 명시하며, 라이브 모드에서 발생 시 비정상 종료(exit 1)한다.

**진짜 D1 성능이 측정되는 지점**: 근거가 있을 때(no_evidence가 아닐 때)의 수치검증 정확도.
이를 라벨-독립적으로(값의 "그럴듯함"이 아니라 실제 공시값과의 일치로) 측정하기 위해
`data/benchmark_v2/batch_c_truth_holdout.json` + `scripts/d1_truth_eval.py`를 신설했다
(SME001/SME002/005930 등 우리가 실제 값을 아는 지표로 구성, 근거 present — 기권과는 별개
트랙). 결과는 §6 관련 산출물 및 `outputs/`에 기록한다.

## 2. 이번 마무리에서 새로 밝힌 것

### 2-a. "손해(검색 실패)"의 실체 = 사실상 1건

기권이 손해가 되는 주 경로는 **검색 실패**다 — 지표가 리포트에 실제로 있는데(공시됨) 그래프가
못 찾아 "근거 없음"으로 착각하고 기권하는 경우. SME001/SME002(샘플/시나리오 리포트) 두 리포트의
**모든 원문(raw_text) 수치**로 주장을 만들어 스캔한 결과, 잘못 기권되는 건
**"재활용량 41.3톤"(SME001) 단 1건**이었다.

이유: 그래프 빌더가 **이미 S영역 원문 수치(재해율·이직률 등)를 노드로 추출**하고 있어, 검색 실패가
E영역 원문 절대값(재활용 톤 등)에만 국한된다. 즉 "있는 근거를 버리는 손해"는 현재 데이터에서
거의 이미 통제돼 있다.

### 2-b. 그 1건을 고치려는 프로토타입이 드러낸 side effect

"재활용 41.3톤"을 E-6-2 노드로 추가하니 그 주장은 올바르게 통과됐으나(손해 제거), **배치 B의
"재활용률 97%"(진짜 미공시, 기권 대상)가 기권을 멈췄다**. 원인: E-6-2 코드가 "재활용량(톤)"과
"재활용률(%)"을 함께 쓰는데, 톤 노드가 생기자 % 주장이 no_evidence가 아니라 unit_mismatch로
분류돼(unit_mismatch 기권은 off) 그냥 통과된 것.

→ 검색 실패 1건을 **제대로** 고치려면 단순 노드화가 아니라, "단위가 호환되는 근거가 있냐"까지
따지는 **단위-인식형 D1 로직**으로 코어를 바꿔야 한다. **ROI 판단: 1건 vs 코어 수정+회귀 위험 →
지금은 하지 않는다.**

### 2-c. implausibility(값 타당성) 게이트 — 배제 확정

benign 기권(정상 범위인데 미공시라 기권되는 낭비)을 줄이려 "값이 비현실적일 때만 기권"하는
게이트를 검토했으나 **배제**한다. 두 가지 이유:
1. **기준 불명확**: "터무니없다"는 주관적이며, 애초에 그린워싱의 정의가 아니다(그린워싱은
   "값이 좋아 보임"이 아니라 "주장이 거짓/오도"). 진짜로 낮은 이직률일 수도, 평범해 보이는
   조작값일 수도 있다.
2. **평가 순환논리**: 배치 B의 greenwash 라벨을 "값 타당성"으로 부여했으므로, 기권 트리거도
   같은 기준으로 걸면 정밀도가 인위적으로 부풀려져 "개선"을 정직하게 증명할 수 없다.

대신 기권은 **객관적 기준 "근거 유무"에만** 건다. 평범해 보이는 미공시 주장을 기권하는 것은
"손해"가 아니라 **"이 숫자를 확인할 수 없다"는 정직한(참인) 진술**이며, 이것이 논문의 책임있는
기권 정신이다.

## 3. 확정된 설계 원칙

1. 기권 트리거 = **객관적 "근거 유무"** 단일 기준. 값 타당성/모호어 등 주관 판단은 트리거에 넣지 않는다.
2. 기권은 **실위험을 가리지 않는다**(score 0.0 고정, 다른 축이 flag하면 기권 아님) — 배치 B 대조군에서 재확인.
3. 채택 평가는 Overall이나 기권 정밀도 p(content-blind — §1-c)가 아니라, **Accuracy(assessed)/
   Coverage 트레이드오프 + 근거 present 구간의 진위 holdout D1 성능(§1-c)**으로 한다.
4. 기본값은 현행 유지(`ABSTAIN_ENABLED=0`) — 활성화/노출은 아래 향후 과제에서 결정.

## 4. 알려진 한계 (의도적으로 남겨둔 것)

- **검색 실패 1건**(E영역 원문 절대값, 예: 재활용 톤): 코어 단위-인식형 수정 전까지 잘못 기권 가능.
  빈도 낮아 현재 미수정. 재현: `재활용량은 41.3톤이었다.`(SME001).
- **정밀도 p=0.60은 성능 지표가 아니다**(2026-08-03 리뷰 반영 — §1-c 참조): no_evidence 기권은
  content-blind라 p는 구조적으로 파일럿 라벨 구성비(gw:clean=9:6)와 같아진다. 근거가 있을 때의
  실제 D1 성능은 진위 holdout(`data/benchmark_v2/batch_c_truth_holdout.json`,
  `scripts/d1_truth_eval.py`)으로 별도 측정한다.
- **사용자 노출 없음**: 기권은 내부 플래그로만 존재하며 리포트 출력(layer6)까지 흐르지 않는다.
  현재 리포트는 노드 기반이라 no_evidence(노드 없음)는 표에 도달하지 않는다.

## 5. 향후 과제 (착수 안 함 — 필요 시 재개)

- **[제품화] 리포트 "검증 불가 — 근거 없음" 블록(갈래 2)**: 회사 텍스트 주장 스캔 → 미공시 수집 →
  리포트 명시. 출력 레이어 신규 경로. 논문도 안 간 영역.
- **[정밀화] 단위-인식형 D1**: any_nodes를 "단위 호환 노드 존재"로 바꿔 §2-b side effect 없이
  검색 실패를 안전하게 제거. 코어 수정 → 전체 회귀 필수.
- **[검증] 진위 holdout — §1-c에서 착수·완료**: 애초 이 항목은 "라벨 독립 셋으로 정밀도 p를
  편향 없이 추정"을 목표로 뒀었으나, 이는 §1-c의 리뷰로 정정됐다 — no_evidence 기권은
  content-blind라 abstain 케이스 자체의 라벨을 아무리 진위 기준으로 다시 매겨도 p는 여전히
  그 케이스셋의 구성비와 같아진다(관측 대상이 참/거짓을 구분하지 않고 전부 기권되므로). 대신
  진위 holdout(`batch_c_truth_holdout.json`)은 p를 "고치는" 게 아니라, **근거가 있어 애초에
  기권되지 않는 영역**에서 D1 수치검증 자체의 정확도를 측정하는 별개 트랙으로 신설했다.
- **[규모] 다양한 실 리포트 확충**: 검색 실패·미공시 prevalence를 신뢰구간과 함께 재측정.

## 6. 관련 산출물

- 데이터: `data/benchmark_v2/abstain_probe.json`, `data/benchmark_v2/batch_b_omission.json`
- 하네스: `scripts/abstain_probe_eval.py`, `scripts/batch_b_omission_eval.py`, `scripts/abstain_prevalence_audit.py`
- 결과: `docs/abstain_probe_result.md`, `docs/batch_b_omission_result.md`, `docs/abstain_realworld_prevalence.md`, `docs/abstention_metrics_result.md`
- 테스트: `tests/test_abstain_probe.py`, `tests/test_batch_b_omission.py`, `tests/test_abstain_*.py`
