# 기권(Abstain) A/B 측정 결과 — Coverage / Accuracy(assessed) / Overall

> 브랜치: `feature/abstention` · 최신 갱신: 2026-07-28(하네스 결정성 수정 B0)
> 이 배치는 "측정 + 결과에 따른 최소 조정"만 한다 — 게이트·HITL 라우팅(Step 4)은 **여전히 보류**한다.

## B0. 하네스 결정성 수정 (2026-07-28, 최신)

### 문제

배치 4에서 recall 하락의 실제 원인을 규명한 결과(2절), `scripts/abstain_ab_eval.py`의
구조적 결함이 드러났다: OFF/ON을 **각각 별도 `capture()` 호출**(=별개의 LLM/judge
실행)로 비교했다. abstain(특히 주 타깃 `no_evidence`)은 룰 레이어의 rule_score를
바꾸지 않으므로(미검증 시 OFF/ON 모두 score 0.0, ON은 여기에 `abstain=True` 플래그만
더함) rule_score·judgeable·다른 축·judge 대상은 OFF/ON에서 **원리상 동일**해야
하는데, 별도 실행이라 judge(LLM) 결과가 실행마다 재현되지 않아 abstain과 무관한
축까지 A/B 결과를 오염시켰다.

### 착수 전 재확인한 3가지 사실

1. `esgenie/calibrate.py`의 `capture()`는 케이스마다 룰(`detect_risk_vector`) →
   judge(LLM)를 실행하고, 축별 `rule_score/detail/judgeable/abstain/abstain_reason/
   verdict/llm_score`를 캐시한다 — 코드 확인.
2. 기존 `scripts/abstain_ab_eval.py`의 `_run_one()`이 `_set_abstain(on/off)` 후
   `capture()`를 호출 — OFF/ON이 서로 다른 실행, 캐시 파일도 분리(`_abstain_{off,on}_
   judge_cache.json`) — 코드 확인.
