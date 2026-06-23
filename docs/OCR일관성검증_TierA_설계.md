# OCR 일관성 검증 (Tier A — 문서 내부 산술 정합) 설계

작성: 2026-06-22 / 범위: PR1(본선 시연) / 대상 채널: `DocChannel.STRUCTURED`

## 0. 한 줄 요약

중소기업 증빙엔 DART 같은 외부 비교 기준이 없다. 그래서 일관성 검증은
**문서 내부의 산술 불변식(합계·곱·변환·절사)** 으로 OCR 오인식을 잡는다.
탐지+플래깅이 기본, 잔차가 결정적으로 설명되는 케이스만 auto-correct.

---

## 1. 왜 기존 cross_check로는 안 되는가

`evidence_graph._link_cross_check()` 는 **DART 노드 ↔ OCR 노드**를 같은
metric/period로 묶어 오차를 본다(D1 교차검증 재료). 그러나 공급망 실사 대상인
중소 협력사(예: 한울정밀공업)는 **DART 공시가 존재하지 않는다.** 비교 대상 노드가
없으니 이 엣지는 생성되지 않고, 일관성 검증이 사실상 비어 있다.

→ 결론: 외부 비교가 아니라 **문서가 스스로 만족해야 하는 산술 항등식**을
검증 기준으로 삼는다. 이건 비교 데이터가 1건도 없어도 항상 작동한다.

---

## 2. 검증 대상 — 데모셋 실측으로 본 불변식

아래 식은 한울정밀 증빙 01/02/03의 **실제 숫자로 모두 성립함을 확인**했다.
허용오차(tolerance)는 양식이 명시한 절사·반올림 규칙에서 도출.

### 2-1. 01 전기요금 청구서 (`kepco_bill`)

| 규칙 ID | 항등식 | 실측 검증 | 허용오차 |
|---|---|---|---|
| `kepco.usage_from_index` | (당월지침 − 전월지침) × 배율 = 사용량(kWh) | (50,586−48,210)×60 = 142,560 ✓ | 0 (정수) |
| `kepco.base_fee` | 계약전력 × 단가 = 기본요금 | 800 × 8,320 = 6,656,000 ✓ | 0 |
| `kepco.subtotal` | 기본+전력량+기후환경+연료비조정 = 전기요금계 | 6,656,000+15,752,880+1,283,040+712,800 = 24,404,720 ✓ | 0 |
| `kepco.vat` | 전기요금계 × 0.10 = 부가세 | 24,404,720×0.10 = 2,440,472 ✓ | ±1원(반올림) |
| `kepco.fund` | 전기요금계 × 0.037 = 기반기금 | 24,404,720×0.037 = 902,974.6 → 902,970 | 10원 절사 |
| `kepco.total` | 전기요금계+부가세+기금 = 청구금액 | 합 27,748,162 → **10원 절사** 27,748,160 ✓ | 10원 절사 |

### 2-2. 02 도시가스 요금 고지서 (`gas_bill`)

| 규칙 ID | 항등식 | 실측 검증 | 허용오차 |
|---|---|---|---|
| `gas.usage_from_index` | 당월지침 − 전월지침 = 사용량(㎥) | 40,000−31,580 = 8,420 ✓ | 0 |
| `gas.mj_conversion` | 사용량 × 보정계수 × 평균열량 = 사용열량(MJ) | 8,420×0.9942×43.1 = 360,773.6 → 360,772 ✓ | ±1 MJ(반올림) |
| `gas.usage_fee` | 사용열량 × 단가 = 사용요금 | 360,772×20.13 = 7,262,340.4 → 7,262,340 ✓ | ±1원 |
| `gas.supply_total` | 기본요금 + 사용요금 = 공급가액계 | 247,500+7,262,340 = 7,509,840 ✓ | 0 |
| `gas.vat` | 공급가액계 × 0.10 = 부가세 | 750,984 ✓ | ±1원 |
| `gas.total` | 공급가액계 + 부가세 = 청구금액 | 8,260,824 → 10원 절사 8,260,820 ✓ | 10원 절사 |

### 2-3. 03 사업장폐기물 위탁처리 명세서 (`waste_ledger`)

| 규칙 ID | 항등식 | 실측 검증 | 허용오차 |
|---|---|---|---|
| `waste.item_sum` | Σ품목 위탁수량 = 총 위탁량 | 3,400+800+1,200+900+5,200+2,100+3,000+1,800 = 18,400 ✓ | 0 |
| `waste.method_split` | 재활용 + 소각 + 매립 = 총 위탁량 | 5,400+6,100+6,900 = 18,400 ✓ | 0 |
| `waste.method_from_items` | 처리방법(R/D10/D9)별 품목 합 = 각 구분 합 | R:5,400 / 소각:6,100 / 매립:6,900 ✓ | 0 |
| `waste.recycle_rate` | 재활용량 ÷ 총량 = 재활용률 | 5,400/18,400 = 29.35% → 29.3% ✓ | ±0.1%p(반올림) |
| `waste.disposal_rate` | (소각+매립) ÷ 총량 = 소각·매립 비율 | 13,000/18,400 = 70.65% → 70.7% ✓ | ±0.1%p |

> 주: `waste.recycle_rate`는 D6(그린워싱) 검출과 별개다. 여기선 "증빙 내부 숫자가
> 서로 맞는가"만 본다. SAQ 주장(92%) vs 증빙(29.3%) 충돌은 기존 D6 경로가 담당.

---

## 3. 데이터 구조

