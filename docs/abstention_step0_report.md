# 책임있는 기권(Abstention) 도입 — Step 0 정독 보고

> 브랜치: `feature/abstention` · 기준 커밋: `ac43d2d`(main) · 코드 변경 없음(정독 전용)
> 목적: `AxisScore`에 기권 필드를 추가하기 전, 현재 반환 지점·소비처를 실측한다.

## 1. 반환 지점별 기권 후보 판정

| 위치(파일:라인) | 현재 반환 | 기권 후보? | 제안 사유 |
|---|---|---|---|
| `ssot/detector_5axis.py:76` (`detect_d1_numeric`, 수치 클레임 없음) | `AxisScore(0.0, [], "수치 클레임 없음")` | 아니오 | 문장에 수치 주장 자체가 없는 진짜 안전 케이스. 기권 대상 아님(판정할 게 없을 뿐, 근거가 없어서가 아님). |
| `ssot/detector_5axis.py:79` (K-ESG 매핑 없음) | `AxisScore(0.6, [], "…근거 추적 불가")` | **예** | `no_evidence`에 가깝지만 정확히는 "코드 매핑 실패"다. 근거 부재(83행)와 사유를 분리해야 함 — 사용자 제안대로 별도 `abstain_reason`(예: `no_mapping` 후보) 검토 필요. 이번 배치에서는 사유 값을 확정하지 않고 필드만 만든다. |
| `ssot/detector_5axis.py:83` (근거 노드 없음, 최우선 후보) | `AxisScore(0.9, [], "…미증빙 수치(고위험)")` | **예(최우선)** | `no_evidence`. 사용자가 예시로 든 정확한 지점 — "근거가 없다"를 "고위험"으로 단정하는 것과 "판정 보류"는 다르다. |
| `ssot/detector_5axis.py:95-100` (미일치 수치, ±2%p 초과) | `AxisScore(0.5+0.1*n, matched_evidence, "미일치 수치 …")` | 아니오(이번 배치 판단) | 근거 노드는 **있고** 값이 실제로 다른 경우라 이건 진짜 위험 신호에 가깝다. 기권으로 돌리면 실제 수치 불일치를 숨기게 될 위험 — 다음 배치에서 신중히 재검토 권장. |
| `ssot/detector_5axis.py:104-106` (모두 일치 + cross_check) | `AxisScore(xrisk, matched_evidence, …)` | 아니오 | 정상 판정 경로(증빙 있음 + 일치). |
| `layer3_detect.py:389` (`_score_d1_numeric`, evidence_graph 없음) | `AxisScore(0.0, [], "evidence_graph 없음 — 스킵")` | **예** | `no_evidence`. 현재 0.0(안전)으로 처리되어 **ssot 쪽 0.9(고위험)와 정반대 극단**이다 — 아래 3절 참조. |
| `layer3_detect.py:523` (`_score_d5_timeseries`, evidence_graph 없음) | `AxisScore(0.0, [], "evidence_graph 없음 — 스킵")` | **예** | 상동(D5도 동일 패턴). |
| `layer3_detect.py:438` (단위 불일치, 후보 1건 skip) | 개별 숫자 1건만 continue — 함수 리턴 지점 아님 | 부분 후보 | 이 숫자만 스킵되고 문장 내 다른 코드로 매칭이 이어질 수 있어, "함수의 최종 반환"이 아니라 루프 내부 스킵이다. 최종적으로 어떤 코드와도 안 맞으면 451행 결과로 수렴. |
| `layer3_detect.py:451` (수치 매칭 없음) | `score = min(1.0, worst_delta/D1_THRESHOLD)`, `worst_delta`가 0으로 남아 `score=0.0`, detail="수치 매칭 없음" | **예** | `no_evidence`/`unit_mismatch` 혼재. 문장에 수치가 있는데 비교할 노드가 전혀 없거나 전부 단위 불일치였던 경우도 **score=0.0(안전)으로 귀결**된다 — ssot의 0.9(고위험)와 정반대. 두 D1의 비대칭이 가장 뚜렷한 지점. |
| `layer3_detect.py:479` (D3, `retrieved_chunks` 없음) | `AxisScore(0.5, [], "…중립값")` | 참고용(이미 유사 패턴 존재) | D3는 이미 0.0/1.0이 아닌 "중립값 0.5"로 명시적 회피값을 두고 있다 — `abstain`은 이 패턴을 정식 필드로 승격하는 것과 유사한 발상. 이번 배치 대상 아님(사용자가 D1만 특정). |
| `layer3_detect.py:555` (D5, 시계열 엣지 없음) | `AxisScore(0.0, [], "시계열 엣지 없음")` | 참고(D1과 동일 패턴) | 이번 배치 대상 아님(사용자가 D1 반환 지점만 특정). |

## 2. `AxisScore` 사용처 grep 전수 (Step 1 영향 범위)

**생성(`AxisScore(...)`) 지점:**
- `esgenie/layer3_detect.py:389,448,463,479,491,498,523,555,559` (D1/D2/D3/D5 전부)
- `esgenie/ssot/detector_5axis.py:76,79,83,97,104,238`(마지막은 `aggregate`용 `AxisScore` 재사용 — `detect_risk_axes`의 반환 dict 내 `"aggregate"` 키)
- `esgenie/layer3_judge.py:199`(judge 결과 블렌딩 후 재생성)
- `esgenie/layer4_verify.py:377`(빈 텍스트 폴백 `AxisScore(score=0.0)`)
- `esgenie/calibrate.py:139`(시뮬레이션용 `AxisScore(score=blended, evidence=[], detail="")`)
- `tests/test_layer3_judge.py:20-23`(테스트 픽스처)

