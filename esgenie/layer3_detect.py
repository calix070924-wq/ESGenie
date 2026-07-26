"""Layer 3 — 그린워싱 탐지.

v10 구조:
- 기존 detect(text, report) → DetectionResult 는 _legacy 로직을 래핑해 하위 호환 유지
- 신규 detect_risk_vector(
      claim_sentence, evidence_graph, retrieved_chunks, industry_stats, industry_module
  ) → RiskVector (D1~D5 분해 점수)
- DetectionResult에 risk_vector 필드 추가 (기본값 None)
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from .config import (
    D1_THRESHOLD, D2_THRESHOLD, D3_THRESHOLD, D5_THRESHOLD,
    D_WEIGHTS, RISK_LEVEL_THRESHOLDS,
)
from .dart_client import CompanyReport
from .embeddings import VectorIndex
from .knowledge.greenwash_lexicon import vague_matches
from .schemas import AxisScore, RiskVector

# ---- 수치 주장 패턴 --------------------------------------------------------
# lookbehind: 글자에 붙은 숫자는 수치 주장이 아니다 — "Scope3 배출량"에서 3+'배',
# "RE100", "IFRS S2" 류가 가짜 claim으로 뽑히는 걸 막는다 (2026-07-27).
# 배(?!출): '배출'의 '배'를 배수 단위로 오인하지 않는다.
_NUMBER_PATTERN = re.compile(
    r"(?<![0-9A-Za-z가-힣])"
    r"(?P<num>\d{1,3}(?:,\d{3})*(?:\.\d+)?)\s*"
    r"(?P<unit>%|억원|조원|tCO2eq|만\s*tCO2eq|톤|ton|TJ|%p|건|명|배(?!출))",
)

# ---- 토픽 인덱스 (kesg_items.search_terms 단일 출처) -------------------------
# 2026-07-27: 손으로 관리하던 13개 _KEYWORD_MAP을 제거하고 항목 정의의
# search_terms + name을 그대로 쓴다. 사전이 두 벌로 갈라지지 않고, E-2-1·E-2-2·
# E-3-2·E-7-1·E-8-1처럼 옛 사전에 아예 없던 항목이 자동으로 커버된다.
# (옛 사전은 13개 코드만 알았고 "Scope"→E-3-1 고정이라 Scope3를 Scope1+2로 보냈다.)

# 단위성 용어 — 지표명으로 오인되면 안 되는 것들. "7,929 TJ" 앞에 TJ가 있다는
# 이유로 E-4-1이 되면 안 되고(에너지 사용량이 근거여야 한다), "GHG"는 Scope1+2와
# Scope3를 가르지 못한다. 2자 이하(SS·TJ·MJ 등)는 아래 길이 조건에서 함께 걸린다.
_UNITISH_TERMS: frozenset[str] = frozenset({
    "tco2eq", "tco2", "co2eq", "co2", "kwh", "mwh", "gwh", "mj", "tj",
    "ghg", "re100", "nox", "sox", "bod", "cod", "ss", "전력량",
})
_TOPIC_MIN_LEN = 3   # 2자 이하 용어는 오매칭이 커 제외


def _norm_topic_text(s: str) -> str:
    """토픽 매칭용 정규화 — 공백 제거 + 소문자화. 텍스트·용어 양쪽에 같이 적용한다."""
    return re.sub(r"\s+", "", s or "").lower()


def _build_topic_terms() -> tuple[tuple[str, str, str], ...]:
    """(정규화 용어, topic 라벨, K-ESG 코드) 목록. **모듈 로드 시 1회**만 만든다.

    문장마다 재구축하면 D1이 문장 수 × 용어 수만큼 돌아 비용이 커진다.

    한 용어가 두 코드에 걸리면(예: "이직률" → S-2-1·S-2-3) 항목명이 그 용어를
    포함하는 쪽을 택한다(S-2-3 "자발적 이직률"). 그래도 안 갈리면 용어를 버린다
    (예: "제3자 검증" → E-3-3·P-3-1) — 임의 배정보다 비교를 건너뛰는 게 낫다.
    kesg_items._ALIAS_UNIQUE가 모호 별칭을 버리는 것과 같은 판단이다.
    """
    from .knowledge.kesg_items import ALL_ITEMS

    by_term: dict[str, list[tuple[str, str]]] = {}
    for item in ALL_ITEMS:
        for raw in (*item.search_terms, item.name):
            n = _norm_topic_text(raw)
            if len(n) < _TOPIC_MIN_LEN or n in _UNITISH_TERMS:
                continue
            # topic 라벨은 항목명 — 새 라벨 체계를 만들지 않는다(NumericClaim.topic 표시용).
            entry = (item.name, item.code)
            if entry not in by_term.setdefault(n, []):
                by_term[n].append(entry)

    terms: list[tuple[str, str, str]] = []
    for n, owners in by_term.items():
        if len(owners) > 1:
            narrowed = [o for o in owners if n in _norm_topic_text(o[0])]
            if len(narrowed) != 1:
                continue
            owners = narrowed
        name, code = owners[0]
        terms.append((n, name, code))
    return tuple(sorted(terms))


_TOPIC_TERMS: tuple[tuple[str, str, str], ...] = _build_topic_terms()

# 업종별 벤치마크 메트릭 키 → K-ESG 코드 대응
# ---- 데이터클래스 -----------------------------------------------------------

@dataclass
class NumericClaim:
    raw: str
    number: float
    unit: str
    topic: str | None
    matched_code: str | None
    sentence: str


@dataclass
class ClaimCheck:
    claim: NumericClaim
    dart_value: float | None
    dart_unit: str | None
    delta_pct: float | None
    verdict: str   # ok | mismatch | unverifiable | approximate


@dataclass
class DetectionResult:
    text: str
    sentences: list[str]
    numeric_claims: list[NumericClaim]
    claim_checks: list[ClaimCheck]
    vague_phrases: list[dict[str, Any]]
    semantic_similarity: float
    risk_score: float
    components: dict[str, float] = field(default_factory=dict)
    highlights: list[dict[str, Any]] = field(default_factory=list)
    risk_vector: RiskVector | None = None   # v10 신설


# ---- 공통 헬퍼 --------------------------------------------------------------

def _sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?。\n])\s+", text.strip())
    return [p.strip() for p in parts if p.strip()]


def _normalize_number(num: str, unit: str) -> tuple[float, str]:
    n = float(num.replace(",", ""))
    u = unit.replace(" ", "")
    if u.startswith("만"):
        n *= 10_000
        u = u[1:]
    return n, u


def _topic_spans(window_text: str) -> list[tuple[int, int, str, str]]:
    """창 안에서 겹치지 않는 토픽 스팬 목록 — (시작, 끝, topic, code).

    **leftmost-longest**로 겹침을 해소한다: 시작이 앞선 것 우선, 같으면 긴 것.
    최장 일치만 쓰면 안 되는 이유 — "재생에너지 사용 비율"에서 `재생에너지`(E-4-2)와
    `에너지사용`(E-4-1)이 겹치되 포함관계가 아니다. 길이만 보면 `에너지사용`이
    이겨 E-4-1로 잘못 간다. leftmost여야 E-4-2가 나온다(PR #44 G2와 같은 계열).
    """
    found: list[tuple[int, int, str, str]] = []
    for term, topic, code in _TOPIC_TERMS:
        start = 0
        while True:
            i = window_text.find(term, start)
            if i < 0:
                break
            found.append((i, i + len(term), topic, code))
            start = i + 1
    found.sort(key=lambda sp: (sp[0], -(sp[1] - sp[0])))

    kept: list[tuple[int, int, str, str]] = []
    for sp in found:
        if any(sp[0] < k[1] and sp[1] > k[0] for k in kept):
            continue   # 이미 채택한 스팬과 겹친다 — 버린다
        kept.append(sp)
    return kept


def _match_topic_near(sentence: str, span_start: int, span_end: int, window: int = 25) -> tuple[str | None, str | None]:
    """수치 주변 창에서 가장 가까운 지표 용어를 찾아 (topic, code)를 돌린다.

    before 창(수치 앞)을 먼저 보고 **끝이 가장 뒤**(= 수치에 가장 가까운) 스팬을 택한다.
    before가 비면 after 창에서 가장 앞선 스팬을 택한다.

    2026-07-27: 창에 아무 용어도 없으면 **None을 돌린다.** 예전에는 문장 전체를 훑어
    아무 키워드나 붙였는데(`kw in sentence`), 다지표 문장에서는 사실상 임의 배정이라
    "원부자재 102,462톤"을 폐기물 노드 72,463과 비교하는 오탐을 만들었다.
    호출부(`_score_d1_numeric`)는 code가 없으면 비교를 건너뛴다 — 정밀도 우선.
    """
    before = _norm_topic_text(sentence[max(0, span_start - window):span_start])
    spans = _topic_spans(before)
    if spans:
        best = max(spans, key=lambda sp: sp[1])
        return best[2], best[3]

    after = _norm_topic_text(sentence[span_end:span_end + window])
    spans = _topic_spans(after)
    if spans:
        best = min(spans, key=lambda sp: sp[0])
        return best[2], best[3]

    return None, None


def _norm_unit(u: str | None) -> str:
    return (u or "").replace(" ", "").lower()


# ---- 단위 호환성 ------------------------------------------------------------
# D1이 단위를 무시하고 값만 비교하면 "95.8 톤"이 "95.8 %" 노드와 일치 판정되거나,
# "% 목표치"가 절대량(tCO2eq) 노드와 비교되는 오류가 생긴다.

_UNIT_ALIASES: dict[str, str] = {
    "톤": "ton", "t": "ton",
    "tco2": "tco2eq", "tco₂eq": "tco2eq",
    "퍼센트": "%", "percent": "%",
    "킬로와트시": "kwh",
}


def canon_unit(u: str | None) -> str:
    """단위 정규화: 공백 제거·소문자·별칭 통일. 빈 문자열 = 단위 미상."""
    s = _norm_unit(u)
    return _UNIT_ALIASES.get(s, s)


def units_compatible(a: str | None, b: str | None) -> bool:
    """두 단위가 비교 가능한가. 어느 한쪽이 미상이면 허용(보수적), 둘 다 있으면 동일해야 함.

    NOTE: rag_gates/units.py에도 동명 함수가 있으나 시맨틱이 다름.
    여기는 '동일 단위 or 미상' 판정(탐지기용 보수적 비교),
    rag_gates 쪽은 '같은 환산 그룹(kWh↔MWh)' 판정(근거 게이트용 환산 비교).
    """
    ca, cb = canon_unit(a), canon_unit(b)
    if not ca or not cb:
        return True
    return ca == cb


# ---- 기존(legacy) 탐지 로직 -------------------------------------------------

def extract_numeric_claims(text: str) -> list[NumericClaim]:
    claims: list[NumericClaim] = []
    for sent in _sentences(text):
        for m in _NUMBER_PATTERN.finditer(sent):
            num_str, unit = m.group("num"), m.group("unit")
            n, u = _normalize_number(num_str, unit)
            topic, code = _match_topic_near(sent, m.start(), m.end())
            claims.append(NumericClaim(
                raw=f"{num_str} {unit}", number=n, unit=u,
                topic=topic, matched_code=code, sentence=sent,
            ))
    return claims


def _dart_numeric_value(report: CompanyReport, code: str) -> tuple[float | None, str | None]:
    entry = report.kesg_data.get(code)
    if not entry:
        return None, None
    v = entry.get("value")
    if isinstance(v, (int, float)):
        return float(v), entry.get("unit")
    return None, entry.get("unit")


def _compare_claim(claim: NumericClaim, report: CompanyReport) -> ClaimCheck:
    if not claim.matched_code:
        return ClaimCheck(claim=claim, dart_value=None, dart_unit=None,
                          delta_pct=None, verdict="unverifiable")
    dart_v, dart_u = _dart_numeric_value(report, claim.matched_code)
    if dart_v is None:
        return ClaimCheck(claim=claim, dart_value=None, dart_unit=dart_u,
                          delta_pct=None, verdict="unverifiable")
    if dart_u and claim.unit and _norm_unit(dart_u) != _norm_unit(claim.unit):
        return ClaimCheck(claim=claim, dart_value=dart_v, dart_unit=dart_u,
                          delta_pct=None, verdict="approximate")
    if dart_v == 0:
        delta = 0.0
    else:
        delta = (claim.number - dart_v) / abs(dart_v) * 100
    verdict = "ok" if abs(delta) <= 10 else "mismatch"
    return ClaimCheck(claim=claim, dart_value=dart_v, dart_unit=dart_u,
                      delta_pct=delta, verdict=verdict)


def detect_vague_phrases(sentences: list[str]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for s in sentences:
        phrases = vague_matches(s)
        if phrases:
            out.append({"sentence": s, "phrases": phrases})
    return out


def _mean_similarity(generated: str, report: CompanyReport) -> float:
    refs = list(report.raw_text_snippets) + [
        f"{e.get('note', '')} {e.get('value')} {e.get('unit', '')}"
        for e in report.kesg_data.values()
    ]
    if not refs:
        return 0.0
    idx = VectorIndex()
    from .embeddings import IndexedDoc
    idx.build([IndexedDoc(text=r, meta={}) for r in refs])
    hits = idx.search(generated, k=min(5, len(refs)))
    if not hits:
        return 0.0
    return float(np.mean([s for _, s in hits]))


def _legacy_score(
    claim_checks: list[ClaimCheck],
    vague: list[dict[str, Any]],
    similarity: float,
) -> tuple[float, dict[str, float]]:
    mismatch_cnt      = sum(1 for c in claim_checks if c.verdict == "mismatch")
    unverifiable_cnt  = sum(1 for c in claim_checks if c.verdict == "unverifiable")
    total_claims      = max(len(claim_checks), 1)
    vague_cnt         = sum(len(v["phrases"]) for v in vague)

    mismatch_score    = min(1.0, mismatch_cnt / total_claims * 2.0)
    unverifiable_score= min(1.0, unverifiable_cnt / total_claims)
    vague_score       = min(1.0, vague_cnt / 4.0)
    similarity_score  = max(0.0, 1.0 - similarity)

    weights = {"numeric_mismatch": 0.45, "unverifiable": 0.10,
               "vague_language": 0.25, "semantic_gap": 0.20}
    composite = (
        weights["numeric_mismatch"] * mismatch_score
        + weights["unverifiable"]   * unverifiable_score
        + weights["vague_language"] * vague_score
        + weights["semantic_gap"]   * similarity_score
    )
    components = {
        "numeric_mismatch": round(mismatch_score * 100, 1),
        "unverifiable":     round(unverifiable_score * 100, 1),
        "vague_language":   round(vague_score * 100, 1),
        "semantic_gap":     round(similarity_score * 100, 1),
    }
    return round(composite * 100, 1), components


def detect(text: str, report: CompanyReport) -> DetectionResult:
    """하위 호환 진입점. layer4_verify / app.py 호출 시 기존 인터페이스 유지."""
    sents  = _sentences(text)
    claims = extract_numeric_claims(text)
    checks = [_compare_claim(c, report) for c in claims]
    vague  = detect_vague_phrases(sents)
    sim    = _mean_similarity(text, report)
    risk, comps = _legacy_score(checks, vague, sim)

    highlights: list[dict[str, Any]] = []
    for c in checks:
        if c.verdict == "mismatch":
            highlights.append({
                "type": "mismatch",
                "sentence": c.claim.sentence,
                "claim": c.claim.raw,
                "dart_value": f"{c.dart_value} {c.dart_unit or ''}",
                "delta_pct": c.delta_pct,
            })
    for v in vague:
        highlights.append({"type": "vague", "sentence": v["sentence"], "phrases": v["phrases"]})

    return DetectionResult(
        text=text, sentences=sents, numeric_claims=claims,
        claim_checks=checks, vague_phrases=vague,
        semantic_similarity=sim, risk_score=risk,
        components=comps, highlights=highlights,
        risk_vector=None,   # v10: 문장별 분석은 detect_risk_vector() 사용
    )


def risk_band(score: float) -> str:
    if score < 25:
        return "LOW"
    if score < 50:
        return "MEDIUM"
    if score < 75:
        return "HIGH"
    return "CRITICAL"


# ---- v10 신설: 5축 위험 분해 ------------------------------------------------

def detect_risk_vector(
    claim_sentence: str,
    evidence_graph: Any | None = None,   # EvidenceGraph | None
    retrieved_chunks: list[dict[str, Any]] | None = None,
    industry_stats: dict[str, Any] | None = None,
    industry_module=None,
    _d3_index: Any | None = None,        # 외부에서 미리 빌드된 VectorIndex (재사용용)
) -> RiskVector:
    """단일 문장에 대한 4축 위험 분해 (D1·D2·D3·D5).

    Args:
        claim_sentence: 분석 대상 문장
        evidence_graph: L0 EvidenceGraph (없으면 D1·D5 스킵)
        retrieved_chunks: L2 RAG 청크 목록 [{"id":..., "text":...}]
        industry_stats:  사용 안 함 (하위 호환용 파라미터 유지)
        industry_module: 업종 모듈. D2 lexicon 확장에 사용, 없으면 전역 동작.
        _d3_index: 미리 빌드된 VectorIndex — 제공 시 D3에서 재빌드 생략

    Returns:
        RiskVector (D1·D2·D3·D5 + aggregate)
    """
    d1 = _score_d1_numeric(claim_sentence, evidence_graph)
    d2 = _score_d2_modifier(claim_sentence, industry_module)
    d3 = _score_d3_semantic(claim_sentence, retrieved_chunks, prebuilt_index=_d3_index)
    d5 = _score_d5_timeseries(claim_sentence, evidence_graph)

    return _build_risk_vector(d1, d2, d3, d5)


# ---- D1: 수치 오차 ----------------------------------------------------------

# 목표/전망 문맥 마커 — 이 문맥의 수치는 '실적 주장'이 아니므로 실적 노드와 비교하지
# 않는다 (2026-07-17: LG화학 E "2030년까지 재생에너지 100% 목표" vs 실적 72% 오탐).
# 보수적 목록 — '추진'·'강화' 같은 실적 서술에도 흔한 어휘는 제외.
_TARGET_CONTEXT_RE = re.compile(
    r"목표|계획|전망|예정|로드맵|공약|\d{4}\s*년\s*까지"
)
_TARGET_WINDOW = 40  # 수치 앞뒤로 살필 문자 수


def _is_target_context(sentence: str, start: int, end: int) -> bool:
    """수치 주변 창(window)에 목표/전망 마커가 있으면 True."""
    window = sentence[max(0, start - _TARGET_WINDOW):min(len(sentence), end + _TARGET_WINDOW)]
    return bool(_TARGET_CONTEXT_RE.search(window))


def _sentence_topic_codes(sentence: str) -> set[str]:
    """문장에 등장하는 모든 지표 용어의 K-ESG 코드 집합.

    `_match_topic_near`와 **같은 인덱스**를 쓴다(2026-07-27). 여기가 D1 교차 비교
    후보(`cand_codes`)를 만드는 곳이라, 인덱스가 갈리면 E-3-2 같은 신규 커버 코드의
    노드가 후보에 안 들어와 최근접 매칭만 남는다.
    여기는 문장 전체가 대상이므로 겹침 해소 없이 등장하는 용어를 모두 모은다.
    """
    text = _norm_topic_text(sentence)
    return {code for term, _topic, code in _TOPIC_TERMS if term in text}


def _repr_ids(evidence_graph: Any) -> dict[str, str]:
    """원장이 기록한 코드→대표 노드 id. 없는 그래프(테스트 Fake 등)는 빈 dict."""
    return getattr(evidence_graph, "representative_node_ids", None) or {}


def _ledger_representative(evidence_graph: Any, code: str, compat: list[Any]) -> Any | None:
    """원장이 채택한 대표 노드를 단위 호환 후보 안에서 찾는다. 없으면 None(폴백).

    None을 돌리는 경우 두 가지 — 호출부가 구분한다.
      · 기록 자체가 없다(미공시 코드, DART-only 경로, 원장 미실행)
      · 기록은 있으나 claim 단위와 환산군이 달라 compat에서 걸러졌다
    """
    node_id = _repr_ids(evidence_graph).get(code)
    if not node_id:
        return None
    for node in compat:
        if getattr(node, "id", None) == node_id:
            return node
    return None


def _score_d1_numeric(
    sentence: str,
    evidence_graph: Any | None,
) -> AxisScore:
    """claim 숫자 vs L0 노드값 상대 오차.

    2026-07-17 정밀도 개선 2건:
    - 목표/전망 문맥의 수치는 실적 노드와 비교하지 않는다 (목표 100% vs 실적 72% 오탐).
    - 다지표 문장 교차 오탐 차단: 수치를 최근접 코드 하나가 아니라 **문장 내 전체 토픽
      코드의 노드들과 비교해 최솟값 오차**를 쓴다. 배치 실측(5개사)에서 D1 만점의
      대부분이 "여성 비율 16.6을 옆 지표 노드 29.0과 비교" 류의 교차 매칭이었다.
      수치가 문장 내 어떤 관련 노드와도 안 맞을 때만 오차로 계산된다.
      (트레이드오프: 문장 내 다른 지표의 노드값과 우연히 일치하는 허위 수치는
      놓칠 수 있음 — 정밀도 우선. 재현율 보강은 D5·교차검증 엣지 몫.)
    """
    if evidence_graph is None:
        return AxisScore(score=0.0, evidence=[], detail="evidence_graph 없음 — 스킵")

    # G5. 노드 선택 기준 연도 — 그래프 report_year 우선, 없으면 후보 최신 연도 폴백.
    # 대표 노드는 원장이 그래프에 남긴 결정(representative_node_ids)을 우선 따르고,
    # 기록이 없을 때만 이 공용 함수로 규칙을 재실행한다(상세는 node_select 참조).
    from .ssot.node_select import select_representative_node

    ref_year = getattr(evidence_graph, "report_year", None)

    worst_delta = 0.0
    hit_node_ids: list[str] = []
    details: list[str] = []
    sentence_codes = _sentence_topic_codes(sentence)

    for m in _NUMBER_PATTERN.finditer(sentence):
        num_str, unit = m.group("num"), m.group("unit")
        claim_val, claim_unit = _normalize_number(num_str, unit)
        _, code = _match_topic_near(sentence, m.start(), m.end())
        if not code:
            continue
        if _is_target_context(sentence, m.start(), m.end()):
            details.append(f"{code}: claim={claim_val} — 목표/전망 문맥, 실적 비교 제외")
            continue

        # 후보: 최근접 코드 우선 + 문장 내 나머지 토픽 코드 (교차 오탐 방지)
        cand_codes = [code] + sorted(sentence_codes - {code})
        best: tuple[float, Any, str] | None = None  # (delta, node, code)
        any_nodes = False
        for c in cand_codes:
            nodes = evidence_graph.search_nodes(keywords=[c])
            if not nodes:
                continue
            any_nodes = True
            # 단위 호환 노드만 비교 대상 ("31 %" 주장을 tCO2eq 노드와 비교하지 않음)
            compat = [n for n in nodes if units_compatible(claim_unit, getattr(n, "unit", None))]
            if not compat:
                continue
            # G5. 대표 노드 — **원장이 그래프에 기록한 결정을 그대로 따른다**(2026-07-26).
            # 공용 함수(select_representative_node)를 공유하는 것만으로는 대칭이 성립하지
            # 않는다: 원장은 origin이 ocr_*인 노드만, 여기 compat는 search_nodes()로
            # DART 노드까지 포함한 풀이라 같은 규칙도 다른 노드를 가리킨다
            # (실측: 원장 623,648 '국내(별도)' vs D1 1,992,921 DART '합계').
            # 규칙을 두 번 돌리는 대신 결정을 공유하면 풀이 달라도 어긋날 수 없다.
            node = _ledger_representative(evidence_graph, c, compat)
            if node is None:
                if _repr_ids(evidence_graph).get(c):
                    # 기록은 있으나 claim과 환산군이 다르다 — 원장은 항목 정의 단위로
                    # 정규화하지만 노드는 원 단위다. 비교 자체가 무의미하므로 폴백한다.
                    details.append(f"{c}: 원장 대표노드와 단위 비호환 → 폴백")
                # 기록이 없는 코드(미공시·DART-only 경로 등)는 기존대로 규칙을 재실행한다.
                node = select_representative_node(c, compat, report_year=ref_year)
            if node is None or node.value == 0:
                continue
            delta = abs(claim_val - node.value) / abs(node.value)
            if best is None or delta < best[0]:
                best = (delta, node, c)

        if best is None:
            if any_nodes:
                details.append(
                    f"{code}: claim={claim_val}{claim_unit} — 단위 불일치(노드 단위와 비교 불가, 스킵)")
            continue

        delta, node, matched_code = best
        if delta > worst_delta:
            worst_delta = delta
        hit_node_ids.append(node.id)
        details.append(f"{matched_code}: claim={claim_val} vs node={node.value} (Δ={delta:.1%})")

    score = min(1.0, worst_delta / max(D1_THRESHOLD, 1e-9))
    return AxisScore(
        score=round(score, 4),
        evidence=hit_node_ids,
        detail="; ".join(details) if details else "수치 매칭 없음",
    )


# ---- D2: 모호어 밀도 --------------------------------------------------------

def _score_d2_modifier(sentence: str, industry_module=None) -> AxisScore:
    """greenwash_lexicon 모호어/최상급 밀도. industry_module이 있으면 업종 패턴 포함."""
    hits = vague_matches(sentence, industry_module)
    # 문장당 밀도: 히트 수 / threshold 정규화
    density = len(hits) / max(D2_THRESHOLD * 4, 1)   # 4개 = 만점 기준
    score = min(1.0, density)
    return AxisScore(
        score=round(score, 4),
        evidence=[],
        detail=f"모호어 {len(hits)}개: {hits[:5]}" if hits else "모호어 없음",
    )


# ---- D3: 의미 유사도 --------------------------------------------------------

def _score_d3_semantic(
    sentence: str,
    retrieved_chunks: list[dict[str, Any]] | None,
    prebuilt_index: Any | None = None,
) -> AxisScore:
    """SBERT cos-sim(claim, evidence chunk) 역수."""
    if not retrieved_chunks and prebuilt_index is None:
        return AxisScore(score=0.5, evidence=[], detail="retrieved_chunks 없음 — 중립값")

    from .embeddings import IndexedDoc, VectorIndex
    if prebuilt_index is not None:
        idx = prebuilt_index
        docs = idx._docs
    else:
        idx = VectorIndex()
        docs = [IndexedDoc(text=c.get("text", ""), meta={"id": c.get("id", "")}) for c in retrieved_chunks]
        idx.build(docs)
    hits = idx.search(sentence, k=min(3, len(docs)))
    if not hits:
        return AxisScore(score=1.0, evidence=[], detail="유사 청크 없음")

    best_sim = max(s for _, s in hits)
    best_chunk_id = hits[0][0].meta.get("id", "")
    # 유사도가 높을수록 안전 → score = 1 - sim (임계치 기준 정규화)
    raw_risk = max(0.0, D3_THRESHOLD - best_sim) / D3_THRESHOLD
    score = min(1.0, raw_risk)
    return AxisScore(
        score=round(score, 4),
        evidence=[best_chunk_id] if best_chunk_id else [],
        detail=f"최고 cos-sim={best_sim:.3f} (임계치 {D3_THRESHOLD})",
    )


def _extract_claim_value_for_code(sentence: str, code: str) -> float | None:
    """문장에서 특정 K-ESG 코드에 대응하는 수치 추출."""
    for m in _NUMBER_PATTERN.finditer(sentence):
        _, matched_code = _match_topic_near(sentence, m.start(), m.end())
        if matched_code == code:
            val, _ = _normalize_number(m.group("num"), m.group("unit"))
            return val
    return None


# ---- D5: 시계열 모순 --------------------------------------------------------

def _score_d5_timeseries(
    sentence: str,
    evidence_graph: Any | None,
) -> AxisScore:
    """L0 시계열 엣지의 YoY·CAGR 방향과 문장 주장 비교."""
    if evidence_graph is None:
        return AxisScore(score=0.0, evidence=[], detail="evidence_graph 없음 — 스킵")

    contradictions: list[str] = []
    edge_ids: list[str] = []

    for m in _NUMBER_PATTERN.finditer(sentence):
        _, code = _match_topic_near(sentence, m.start(), m.end())
        if not code:
            continue
        # 해당 코드의 timeseries 엣지 검색
        for edge in evidence_graph.edges:
            if code not in edge.target_id:
                continue
            if edge.edge_type != "timeseries":
                continue
            edge_ids.append(edge.target_id)
            if edge.yoy is None:
                continue
            # 문장에 "감소" / "증가" 방향이 엣지 YoY 방향과 일치하는지 확인
            sent_lower = sentence.lower()
            claim_down = any(w in sentence for w in ("감소", "하락", "절감"))
            claim_up   = any(w in sentence for w in ("증가", "상승", "개선"))
            yoy_down = edge.yoy < 0
            yoy_up   = edge.yoy > 0

            if (claim_down and yoy_up) or (claim_up and yoy_down):
                contradictions.append(
                    f"{code} 문장방향={'감소' if claim_down else '증가'} "
                    f"vs YoY={edge.yoy:+.1f}%"
                )

    if not edge_ids:
        return AxisScore(score=0.0, evidence=[], detail="시계열 엣지 없음")

    contradiction_ratio = len(contradictions) / max(len(edge_ids), 1)
    score = min(1.0, contradiction_ratio / max(D5_THRESHOLD, 1e-9))
    return AxisScore(
        score=round(score, 4),
        evidence=edge_ids[:5],
        detail="; ".join(contradictions) if contradictions else "시계열 모순 없음",
    )


# ---- aggregate 계산 ---------------------------------------------------------

def _build_risk_vector(
    d1: AxisScore, d2: AxisScore, d3: AxisScore,
    d5: AxisScore,
) -> RiskVector:
    axes = {
        "D1_numeric":    d1,
        "D2_modifier":   d2,
        "D3_semantic":   d3,
        "D5_timeseries": d5,
    }
    weighted = sum(D_WEIGHTS[k] * ax.score for k, ax in axes.items())
    risk_score = round(weighted, 4)

    if risk_score < RISK_LEVEL_THRESHOLDS["low"]:
        level = "low"
    elif risk_score < RISK_LEVEL_THRESHOLDS["medium"]:
        level = "medium"
    else:
        level = "high"

    top_axis = max(axes, key=lambda k: axes[k].score)

    return RiskVector(
        D1_numeric=d1, D2_modifier=d2, D3_semantic=d3,
        D5_timeseries=d5,
        aggregate={
            "risk_score": risk_score,
            "level":      level,
            "top_axis":   top_axis,
        },
    )


# ---- 공개 별칭 (esgenie.ssot 등 외부 모듈 재사용용) ---------------------------
score_d1_numeric = _score_d1_numeric
score_d2_modifier = _score_d2_modifier
score_d3_semantic = _score_d3_semantic
score_d5_timeseries = _score_d5_timeseries