```python
# esgenie/ssot/ocr_consistency.py (신설)

@dataclass(frozen=True)
class ConsistencyRule:
    rule_id: str               # "kepco.total"
    doc_type: str              # "kepco_bill"
    description: str
    inputs: tuple[str, ...]    # 식에 필요한 metric_hint 라벨들
    output: str                # 결과를 대조할 metric_hint
    tolerance_abs: float = 1.0 # 절대 허용오차(원/kg/MJ)
    tolerance_rel: float = 0.0 # 상대 허용오차(비율 필드용)
    truncate_won10: bool = False  # 10원 절사 규칙 적용

@dataclass
class ConsistencyFinding:
    rule_id: str
    severity: str              # "ok" | "warn" | "fail"
    metrics_involved: list[str]   # 관련 ExtractedMetric 식별자
    expected: float            # 식으로 계산한 값
    observed: float            # OCR이 읽은 값
    residual: float            # |expected - observed|
    residual_rel: float        # residual / expected
    suggested_fix: float | None    # 역산 가능할 때만
    auto_corrected: bool = False
    detail: str = ""
```

`ExtractedMetric`은 현행 유지. finding은 metric에 직접 안 넣고 별도 리스트로
모아서 extraction에 부착(아래 5절).

---

## 4. 판정·보정 로직

### 4-1. 판정

```
expected = rule.compute(metrics)        # 식 평가
observed = metrics[rule.output].value
if rule.truncate_won10:
    expected = floor(expected / 10) * 10
residual = abs(expected - observed)
ok = residual <= rule.tolerance_abs or
     (rule.tolerance_rel and residual/expected <= rule.tolerance_rel)
severity = "ok" if ok else "fail"
```

### 4-2. 결정적 auto-correct (잔차 역산)

합계/곱 규칙에서 **틀린 항이 하나뿐**일 때 역산으로 정답 복원이 가능하다.
다음 두 패턴만 auto-correct, 나머지는 `fail`로 두고 HITL.

1. **콤마/자릿수 단일 오인식**: observed와 expected의 비가 10ⁿ(±소수) 근방이거나,
   자릿수 하나 누락/추가로 잔차가 정확히 설명되면 → expected로 교체.
   예) 사용량 14,256(콤마 위치 오독) vs 검침역산 142,560 → ×10 일치 → 보정.
2. **합계 항 1개 누락**: 합계는 맞는데 항목 중 하나가 0/결측이면
   `누락항 = 합계 − Σ나머지` 로 복원.

auto-correct 시 `auto_corrected=True`, 원본값을 `detail`에 보존(감사 추적).
그 외 모든 불일치는 **고치지 않고 플래그만** — 안전·방어 우선 원칙.

### 4-3. confidence·HITL 연동

```
for f in findings:
    if f.severity == "fail" and not f.auto_corrected:
        for m in f.metrics_involved:
            metric.confidence = min(metric.confidence, 0.3)   # 강등
        extraction.router_meta["hitl_required"] = True
```

기존 `layer4`의 `HITL_REQUIRED` / `status` 패턴과 동일 시맨틱. 강등된 metric은
검증 큐(사람 확인)로 라우팅된다.

---

## 5. 통합 지점

`pipeline.py:_collect_ocr_extractions()` 안, **OCR 추출 직후 ~
`merge_ocr_extraction()` 호출 전**에 한 줄 삽입:

```python
ext = extract_document(path, decision)
findings = validate_consistency(ext)        # ← 신설
ext.router_meta["consistency_findings"] = [asdict(f) for f in findings]
apply_confidence_penalty(ext, findings)     # confidence 강등 + hitl 플래그
merge_ocr_extraction(graph, ext)            # 기존
```

evidence_graph 쪽은 무변경(스키마 하위호환). finding은 `router_meta`로 흘러가
UI(`ui/tabs.py`)에서 🚩 + bbox 하이라이트로 노출 — 기존 D6 표시 컴포넌트 재활용.

---

## 6. 테스트 계획 (`tests/test_ocr_consistency.py`)

1. **정상 통과**: 01/02/03 실측값 → 모든 규칙 `ok`, finding `fail` 0건.
2. **콤마 오인식 주입**: 청구금액 27,748,160 → 2,774,816 변조 → `kepco.total` fail,
   auto-correct로 복원 확인.
3. **합계 항 누락**: 폐기물 품목 1행 value=0 → `waste.item_sum` fail → 역산 복원.
4. **절사 경계**: 902,974 입력(절사 전) → tolerance 내 `ok` (false positive 없음).
5. **그럴듯한 오류는 못 잡음을 명시**: 18,400→18,500(합계도 같이 변조) →
   탐지 불가 케이스로 문서화(한계 회귀 테스트).

---

## 7. 한계 (명시)

- **합계까지 일관되게 틀린 값**(여러 항 동시 오독)은 못 잡는다 — 산술 사각지대.
- **누락 항목 복원**은 "합계가 정상일 때"만. 합계 자체가 안 읽히면 불가.
- **자유서술(비정형 채널)**엔 적용 안 됨 — Tier A는 정형 전용.

→ 그래서 목표는 "전수 교정"이 아니라 **정형 수치 OCR 오류의 체감 절반 이상을
결정적으로 잡고, 나머지는 confidence 강등으로 사람에게 넘기는 2단 안전망.**

---

## 8. 다음 단계(향후 PR)

- Tier B(단위·범위 불변식), Tier C(시계열 급변·배출계수 정합), Tier D(단위변환 중복).
- 규칙 정의를 `knowledge/`의 doc_type 템플릿과 통합해 단일 출처화.
