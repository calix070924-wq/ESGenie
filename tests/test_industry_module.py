"""업종 모듈(세로축) PR1 — 회귀 가드 + 오버라이드 동작 테스트.

핵심 불변식: 모듈 없음(None) 또는 빈 스켈레톤이면 엔진은 전역 기본값과
**완전히 동일**하게 동작한다(회귀 0). 오버라이드가 주어진 경우에만 결과가 바뀐다.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from esgenie.industry import (
    IndustryModule,
    get_module,
    infer_industry,
    resolve_map,
    resolve_module,
    resolve_terms,
)
from esgenie.knowledge.greenwash_lexicon import ALL_VAGUE, vague_matches
from esgenie.layer3_detect import score_d2_modifier
from esgenie.layer3_disclosure import (
    OMISSION_SENSITIVITY,
    RATIO_CONTEXT_PAIRS,
    detect_selective_disclosure,
)
from esgenie.ssot.evidence_graph import (
    EvidenceGraph,
    EvidenceNode,
    _emit_derived_emission,
)


def _extraction(disclosed: list[str], missing: list[str]):
    return SimpleNamespace(
        mapped={c: {"code": c} for c in disclosed},
        missing=list(missing),
    )


def _kwh_node(value: float) -> EvidenceNode:
    return EvidenceNode(
        id=f"T_E-4-1_2025__dart", metric="E-4-1", value=value,
        unit="kWh", period=2025, source="test",
    )


# ====================================================================
# 1. 레지스트리 / 모듈 선택
# ====================================================================

def test_automotive_registered():
    mod = get_module("automotive_parts")
    assert mod is not None
    assert mod.key == "automotive_parts"
    assert mod.emission_factors["kWh_to_tco2"] > 0
    assert mod.emission_factors["MJ_gas_to_tco2"] > 0


def test_alias_inference_success():
    assert infer_industry("자동차부품 제조업") == "automotive_parts"
    assert infer_industry("자동차 부품") == "automotive_parts"


def test_inference_miss_returns_none():
    assert infer_industry("은행 및 저축기관") is None
    assert infer_industry("") is None
    assert infer_industry(None) is None


def test_explicit_overrides_inference_and_unknown_falls_back():
    # 명시 키가 추정보다 우선 — 알 수 없는 명시값이면 report 업종이 매칭돼도 None(전역 폴백).
    assert resolve_module("nonexistent", "자동차부품") is None
    # 명시 없음 → report 업종 추론 사용.
    assert resolve_module(None, "자동차부품").key == "automotive_parts"
    # 명시 알려진 키 → 그 모듈.
    assert resolve_module("automotive_parts", "은행업").key == "automotive_parts"
    # 둘 다 없음 → None.
    assert resolve_module(None, None) is None


# ====================================================================
# 2. 리졸버 단위 동작
# ====================================================================

def test_resolve_map_preserves_global_keys():
    mod = IndustryModule(key="t", d6_omission_sensitivity={"E-3-1": 0.1, "NEW-1": 0.9})
    merged = resolve_map(mod, "d6_omission_sensitivity", OMISSION_SENSITIVITY)
    assert merged["E-3-1"] == 0.1                 # 업종값으로 덮임
    assert merged["NEW-1"] == 0.9                 # 신규 키 추가
    assert merged["S-9-1"] == OMISSION_SENSITIVITY["S-9-1"]  # 미지정 전역 키 보존
    assert OMISSION_SENSITIVITY["E-3-1"] == 1.0   # 원본 불변


def test_resolve_terms_appends_without_dup():
    mod = IndustryModule(key="t", lexicon_extra={"env": ("친환경 경량화", "녹색")})
    terms = resolve_terms(mod, "lexicon_extra", ALL_VAGUE)
    assert "친환경 경량화" in terms                # 신규 추가
    assert terms.count("녹색") == 1               # 전역에 이미 있던 항목 중복 제거


def test_resolve_none_returns_defaults():
    assert resolve_map(None, "emission_factors", {"a": 1}) == {"a": 1}
    assert resolve_terms(None, "lexicon_extra", ["x"]) == ["x"]


# ====================================================================
# 3. 회귀: 모듈 없음 / 빈 스켈레톤 == 전역
# ====================================================================

SENTENCE = "우리 회사는 세계 최고의 친환경 경량화 부품을 만든다."


def test_lexicon_none_equals_global():
    assert vague_matches(SENTENCE) == vague_matches(SENTENCE, None)


def test_empty_skeleton_equals_global_lexicon():
    empty = get_module("automotive_parts")        # PR1 빈 스켈레톤
    assert vague_matches(SENTENCE, empty) == vague_matches(SENTENCE)


def test_empty_skeleton_equals_global_d6():
    ext = _extraction(disclosed=["E-6-2"], missing=["E-6-1"])
    empty = get_module("automotive_parts")
    assert (
        detect_selective_disclosure(ext, empty).score
        == detect_selective_disclosure(ext).score
    )


def test_empty_skeleton_equals_global_emission():
    g1, g2 = EvidenceGraph("T", "T"), EvidenceGraph("T", "T")
    _emit_derived_emission(g1, _kwh_node(1000.0))                          # 전역
    _emit_derived_emission(g2, _kwh_node(1000.0), get_module("automotive_parts"))
    assert g1.nodes_by_metric("E-3-1")[0].value == g2.nodes_by_metric("E-3-1")[0].value


# ====================================================================
# 4. 오버라이드가 실제로 결과를 바꾼다
# ====================================================================

def test_lexicon_extra_increases_d2_hits():
    mod = IndustryModule(key="t", lexicon_extra={"env": ("친환경 경량화",)})
    base = vague_matches(SENTENCE)
    boosted = vague_matches(SENTENCE, mod)
    assert "친환경 경량화" not in base
    assert "친환경 경량화" in boosted
    assert len(boosted) > len(base)
    # D2 점수도 히트 증가를 반영
    assert score_d2_modifier(SENTENCE, mod).score >= score_d2_modifier(SENTENCE).score


def test_d6_omission_override_changes_score():
    # 전역엔 없는 항목을 민감항목으로 지정 → 그 항목 누락 시 점수 상승.
    mod = IndustryModule(key="t", d6_omission_sensitivity={"E-2-2": 1.0})
    ext = _extraction(disclosed=[], missing=["E-2-2"])
    base = detect_selective_disclosure(ext).score
    over = detect_selective_disclosure(ext, mod).score
    assert over > base


def test_d6_ratio_pair_override_flags_orphan():
    # 전역에 없는 (비율→분모) 페어를 추가 → 분모 누락 시 고아비율 탐지.
    mod = IndustryModule(key="t", d6_ratio_context_pairs={"S-1-2": ("S-1-1",)})
    ext = _extraction(disclosed=["S-1-2"], missing=["S-1-1"])
    assert not detect_selective_disclosure(ext).orphan_ratios
    over = detect_selective_disclosure(ext, mod)
    assert any(o.ratio_code == "S-1-2" for o in over.orphan_ratios)


def test_emission_factor_override_changes_derived():
    mod = IndustryModule(key="t", emission_factors={"kWh_to_tco2": 1.0})
    g_base, g_over = EvidenceGraph("T", "T"), EvidenceGraph("T", "T")
    _emit_derived_emission(g_base, _kwh_node(1000.0))
    _emit_derived_emission(g_over, _kwh_node(1000.0), mod)
    assert g_over.nodes_by_metric("E-3-1")[0].value == 1000.0       # 1000 * 1.0
    assert g_base.nodes_by_metric("E-3-1")[0].value != 1000.0       # 전역 계수


def test_partial_emission_override_keeps_other_factor():
    # kWh만 덮어도 MJ(가스) 계수는 전역 폴백으로 남아야 함.
    mod = IndustryModule(key="t", emission_factors={"kWh_to_tco2": 1.0})
    g = EvidenceGraph("T", "T")
    node = EvidenceNode(id="T_E-4-1_2025__dart", metric="E-4-1", value=100.0,
                        unit="MJ", period=2025, source="test")
    _emit_derived_emission(g, node, mod)   # MJ 경로 — 전역 계수로 산출돼야 함(KeyError 없이)
    assert g.nodes_by_metric("E-3-1")        # 파생 노드 생성됨
