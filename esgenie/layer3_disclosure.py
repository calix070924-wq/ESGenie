"""Layer 3.6 — D6 선택적 공시(Selective Disclosure) 탐지.

설계 배경
---------
학계는 그린워싱 측정을 두 축으로 본다(systematic review):
  1) decoupling(탈동조화)   — 말과 실제 성과의 괴리  → ESGenie D1/D2/D5가 담당
  2) selective disclosure   — 유리한 지표만 공개, 불리한 건 누락(cherry-picking)
본 모듈은 ESGenie가 비어 있던 (2)를 채운다. greenwatch.ai 등 기존 도구가
주로 (1)에 집중하는 것과 대비되는 차별점.

D1~D5가 **문장 단위**인 것과 달리 D6는 **문서(보고서) 단위** 탐지기다.
입력은 layer1_extract.ExtractionResult (어떤 K-ESG 항목이 공시/누락됐는지).

두 가지 신호
-----------
신호 A — 민감 항목 누락 (hidden trade-off):
  배출량·폐기물·오염물질·법규위반·산재율 등 "숨기고 싶은" 항목이
  프로파일 대상인데 누락됐는가. 항목별 민감도 가중치로 합산.

신호 B — 고아 비율 (orphan ratio, 가장 강한 신호):
  유리한 *비율*만 공시하고 그 분모/맥락이 되는 *총량·불리 항목*은 누락.
  예: 폐기물 재활용률(E-6-2)은 자랑하면서 폐기물 총량(E-6-1)은 침묵.
  분모 없는 비율은 전형적 cherry-picking.

점수는 0(정상)~1(강한 선택적 공시 의심). 룰 기반이라 결정적·재현 가능.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .knowledge.issb_mapping import mappings_for


# ====================================================================
# 메타데이터 — 민감 항목 가중치 & 고아 비율 페어
# ====================================================================

# 누락 시 그린워싱 의심도가 높은 항목(0~1). "불리 노출" 성격이 강할수록 큼.
OMISSION_SENSITIVITY: dict[str, float] = {
    # 환경 — 배출·오염·위반은 숨기고 싶은 대표 항목
    "E-3-1": 1.0,   # 온실가스 배출량 Scope1+2 (핵심)
    "E-3-2": 0.6,   # Scope3
    "E-6-1": 0.8,   # 폐기물 배출량
    "E-7-1": 0.7,   # 대기오염물질
    "E-7-2": 0.7,   # 수질오염물질
    "E-8-1": 1.0,   # 환경 법규 위반
    "E-2-1": 0.5,   # 원부자재 사용량(총량)
    "E-4-1": 0.5,   # 에너지 사용량(총량)
    "E-5-1": 0.4,   # 용수 사용량(총량)
    # 사회 — 산재·이직·침해·위반
    "S-4-2": 0.9,   # 산업재해율
    "S-2-3": 0.6,   # 자발적 이직률
    "S-8-2": 0.7,   # 개인정보 침해 및 구제
    "S-9-1": 1.0,   # 사회 법규 위반
    # 지배구조 — 위반·윤리
    "G-4-1": 0.7,   # 윤리규범 위반사항 공시
    "G-6-1": 1.0,   # 지배구조 법규 위반
}

# 유리한 비율 → 그 비율을 맥락화하는 분모/총량(불리) 항목.
# 비율은 공시했는데 맥락 항목을 누락하면 강한 cherry-picking 신호.
RATIO_CONTEXT_PAIRS: dict[str, list[str]] = {
    "E-4-2": ["E-3-1", "E-4-1"],   # 재생에너지 비율 → 총배출량 / 총에너지
    "E-6-2": ["E-6-1"],            # 폐기물 재활용률 → 폐기물 총량
    "E-5-2": ["E-5-1"],            # 재사용 용수 비율 → 용수 총량
    "E-2-2": ["E-2-1"],            # 재생 원부자재 비율 → 원부자재 총량
}

ORPHAN_RATIO_WEIGHT = 1.0   # 고아 비율 1건의 기여(민감도 환산)

_LEVELS = (("low", 0.25), ("medium", 0.50))  # 그 이상은 high
_ISSB_REQUIRED_DISCLOSURE_CODES = {"E-3-1", "E-3-2"}


# ====================================================================
# 결과 스키마
# ====================================================================

@dataclass
class OmittedItem:
    code: str
    name: str
    area: str
    sensitivity: float
    reason: str


@dataclass
class OrphanRatio:
    ratio_code: str          # 공시된 유리 비율
    ratio_name: str
    missing_context: list[str]   # 누락된 맥락/분모 항목 코드
    detail: str


@dataclass
class DisclosureReport:
    score: float                              # 0~1 선택적 공시 의심도
    level: str                                # low | medium | high
    omitted_sensitive: list[OmittedItem] = field(default_factory=list)
    orphan_ratios: list[OrphanRatio] = field(default_factory=list)
    asymmetry: dict[str, Any] = field(default_factory=dict)
    rationale: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "score": self.score, "level": self.level,
            "omitted_sensitive": [vars(o) for o in self.omitted_sensitive],
            "orphan_ratios": [vars(r) for r in self.orphan_ratios],
            "asymmetry": self.asymmetry,
            "rationale": self.rationale,
        }


# ====================================================================
# 탐지기
# ====================================================================

def _level(score: float) -> str:
    for name, bound in _LEVELS:
        if score < bound:
            return name
    return "high"


def detect_selective_disclosure(extraction: Any, industry_module=None) -> DisclosureReport:
    """ExtractionResult → 문서 단위 D6 선택적 공시 리포트.

    extraction: layer1_extract.ExtractionResult (mapped/missing/profile 보유)
    industry_module: 주어지면 민감항목 가중치/고아비율 페어를 업종값으로 덮어씀
                     (전역 키 보존 + 업종 키만 오버라이드). None이면 전역 그대로.
    """
    from .knowledge.kesg_items import ALL_ITEMS
    from .industry.base import resolve_map

    omission_sensitivity = resolve_map(
        industry_module, "d6_omission_sensitivity", OMISSION_SENSITIVITY)
    ratio_context_pairs = resolve_map(
        industry_module, "d6_ratio_context_pairs", RATIO_CONTEXT_PAIRS)

    item_by_code = {it.code: it for it in ALL_ITEMS}
    mapped: dict[str, Any] = getattr(extraction, "mapped", {}) or {}
    missing: list[str] = list(getattr(extraction, "missing", []) or [])
    missing_set = set(missing)
    disclosed_set = set(mapped.keys())

    # 프로파일 내 민감 항목만 대상(분모) — 누락도 프로파일 기준이므로 일관
    profile_codes = disclosed_set | missing_set

    # ── 신호 A: 민감 항목 누락 ──────────────────────────────────────
    omitted: list[OmittedItem] = []
    sens_omitted_weight = 0.0
    sens_total_weight = 0.0
    for code, w in omission_sensitivity.items():
        if code not in profile_codes:
            continue  # 이 회사 프로파일 대상이 아니면 누락으로 보지 않음
        sens_total_weight += w
        if code in missing_set:
            it = item_by_code.get(code)
            sens_omitted_weight += w
            omitted.append(OmittedItem(
                code=code, name=it.name if it else code,
                area=it.area if it else "?", sensitivity=w,
                reason="불리 노출 항목 누락(hidden trade-off)",
            ))

    signal_a = (sens_omitted_weight / sens_total_weight) if sens_total_weight else 0.0

    # ── 신호 B: 고아 비율 ───────────────────────────────────────────
    orphans: list[OrphanRatio] = []
    orphan_weight = 0.0
    for ratio_code, ctx_codes in ratio_context_pairs.items():
        if ratio_code not in disclosed_set:
            continue  # 유리 비율 자체를 공시하지 않았으면 cherry-picking 아님
        missing_ctx = [c for c in ctx_codes if c in missing_set]
        if missing_ctx:
            it = item_by_code.get(ratio_code)
            ctx_names = ", ".join(
                item_by_code[c].name for c in missing_ctx if c in item_by_code
            )
            orphans.append(OrphanRatio(
                ratio_code=ratio_code,
                ratio_name=it.name if it else ratio_code,
                missing_context=missing_ctx,
                detail=f"'{it.name if it else ratio_code}'은 공시하면서 맥락 항목({ctx_names}) 누락",
            ))
            orphan_weight += ORPHAN_RATIO_WEIGHT

    # 고아 비율은 가능한 페어 수로 정규화(없으면 0)
    signal_b = min(1.0, orphan_weight / max(len(ratio_context_pairs), 1) * 2.0)

    # ── 비대칭(참고 지표) ───────────────────────────────────────────
    favorable_disclosed = sum(1 for c in ratio_context_pairs if c in disclosed_set)
    asymmetry = {
        "favorable_ratios_disclosed": favorable_disclosed,
        "sensitive_items_omitted": len(omitted),
        "orphan_ratios": len(orphans),
    }

    # ── 종합 점수: 고아 비율(강신호)에 가중 ─────────────────────────
    score = round(min(1.0, 0.45 * signal_a + 0.55 * signal_b), 4)
    level = _level(score)

    parts: list[str] = []
    if orphans:
        parts.append(f"고아 비율 {len(orphans)}건(분모 없는 유리 비율)")
    if omitted:
        top = sorted(omitted, key=lambda o: o.sensitivity, reverse=True)[:3]
        parts.append("민감 항목 누락: " + ", ".join(f"{o.name}" for o in top))
    issb_note = _issb_required_omission_note(missing)
    if issb_note:
        parts.append(issb_note)
    rationale = (
        "선택적 공시 의심 신호 없음" if not parts
        else f"[{level.upper()}] " + " | ".join(parts)
    )

    return DisclosureReport(
        score=score, level=level,
        omitted_sensitive=omitted, orphan_ratios=orphans,
        asymmetry=asymmetry, rationale=rationale,
    )


def _issb_required_omission_note(missing_codes: list[str]) -> str:
    """ISSB 기후 의무 공시 항목 누락 시 보강 근거를 반환한다."""
    for code in missing_codes:
        if code not in _ISSB_REQUIRED_DISCLOSURE_CODES:
            continue
        if any(mapping.standard == "S2" for mapping in mappings_for(code)):
            return "IFRS S2는 Scope1·2·3 공시를 요구 — 누락은 선택적 공시 신호"
    return ""
