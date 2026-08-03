# 클코 실행 프롬프트 — no_evidence 기권 다음 단계 (실키 측정 → 게이트 결정)

아래 블록을 그대로 Claude Code에 붙여넣어 실행한다.

---

## 배경 (이미 완료된 것 — 재작업 금지)

`feature/abstention` 계열 작업으로 no_evidence 기권의 **순수 효과 측정 하네스**가 이미 커밋돼 있다:

- `data/benchmark_v2/abstain_probe.json` — 통제 probe 12케이스. 각 케이스는 `omit_codes`
  코드를 그래프에서 제거해 "해당 지표 미공시"를 재현 → D1 no_evidence 기권을 결정적으로 발동.
- `scripts/abstain_probe_eval.py` — OFF/ON A/B. `evaluate.abstain_coverage/with_abstain_ignored`
  재사용. 산출: Coverage·Accuracy(assessed)·Overall + 기권 정밀도(save/waste) + 현실 prevalence 투영.
- `tests/test_abstain_probe.py` — 회귀 5건(전부 통과). 기존 abstain 테스트 35건도 통과.

**mock 실행 결과 (결정적):** 기권 10건 전부 no_evidence, 정밀도 p=0.60(save 6 / waste 4),
Accuracy(assessed) 0.50→1.00, Overall 0.50→0.167.

**확정된 핵심 성질:** 이 A/B 설계에서 `Overall = 정답assessed / N` 이라 기권으로는
**절대 오르지 않는다**(단조 비증가). 기권의 가치("미탐→검토 전환")는 Overall이 구조적으로
못 본다 → 채택 판정은 **기권 정밀도 p**와 **비용비 B/R > (1−p)/p** 로 한다. 상세: `docs/abstain_probe_result.md`.

## 이번에 할 일 — 딱 2가지

### 작업 1. 실키로 probe 재실행 (수치 확정)
```bash
OPENAI_API_KEY=... ESGENIE_STRICT=1 PYTHONPATH=. python scripts/abstain_probe_eval.py
```
- mock과 동일한 구조가 나와야 정상(probe는 조용한 수치주장이라 D1 룰/기권만으로 판정 →
  실키에서도 값이 거의 같아야 함). 만약 크게 다르면 그 원인을 `docs/abstain_probe_result.md`
  하단에 "실키 편차" 절로 기록.

### 작업 2. **실제 벤치의 no_evidence 실태 측정** (다음 투자처를 가르는 게이트)
현행 `data/benchmark_v2/{dev,test}.json`(n=320)에서 no_evidence 기권이 실제로 몇 건 발생하며,
그것이 **"진짜 미공시"인지 "검색 실패(리포트엔 있는데 못 찾음)"인지** 분해한다.

구현 지침:
- `ABSTAIN_ENABLED=True`로 dev+test를 `calibrate.capture()` 흐름으로 1회 실행(또는
  `scripts/abstain_ab_eval.py` 재사용). 각 케이스의 D1 `abstain_reason=="no_evidence"` 건을 수집.
- 각 no_evidence 케이스에 대해 **검색 실패 vs 진짜 미공시**를 판정:
  - 해당 문장의 topic code로 `evidence_graph.search_nodes([code])`가 비어 있는지 확인.
  - **추가 확인**: 원본 리포트 텍스트(`load_report(...)`의 원문/청크)에 그 지표 수치가
    실제로 존재하는데 그래프 노드로만 안 올라왔다면 → **검색 실패**로 분류.
    (예: 키워드 매핑 누락, L0 그래프 빌드 시 해당 지표 파싱 실패)
  - 리포트 원문에도 그 지표가 아예 없으면 → **진짜 미공시**.
- 결과를 `docs/abstain_realworld_prevalence.md`에 표로 기록:
  no_evidence 총건수 / 진짜 미공시 n / 검색 실패 n / 각 케이스 id·code·문장.

## 게이트 (작업 2 결과로 자동 결정 — 결과 없이는 착수 금지)

- **검색 실패가 다수** → 경로 (2) **prevalence↓**: `esgenie/rag_gates/retrieval_gate.py`와
  L0 그래프의 지표 파싱/키워드 매핑을 보강해, 리포트에 실재하는 수치가 기권으로 새는 것을 막는다.
  (기권은 '정말 미공시'만 남긴다 → waste 감소 + 실제 탐지 커버리지 상승)
- **진짜 미공시가 다수** → 경로 (1) **정밀도↑**: no_evidence라도 위험신호 동반 시에만 기권하도록
  게이트를 조인다(예: D2 모호어 밀도 또는 D3 의미이탈이 함께 임계 초과인 미검증 수치만 기권).
  `layer3_detect._score_d1_numeric`의 no_evidence 분기(현재 `reason=="no_evidence"`면 무조건 기권)에
  동반신호 조건을 AND로 추가. clean 오기권(waste)을 줄여 coverage 비용을 낮춘다.

## 제약
- **임계값(BASELINE)은 test에서 재튜닝 금지** — held-out 무결성 유지.
- 게이트/HITL 라우팅(Step 4)은 이번에도 **건드리지 않는다** — 측정·정밀도 조정까지만.
- 변경 시 `tests/test_abstain_probe.py` + 기존 abstain 테스트 전부 통과 유지. 새 동작은 회귀 테스트 추가.
- 모든 수치 결론은 실키 실행 로그로 뒷받침하고 mock 값과 구분해 표기.

---