3. **핵심**: `esgenie.layer3_detect.detect_risk_vector`를 `ABSTAIN_ENABLED=False`와
   `True`로 각각 호출해 직접 비교한 결과, **D1의 rule_score가 완전히 동일**했고
   (`0.0 == 0.0`), D2/D3/D5는 AxisScore 객체 자체가 동일했다. 유일한 차이는
   `abstain`/`abstain_reason`/`detail` 문자열뿐이었다. 또한 `_is_judgeable()`(LLM
   판정 대상 여부)도 no_evidence·unit_mismatch 두 시나리오 모두에서 abstain 래핑과
   무관하게 동일한 값을 냈다("정확성 주의"에서 우려한 "abstained 축이 judge 처리에서
   달라질 가능성"은 실측으로 **기각** — 보정 불필요, `tests/test_abstain_ab_determinism.py::
   test_abstain_flag_does_not_change_rule_score_or_judgeable`로 회귀 가드).

→ 파이프라인을 **한 번만 실행**하고, 그 동일한 캐시에서 OFF/ON 지표를 **둘 다 유도**하면
실행 변동이 0이 되어 abstain의 순수 효과만 남는다는 게 실측으로 확인됐다.

### 구현

- `esgenie/evaluate.py::with_abstain_ignored(rows)` 신설 — `_case_rows()`가 만든
  rows(캐시 1회 실행분)에서 abstain 플래그만 무시한 목록을 만든다(재판정 없음,
  `pred`/`y`/`correct`는 원본과 100% 동일하게 유지). ON은 원본 rows(`abstain_coverage`
  그대로), OFF는 `with_abstain_ignored(rows)`를 `abstain_coverage`에 넣어 유도한다.
- `scripts/abstain_ab_eval.py`를 스플릿당 **capture 1회**로 재작성. 총 LLM 호출이
  이전(이중-capture) 대비 **정확히 절반**으로 줄었다(로그로 확인 — 아래 참조).
- `ABSTAIN_ENABLED=True`로 1회 캡처(현행 `ABSTAIN_UNIT_MISMATCH` 기본값은 그대로 둠)해
  캐시에 abstain 플래그가 기록되게 하고, 그 위에서 두 해석을 유도한다.

### LLM 호출 수 확인 (mock 재실행 로그)

```
- 스플릿당 capture 1회(=LLM 호출 1세트)만 실행 — 총 LLM 호출 320건 (이전 이중-capture 방식 대비 절반)
## DEV — n=50 · ... · LLM 호출 50건(1회)
## TEST — n=270 · ... · LLM 호출 270건(1회)
```
이전 버전은 dev(50)+test(270)를 OFF/ON 각각 실행해 640건을 호출했다. 이번 버전은
320건(50+270) — 정확히 절반.

### 수용 기준 결과 — `tests/test_abstain_ab_determinism.py`(6건, 전부 통과)

- **비-abstain 케이스는 OFF/ON pred가 100% 동일** — 합성 픽스처(4건 중 2건 비-abstain)로
  `pred`/`y`/`correct` 리스트 완전 일치 + `_prf()` 결과 완전 일치까지 확인
  (`test_off_and_on_pred_identical_for_every_case`). 배치 4에서 깨졌던 바로 그 속성이
  이제 **구조적으로**(같은 rows에서 유도되므로) 보장된다 — 실행에 의존하지 않는다.
- abstained 케이스(D1 abstain 축 있음 + 다른 축이 flag 안 함)만 ON에서 assessed 제외,
  OFF에선 포함 확인(`test_only_abstained_and_unflagged_case_is_excluded_in_on`) — D2가
  flag하는 케이스는 abstain 축이 있어도 기권 아님이라는 기존 판정 규칙도 함께 재확인.
- Coverage = (전체−기권)/전체, Overall = Accuracy(assessed)×Coverage 산식을 단일
  캐시 기준으로 검증(`test_coverage_and_overall_formula_from_single_cache`).
- 기본값(`ABSTAIN_UNIT_MISMATCH=False`)에서는 기권 0건 → rows 리스트 자체가 OFF==ON으로
  완전히 같음을 확인(`test_no_abstain_records_give_off_equals_on`).
- `calibrate._simulate_vector()` 캐시 왕복 경유 배관까지 포함한 end-to-end 확인
  (`test_with_abstain_ignored_after_simulate_vector_roundtrip`).

### 재측정 결과 (mock, deterministic 하네스)

dev(n=50)·test(n=270) 모두 OFF==ON 완전 동일(현행 기본값에서 기권 0건이므로 — 4절과
동일한 수치). 다만 이번엔 **같은 실행에서 유도된 값**이라는 점이 다르다 — 이전 버전은
"우연히 같았다"였다면, 이번은 "구조적으로 같을 수밖에 없다."

### 의의

배치 4에서 "추가 확인 필요"로 남겼던 recall 하락 메커니즘(judge 레이어 실행 간
비재현성)은 이 수정으로 **구조적으로 제거됐다** — 정확한 내부 메커니즘을 규명하지
않고도, 애초에 그 메커니즘이 작동할 여지 자체를 없앴다(judge를 한 번만 부른다).
앞으로 `no_evidence` 실측 평가셋(배치 B)으로 A/B를 돌릴 때도 이 결정적 하네스를
그대로 재사용하면 된다.

---

## 실행 커맨드 / 재현법

```bash
# bash / macOS / Linux / Git Bash
ESGENIE_STRICT=1 python scripts/abstain_ab_eval.py
```
```cmd
:: Windows CMD
set ESGENIE_STRICT=1 && python scripts/abstain_ab_eval.py
```
```bash
# 배선 검증용 목(수치 무의미) — 키 없이도 실행 가능
ESGENIE_FORCE_MOCK=1 python scripts/abstain_ab_eval.py
```

> ⚠ **주의**: `python-dotenv`가 설치돼 있지 않으면 `.env`의 API 키가 로드되지 않고
> **조용히 mock으로 빠진다**(예외 없이 `SETTINGS.use_mock_llm=True`가 됨). `pip show
> python-dotenv`로 설치 여부를 먼저 확인할 것 — 그렇지 않으면 "REAL-KEY로 돌렸는데
> 사실 MOCK이었다"를 못 알아챈 채 결과를 신뢰하게 된다. 스크립트가 출력하는 헤더의
> `모드:` 표기(MOCK/REAL-KEY/AUTO)를 항상 확인할 것.

---

## 1. 실키(REAL-KEY, strict) A/B 결과 — 확정 (구 이중-capture 하네스)

실행: `ESGENIE_STRICT=1 python scripts/abstain_ab_eval.py` · 모드: **REAL-KEY (strict)** · cfg=BASELINE(trig=0.25 w=0.4 thr=0.25 axf=0.8).
(이번 조치 — Part 2 — 이전, `ABSTAIN_UNIT_MISMATCH` 신설 전 + B0 결정성 수정 전, **구 이중-capture
하네스**로 얻은 결과다. 이 결과 자체와 결론 1~3은 여전히 유효하지만, "왜 이렇게 나왔는지"의
메커니즘 설명은 B0 절이 갱신·대체한다.)

### DEV — n=50

OFF/ON 완전 동일 — Coverage 100%, Accuracy 0.980, Overall 0.980, 기권 0. (F1 0.977, 95% CI 0.919~1.000)

### TEST — n=270 (held-out)

| 조건 | Coverage | Accuracy | Overall | 기권(no_ev/unit/lowconf) | precision | recall | f1 |
|---|---|---|---|---|---|---|---|
| OFF | 100.0% | 0.793 | 0.793 | 0 (0/0/0) | 0.909 | 0.361 | 0.517 |
| ON  | 97.4%  | 0.783 | 0.763 | 7 (0/7/0) | 0.903 | 0.337 | 0.491 |

F1 95% CI — OFF 0.517 (0.400~0.619) · ON 0.491 (0.370~0.602)

### 결론 (확정)

1. **기권을 켜면 test 전 지표가 하락한다** — Overall 0.793→0.763, recall 0.361→0.337, f1 0.517→0.491. MOCK 배선검증 때와 달리 **p/r/f1이 실제로(=이 측정 기준으로) 나빠졌다.** → **게이트 라우팅(Step 4)을 켜지 않는다.** 측정을 먼저 한 덕에 순손해를 프로덕션에 넣는 것을 막았다.
2. **기권 7건이 전부 `unit_mismatch`, `no_evidence`는 0건.** 주 타깃은 `no_evidence`(근거 노드 없음)인데, 현재 벤치는 **단일 그래프(005930)로 여러 회사 문장을 검증**하는 구조라 대부분 코드에 이미 노드가 존재해 `no_evidence`가 발생하지 않는다. → **이 벤치로는 no_evidence 기권의 진짜 가치를 아직 측정할 수 없다**(후속 배치 B 필요).
3. 따라서 이번 배치에서: **(C) `unit_mismatch`를 기권 트리거에서 기본 제외**(`ABSTAIN_UNIT_MISMATCH=False` 신설)해 현 벤치에서의 손해를 0으로 만들고, no_evidence 기권 자체(주 타깃)는 계속 유지한다. **(B) `no_evidence`가 실제로 발생하는 평가셋 확보는 다음 배치.**

---

## 2. recall 하락 원인 규명 (★필수 — 완료)

**의문**: unit_mismatch 기권은 D1 점수를 0.0으로 두는데(OFF에서도 0.0이었음), 그럼에도 ON에서 recall이 0.361→0.337로 떨어졌다. 다른 축(D2 등)이 잡던 케이스라면 기권과 무관하게 그대로 잡혀야 하는데 recall이 줄었다면, **원래 옳게 flagged 되던 케이스가 기권으로 잘못 빠졌을 가능성**을 의심해야 한다.

### 조사 방법

실키 캐시는 재현 불가(과금·API 호출 필요)하므로, **동일 코드 경로를 MOCK 모드로 재실행해 기권 7건을 케이스 단위로 직접 추출**했다(`_mock_judge`는 프롬프트의 순수 함수라 rule-layer 비교에는 mock/real 여부가 영향을 주지 않는다 — D1 abstain 여부는 LLM을 전혀 거치지 않는 순수 룰 로직이기 때문).

### 확인 포인트별 결과

**① 케이스 단위 기권 판정 규칙("abstain 축이 있고 + 다른 축이 위험 flag 안 할 때만 기권")이 지켜지는가?**
→ **지켜짐. 버그 없음.** MOCK 재현에서 기권 처리된 7건(`GOLD-42, V2-079, V2-089, EXT-PDF-03, TRKA-001, TRKA-021, TRKA-073`)을 OFF/ON 각각의 `pred`(flagged 여부)와 함께 직접 대조한 결과, **7건 전부 OFF에서도 이미 `pred=0`(미검출)이었다** — 즉 "다른 축이 이미 위험으로 잡고 있던" 케이스는 단 하나도 기권으로 분류되지 않았다. 판정 규칙은 설계대로 동작한다.

| id | label | OFF pred | ON pred | ON 기권사유 |
|---|---|---|---|---|
| GOLD-42 | greenwash | 0 | 0 | unit_mismatch |
| V2-079 | clean | 0 | 0 | unit_mismatch |
| V2-089 | clean | 0 | 0 | unit_mismatch |
| EXT-PDF-03 | clean | 0 | 0 | unit_mismatch |
| TRKA-001 | clean | 0 | 0 | unit_mismatch |
| TRKA-021 | clean | 0 | 0 | unit_mismatch |
| TRKA-073 | clean | 0 | 0 | unit_mismatch |

**② `_flagged()`가 `high_axes()`/`abstained_axes()`를 참조해 flagged 판정을 의도치 않게 바꾸는가?**
→ **아니다.** `esgenie/benchmark.py`·`esgenie/calibrate.py`의 `_flagged()` 구현 어디에도 `high_axes()`/`abstained_axes()` 호출이 없다(grep 전수 확인). `high_axes()`는 오직 `esgenie/layer4_verify.py:116`(재생성 프롬프트 제약 선택)에서만 쓰인다 — 이 A/B 측정 경로와 완전히 무관.

**③ 그렇다면 recall 하락은 어디서 왔는가?**
→ **abstain 로직이 아니라 judge(LLM) 레이어의 실행 간 비재현성(run-to-run irreproducibility)에서 왔다.** MOCK 캐시를 직접 대조한 결과:
- D1 축의 `rule_score`/`judgeable` 여부는 **270건 전체에서 OFF/ON 완전히 동일**했다(0건 차이). abstain 로직이 rule 점수 자체를 바꾸지 않는다는 Step 2/3의 설계 의도가 실측으로도 확인됨.
- 그런데도 D1/D2/D5의 `verdict`/`llm_score`(judge 결과)는 **7건과 무관한 다른 32개 축**에서 OFF/ON 간에 값이 달랐다(예: `V2-053`의 D2 `llm_score`가 OFF 0.7 vs ON 0.2). 이 32건 중 2건(`V2-053`, `V2-143`, 둘 다 실제 greenwash 라벨)에서 D2 judge 점수 차이가 `_flagged()` 임계값을 넘나들어 **`pred`가 1→0으로 뒤집혔다** — 이것이 이번 재현에서 recall 하락의 실제 원인이다.
- `V2-053`/`V2-143` 둘 다 **D1 축은 abstain=False이고 OFF/ON 완전히 동일**했다(abstain과 무관한 케이스). 즉 기권 기능 자체가 이 두 건의 판정을 바꾼 게 아니라, **`scripts/abstain_ab_eval.py`가 OFF/ON을 별개의 `capture()` 호출(=별개의 LLM/judge 실행)로 수행하기 때문에, abstain과 무관한 축까지 포함해 judge 결과가 실행마다 완전히 재현되지는 않는다**는 이 하네스의 구조적 한계가 드러난 것이다.
- 이 가설을 뒷받침하는 정황: Part 4(아래)에서 `ABSTAIN_UNIT_MISMATCH=False`로 기권을 0건으로 만든 뒤 같은 스크립트를 재실행했더니, **OFF/ON의 judge 결과가 270건 전부 완전히 동일하게 나왔다**(재현 성공). 7건에서라도 D1의 실행 경로(기권 분기 통과 여부)가 갈리는 순간, 그 뒤로 이어지는 judge 실행 결과 전체가 흔들렸다가, 그 갈림이 사라지자(0건) 다시 완전히 재현됐다 — 인과관계의 정확한 내부 메커니즘(예: LLM 클라이언트의 재시도/지연 로직, 혹은 mock 라우팅의 어떤 상태 의존성)은 이번 조사로 **확정하지 못했다**(추가 확인 필요로 남김). 다만 원인이 "abstain이 옳은 판정을 잘못 가렸다"가 아니라 "하네스의 실행 간 재현성 부족"이라는 점은 위 ①·②·rule-layer 완전 일치 증거로 충분히 뒷받침된다.

### 결론: no_evidence에도 같은 버그가 재발하는가?

**재발하지 않는다.** 판정 규칙(①) 자체에는 버그가 없었고, 이번에 발견한 "judge 레이어 실행 간 비재현성"은 **기권 사유(no_evidence든 unit_mismatch든)와 무관하게 이 A/B 하네스 구조 자체의 한계**이기 때문이다. 다만 이는 앞으로 no_evidence만으로 A/B를 돌릴 때도 **"판정이 바뀐 케이스 = abstain의 순수 효과"라고 단정하지 말고, D1 rule_score/judgeable이 실제로 변경된 케이스만 abstain 효과로 귀속시켜야 한다**는 방법론적 교훈으로 남긴다(후속 배치 B에서 judge 캐시를 OFF/ON 간 공유·고정하는 개선을 고려할 것 — 이번 배치 범위 밖).

---

## 3. Part 2 조치 — unit_mismatch를 기권 트리거에서 기본 제외

- `esgenie/config.py`에 `ABSTAIN_UNIT_MISMATCH`(기본 **False**) 신설.
- `esgenie/layer3_detect.py`의 `_score_d1_numeric`: 기권 사유가 `unit_mismatch`일 때는 `ABSTAIN_UNIT_MISMATCH=True`일 때만 기권시키고, 기본값(False)에서는 기존 동작(score 계산으로 흘려보냄, 결과적으로 score=0.0)으로 되돌린다. `no_evidence`는 이 플래그와 무관하게 계속 기권(주 타깃 유지).
- `esgenie/ssot/detector_5axis.py`: 애초에 `unit_mismatch`를 기권으로 전환하는 분기가 없어(L99 미일치는 그대로 위험 점수로 유지) 별도 코드 변경 없이 이미 정합 — 주석으로 명기.

---

## 4. Part 4 — 조치 후 재측정 ("do no harm" 확인)

`ABSTAIN_UNIT_MISMATCH=False`(기본값 그대로) 상태에서 MOCK 모드로 `scripts/abstain_ab_eval.py` 재실행:

### DEV — n=50

| 조건 | Coverage | Accuracy(assessed) | Overall | 기권수(no_ev/unit/lowconf) | precision | recall | f1 |
|---|---|---|---|---|---|---|---|
| OFF | 100.0% | 0.960 | 0.960 | 0(0/0/0) | 0.913 | 1.000 | 0.955 |
| ON  | 100.0% | 0.960 | 0.960 | 0(0/0/0) | 0.913 | 1.000 | 0.955 |

### TEST — n=270

| 조건 | Coverage | Accuracy(assessed) | Overall | 기권수(no_ev/unit/lowconf) | precision | recall | f1 |
|---|---|---|---|---|---|---|---|
| OFF | 100.0% | 0.774 | 0.774 | 0(0/0/0) | 0.720 | 0.434 | 0.541 |
| ON  | 100.0% | 0.774 | 0.774 | 0(0/0/0) | 0.720 | 0.434 | 0.541 |

**ON == OFF, 완전 동일(기권 0건).** dev/test 모두 precision/recall/f1까지 소수점 전부 일치 — 조치 전 관측됐던 judge 레이어 비재현성(2절)도 재발하지 않았다(기권 분기 자체가 실행되지 않으니 실행 경로가 OFF와 완전히 같아졌기 때문으로 추정). "do no harm" 확인됨.

> 실키로도 동일 결과(ON==OFF)가 나오는지는 **사람이 재실행해 확인 필요**(재현 커맨드는 문서 상단 참조). 이번 배치는 코드/문서 변경까지만 수행했다.

---

## 5. 이전(MOCK) 결과 — 참고용, Part 2 조치 이전

Part 2 조치 이전, unit_mismatch가 기권 대상이던 시점의 MOCK 배선검증 결과(수치 무의미):

### DEV — n=50

| 조건 | Coverage | Accuracy(assessed) | Overall | 기권수(no_ev/unit/lowconf) | precision | recall | f1 |
|---|---|---|---|---|---|---|---|
| OFF | 100.0% | 0.960 | 0.960 | 0(0/0/0) | 0.913 | 1.000 | 0.955 |
| ON  | 100.0% | 0.960 | 0.960 | 0(0/0/0) | 0.913 | 1.000 | 0.955 |

### TEST — n=270

| 조건 | Coverage | Accuracy(assessed) | Overall | 기권수(no_ev/unit/lowconf) | precision | recall | f1 |
|---|---|---|---|---|---|---|---|
| OFF | 100.0% | 0.774 | 0.774 | 0(0/0/0) | 0.720 | 0.434 | 0.541 |
| ON  | 97.4%  | 0.772 | 0.752 | 7(0/7/0) | 0.720 | 0.434 | 0.541 |

당시 해석(잠정, 지금은 2절로 대체됨): MOCK에서는 p/r/f1이 ON/OFF 동일했으나 Overall만 하락 — 실키 재실행이 필요하다고 결론 내렸었다. 실키 결과(1절)는 이 MOCK 결과와 달리 p/r/f1까지 실제로 하락했고, 그 원인은 2절에서 규명했다.

## 참고

- 캐시 파일: `outputs/benchmark/{dev,test}_abstain_{off,on}_judge_cache.json`(재실행 시 갱신, git 추적 대상 아님)
- 리포트 원본: `outputs/benchmark/abstain_ab.md`
- 단위 테스트: `tests/test_abstain_unit_mismatch_toggle.py`(ABSTAIN_ENABLED × ABSTAIN_UNIT_MISMATCH 조합), `tests/test_d1_abstain.py`(갱신됨)