**직렬화(`to_dict`/`asdict`):** `schemas.py:24`(`AxisScore.to_dict`, 이번에 필드 추가되는 지점), `RiskVector.to_dict`(schemas.py:43-50, 각 축의 `to_dict()`를 호출하므로 자동으로 새 필드 포함됨).

**aggregate 가중합:** `layer3_detect.py:578`(`_build_risk_vector`, `D_WEIGHTS[k]*ax.score`), `layer3_judge.py:329`(`_rebuild_vector`, 동일 로직). **둘 다 `ax.score`만 참조하므로 abstain 필드를 추가해도 가중합 계산 자체는 변경되지 않는다** — 다만 "abstain인데 score가 0.6/0.9로 남아있으면 가중합에 그대로 섞여 들어간다"는 게 다음 배치(Step 3~4)에서 실제로 처리해야 할 문제. 이번 배치는 필드만 추가하므로 아직 그 상황 자체가 발생하지 않는다(어디서도 abstain=True를 세팅하지 않으므로).

**`top_axis` 선정:** `layer3_detect.py:588`, `layer3_judge.py:337` — `max(axes, key=lambda k: axes[k].score)`. 마찬가지로 score만 봄.

**`high_axes()`:** `schemas.py:64-74`, 소비처는 `layer4_verify.py:116`(`_axis_constraint_instruction`에서 재생성 프롬프트에 넣을 축 결정). 이번 배치에서 abstain 축을 제외하도록 최소 처리한다(사용자 지시).

## 3. 두 D1 경로의 근거없음/단위불일치 처리 — 실제로 다른 점

| 상황 | `ssot/detector_5axis.detect_d1_numeric` | `layer3_detect._score_d1_numeric` |
|---|---|---|
| 근거 자체가 없음(그래프 없음 / 코드에 대응하는 노드 전무) | **0.9(고위험)로 단정**(:83) | **0.0(안전)로 처리**(:389 evidence_graph 없음, 또는 :451 매칭 없음 시 worst_delta=0 유지) |
| 단위 불일치 | 노드는 있으나 `_find_matching_node`가 단위 호환성도 함께 검사(:284-293, `units_compatible` 재사용) → 매칭 실패 시 "미일치 수치"로 합산(0.5~1.0) | 후보 노드는 있으나 단위 비호환이면 해당 후보만 건너뛰고(:419-421) 문장 detail에 "단위 불일치(스킵)"만 남긴 채(:437-438) **다른 후보로 계속 탐색** — 끝내 아무 매칭도 없으면 결국 0.0으로 수렴 |
| 결론 | **"증빙이 없다" = 위험**이라는 태도(과잉판정 방향) | **"증빙이 없다" = 판정 불가지만 현재는 안전(0점)으로 코딩됨**(과소판정 방향, 즉 실제로는 "기권"이어야 할 상황이 "클린 통과"로 새고 있음) |

이 비대칭이 `docs/greenwash_baseline.md`(이전 조사)에서 지적한 "두 D1의 판정 기준이 다르다"는 것보다 한 단계 더 근본적인 문제다: 단순히 임계값(15% vs 2%)이 다른 게 아니라, **"근거 없음"이라는 동일한 상황에 대해 한쪽은 고위험(0.9), 다른 쪽은 안전(0.0)이라는 정반대 해석을 내린다.** 기권 필드가 도입되면 두 경로 모두 이 상황을 "판정 보류"로 수렴시킬 수 있는 공통 표현을 갖게 된다(다음 배치 대상).

## 4. 벤치의 Coverage 개념 — 이미 있음, 그러나 연결 안 됨

- `esgenie/evaluate.py:111-128`(`risk_coverage`)에 **이미 "coverage" 개념이 존재**한다. 단, 이는 실제 축별 기권 여부가 아니라 `confidence = |risk_score − threshold|` 기준으로 "결정 경계에서 먼 순서대로 자동판정하고 나머지는 사람에게 위임한다고 가정했을 때"의 **사후 시뮬레이션**이다(`evaluate.py:118-127`).
- `esgenie/benchmark.py`와 `scripts/held_out_eval.py`에는 `coverage`/`abstain` 키워드가 **전혀 없다**(grep 확인, 매치 0건). `held_out_eval.py:28`은 `evaluate`에서 `_case_rows, _prf, bootstrap_ci`만 임포트하고 `risk_coverage`는 가져오지 않는다 — 즉 현재 held-out 리포트에는 이 coverage 곡선조차 반영되지 않는다.
- 결론: **"기권 개념을 반영할 여지"는 있다**(evaluate.py에 유사한 프레임이 이미 있음) **그러나 실제 `AxisScore.abstain` 플래그와는 아직 연결되어 있지 않다.** Step 1에서 필드만 추가하는 이번 배치는 이 벤치 인프라에 영향을 주지 않는다(다음 배치인 Step 6에서 "진짜 기권 수/사유"를 Coverage·Accuracy·Overall 지표로 연결해야 함).

## 5. 요약

- 필드 추가만으로는 **어떤 기존 동작도 바뀌지 않는다** — 4개 생성 지점(D1/D2/D3/D5) 중 어디서도 아직 `abstain=True`를 세팅하지 않으므로.
- 가장 시급한 실제 문제(3절)는 두 D1 경로가 "근거 없음"을 정반대로 해석한다는 것 — 이번 배치 범위 밖이며, Step 3(다음 배치)에서 다뤄야 한다.
- `high_axes()`/`aggregate`에 대한 최소 처리(abstain 축 제외 + `abstained_axes` 목록)는 안전하다 — 현재 아무도 abstain을 세팅하지 않으므로 이 처리 코드 자체가 실행되어도 반환값은 기존과 동일하다(빈 목록이 하나 추가될 뿐).
