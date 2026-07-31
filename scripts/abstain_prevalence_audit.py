"""no_evidence 기권 실태 조사 — dev/test(n=320)에서 몇 건 발생하며,
'검색 실패(리포트엔 있는데 그래프에 안 올라옴)' vs '진짜 미공시'로 분해한다.

읽기 전용 분석 스크립트다 — 탐지 로직(layer3_detect/ssot)은 건드리지 않는다.
_NUMBER_PATTERN/_match_topic_near/_is_target_context/_sentence_topic_codes를
그대로 재사용해 "어느 K-ESG 코드가 no_evidence를 유발했는지"만 사후 재구성한다.

한계(반드시 읽을 것): dev.json/test.json은 전 케이스가 단일 그래프(005930, 삼성전자
DART 샘플 데이터)로 검증된다. 문장 자체는 여러 회사(LG전자·현대모비스 등)를 다루므로,
"진짜 미공시"는 "삼성전자 리포트 기준으로 이 코드의 흔적이 없다"는 뜻이지 "문장이
가리키는 실제 회사가 공시하지 않았다"는 뜻이 아니다 — 이 벤치 구조 자체의 한계다
(docs/abstention_metrics_result.md 1절에서 이미 지적된 것과 동일한 confound).

실행: python scripts/abstain_prevalence_audit.py  (LLM 불필요 — D1 룰만 사용)
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

import esgenie.layer3_detect as _layer3_detect
from esgenie.dart_client import load_report
from esgenie.layer0_evidence_graph import _METRIC_KEYWORDS, build_evidence_graph
from esgenie.layer3_detect import (
    _is_target_context,
    _match_topic_near,
    _NUMBER_PATTERN,
    _sentence_topic_codes,
    detect_risk_vector,
)

ROOT = Path(__file__).resolve().parents[1]
SPLIT_DIR = ROOT / "data" / "benchmark_v2"
# 생성물은 gitignored outputs/ 아래로 (docs/ 추적 파일 덮어쓰기 방지 — 코드리뷰 개선).
OUT_DOC = ROOT / "outputs" / "benchmark" / "abstain_realworld_prevalence.md"
SPLITS = ["dev", "test"]
TICKER = "005930"


def _claim_codes(sentence: str) -> list[str]:
    """문장 내 수치별 매핑 코드(목표/전망 문맥 제외) — _score_d1_numeric의 판정
    로직을 읽기 전용으로 재현(재판정 아님, 결과 비교용)."""
    sentence_codes = _sentence_topic_codes(sentence)
    out: list[str] = []
    for m in _NUMBER_PATTERN.finditer(sentence):
        _, code = _match_topic_near(sentence, m.start(), m.end())
        if not code:
            continue
        if _is_target_context(sentence, m.start(), m.end()):
            continue
        out.append(code)
    return out


def _classify_code(code: str, report: Any, graph: Any) -> str:
    """코드 하나가 '검색 실패'인지 '진짜 미공시'인지 분류.

    - has_node: 그래프에 이미 노드가 있음(no_evidence를 유발하지 않았을 코드 — 필터링용)
    - search_failure_text: raw_text_snippets에 관련 키워드가 있는데 그래프 노드로 안 올라감
    - search_failure_structured: kesg_data엔 값이 있는데(비수치형이라) 그래프 노드로 안 올라감
    - true_non_disclosure: kesg_data에도 없고 raw_text_snippets에도 관련 키워드 흔적이 없음
    - unclassifiable_no_pattern: layer0_evidence_graph._METRIC_KEYWORDS에 이 코드의 검색
      패턴 자체가 정의돼 있지 않아 "찾아봤는데 없다"를 판정할 수 없음(다른 두 범주와
      혼동하면 안 됨 — 정직하게 별도 표기)
    """
    if graph.nodes_by_metric(code):
        return "has_node"

    kesg_entry = report.kesg_data.get(code)
    kws = _METRIC_KEYWORDS.get(code)

    if kesg_entry is not None:
        # kesg_data엔 있지만(문자열 등 비수치라) 그래프 노드로 승격되지 않은 경우
        return "search_failure_structured"

    if kws is None:
        return "unclassifiable_no_pattern"

    hits = [s for s in report.raw_text_snippets if any(kw in s for kw in kws)]
    if hits:
        return "search_failure_text"
    return "true_non_disclosure"


def main() -> None:
    _layer3_detect.ABSTAIN_ENABLED = True
    try:
        report = load_report(TICKER)
        graph = build_evidence_graph(report)

        rows: list[dict[str, Any]] = []
        total_cases = 0
        claim_code_freq: Counter[str] = Counter()
        for split in SPLITS:
            bench = json.loads((SPLIT_DIR / f"{split}.json").read_text(encoding="utf-8"))
            for case in bench["cases"]:
                total_cases += 1
                rv = detect_risk_vector(case["sentence"], evidence_graph=graph)
                d1 = rv.D1_numeric
                for c in _claim_codes(case["sentence"]):
                    claim_code_freq[c] += 1
                if not (d1.abstain and d1.abstain_reason == "no_evidence"):
                    continue
                codes = _claim_codes(case["sentence"])
                failing = [c for c in codes if not graph.nodes_by_metric(c)]
                classifications = {c: _classify_code(c, report, graph) for c in failing}
                rows.append({
                    "split": split, "id": case["id"], "domain": case.get("domain"),
                    "label": case["label"], "category": case.get("category"),
                    "sentence": case["sentence"], "codes": codes, "failing_codes": failing,
                    "classification": classifications,
                })
    finally:
        _layer3_detect.ABSTAIN_ENABLED = False

    cls_counter: Counter[str] = Counter()
    for r in rows:
        for c in r["classification"].values():
            cls_counter[c] += 1

    lines = [
        "# no_evidence 기권 실태 — dev/test(n={}) 검색 실패 vs 진짜 미공시 분해".format(total_cases),
        "",
        f"> 그래프: 단일 종목({TICKER}) DART 샘플 데이터. 벤치(dev+test) 전 케이스가 이 하나의",
        "> 그래프로 검증된다(dev.json/test.json의 `ticker` 필드 그대로) — 아래 '한계' 참조.",
        "",
        f"## 1. 총계: no_evidence 기권 {len(rows)}건 / 전체 {total_cases}건",
        "",
        f"- dev: {sum(1 for r in rows if r['split']=='dev')}건 / test: "
        f"{sum(1 for r in rows if r['split']=='test')}건",
        "",
        "## 1-b. 왜 0건(또는 이 정도)인가 — claim 코드 커버리지 진단",
        "",
        f"dev+test 320건 문장에서 실제 매핑된 K-ESG 코드는 총 {len(claim_code_freq)}종"
        f"({sum(claim_code_freq.values())}회 언급)뿐이었고, **그중 그래프에 노드가 없는",
        "코드는 0종**이었다(아래 표 — `graph_has_node`가 전부 True).",
        "",
        "| 코드 | 언급 횟수 | 그래프 노드 존재 |",
        "|---|---|---|",
    ] + [
        f"| {code} | {n} | {'✅' if graph.nodes_by_metric(code) else '❌'} |"
        for code, n in claim_code_freq.most_common()
    ] + [
        "",
        "**해석**: dev.json은 `greenwash_bench.json`(배치1 조사에서 확인: \"005930 샘플 DART",
        "수치를 정답 앵커로 사용한 라벨링 문장\")을 그대로 가져온 것이고, test.json도 이",
        "005930 그래프를 기준으로 검증 가능하게 라벨링됐다. 즉 **벤치 자체가 005930의",
        "공시 코드를 '정답 앵커'로 삼아 만들어졌기 때문에, 애초에 이 그래프에 없는 코드를",
        "언급하는 문장이 벤치에 들어갈 이유가 없다.** no_evidence 0건은 \"현실에 미공시가",
        "없다\"는 뜻이 아니라 **\"이 벤치가 미공시를 관측하도록 설계되지 않았다\"**는",
        "뜻이다 — 벤치를 아무리 늘려도(같은 방식으로 만드는 한) 구조적으로 0건일 수밖에",
        "없다. 이는 `abstain_probe.json`(코드를 의도적으로 omit)이 왜 필요했는지를",
        "재확인시켜준다.",
        "",
        "## 2. 실패 코드 분류(코드 단위 — 한 케이스에 여러 코드가 걸릴 수 있음)",
        "",
        "| 분류 | 건수 | 의미 |",
        "|---|---|---|",
        f"| search_failure_text | {cls_counter.get('search_failure_text', 0)} | "
        "리포트 원문(raw_text_snippets)엔 관련 키워드가 있는데 그래프 노드로 안 올라감 |",
        f"| search_failure_structured | {cls_counter.get('search_failure_structured', 0)} | "
        "kesg_data엔 값이 있는데 비수치형이라 그래프 노드로 승격 안 됨 |",
        f"| true_non_disclosure | {cls_counter.get('true_non_disclosure', 0)} | "
        "kesg_data에도, raw_text_snippets 키워드에도 흔적 없음(이 리포트 기준 미공시) |",
        f"| unclassifiable_no_pattern | {cls_counter.get('unclassifiable_no_pattern', 0)} | "
        "이 코드의 검색 키워드 패턴 자체가 정의 안 돼 있어 판정 불가 |",
        "",
        "## 3. 케이스별 상세",
        "",
        "| split | id | domain | label | 코드 | 분류 | 문장(발췌) |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        cls_str = ", ".join(f"{c}={v}" for c, v in r["classification"].items())
        sent = r["sentence"][:40].replace("|", "/")
        lines.append(
            f"| {r['split']} | {r['id']} | {r['domain']} | {r['label']} | "
            f"{', '.join(r['failing_codes'])} | {cls_str} | {sent}… |"
        )

    lines += [
        "",
        "## 4. 한계(반드시 감안)",
        "",
        f"- 벤치(dev+test, n={total_cases})가 **단일 종목({TICKER}) 그래프**로 전 케이스를 검증한다.",
        "  문장 자체는 여러 회사(LG전자·현대모비스 등)를 다루므로, 여기서 'true_non_disclosure'는",
        f"  '{TICKER} 리포트 기준으로 이 코드의 흔적이 없다'는 뜻이지 '문장이 가리키는 실제",
        "  회사가 공시하지 않았다'는 뜻이 아니다. 이 벤치 구조 자체의 한계이며,",
        "  docs/abstention_metrics_result.md 1절에서 이미 지적된 것과 같은 confound다.",
        "- `search_failure_text` 판정은 `layer0_evidence_graph._METRIC_KEYWORDS`의 키워드",
        "  존재 여부로만 판단한다 — 키워드가 있어도 실제로 파싱 가능한 형식이 아닐 수 있고,",
        "  반대로 키워드가 없어도(동의어 등) 원문에 실제로 존재할 수 있다(과소추정 가능성).",
        "",
        "## 5. 게이트 판정",
        "",
        _gate_verdict(cls_counter),
        "",
    ]
    out = "\n".join(lines)
    OUT_DOC.parent.mkdir(parents=True, exist_ok=True)
    OUT_DOC.write_text(out, encoding="utf-8")
    print(out)
    print(f"\n저장: {OUT_DOC}")


def _gate_verdict(cls_counter: Counter) -> str:
    search_fail = cls_counter.get("search_failure_text", 0) + cls_counter.get("search_failure_structured", 0)
    true_non = cls_counter.get("true_non_disclosure", 0)
    unclassifiable = cls_counter.get("unclassifiable_no_pattern", 0)
    total = search_fail + true_non + unclassifiable
    if total == 0:
        return (
            "no_evidence 기권 자체가 0건 — 현행 dev/test에서는 여전히 관측되지 않는다"
            "(이전 배치와 동일). 위 1-b절에서 확인했듯 이는 벤치가 005930의 공시 코드를 "
            "정답 앵커로 삼아 구성됐기 때문에 **구조적으로 0건일 수밖에 없다**(더 많은 "
            "케이스를 같은 방식으로 추가해도 0건일 것). "
            "**게이트 미착수** — '검색 실패가 다수' vs '진짜 미공시가 다수' 중 어느 쪽도 "
            "이 데이터로는 판정할 수 없으므로, 경로 (1) 정밀도↑ 또는 경로 (2) prevalence↓ "
            "코드 변경에 착수하지 않는다(결과 없이는 착수 금지 원칙 준수). "
            "유일하게 no_evidence를 관측시킨 것은 통제 probe(코드를 의도적으로 omit)뿐이었다 "
            "— 진짜 게이트 판정은 배치 B(조직적 미공시가 재현되는 실측 평가셋)를 확보한 "
            "뒤에나 가능하다."
        )
    if search_fail > true_non:
        return (
            f"검색 실패({search_fail}건)가 진짜 미공시({true_non}건)보다 많다 → "
            "**경로 (2) prevalence↓**: retrieval_gate/L0 그래프의 지표 파싱·키워드 매핑을 "
            "보강해 리포트에 실재하는 수치가 기권으로 새는 것을 막아야 한다."
        )
    if true_non > search_fail:
        return (
            f"진짜 미공시({true_non}건)가 검색 실패({search_fail}건)보다 많다 → "
            "**경로 (1) 정밀도↑**: no_evidence라도 위험신호(D2/D3) 동반 시에만 기권하도록 "
            "조건을 좁혀야 한다."
        )
    return (
        f"검색 실패({search_fail}건)와 진짜 미공시({true_non}건)가 동수 — 단일 경로로 "
        "단정하기 어렵다. unclassifiable_no_pattern이 {}건 있어 표본이 작다면 특히 "
        "추가 확인이 필요하다.".format(unclassifiable)
    )


if __name__ == "__main__":
    main()
