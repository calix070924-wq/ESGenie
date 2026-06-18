"""업종 모듈(세로축) 계약 + 레지스트리 + 타입별 리졸버.

설계 원칙
---------
- IndustryModule은 **코드가 아니라 데이터 컨테이너**다. 엔진 로직은 공통(가로축)에
  두고, 업종별 차이는 이 설정 객체로만 주입한다.
- resolution 규칙은 값 타입별로 분리한다(딕셔너리/용어목록/스칼라가 섞이므로):
    · 스칼라     → 업종값 있으면 업종값, 없으면 전역값          (resolve_scalar)
    · 맵         → 전역 복사 후 업종 키만 덮어쓰기              (resolve_map)
    · 용어 목록  → 전역 목록 + 업종 추가 목록(중복 제거)        (resolve_terms)
- **모듈 없음(None)이면 항상 기존 전역값 그대로** → 회귀 위험 0.
- 업종 문자열(DART 원문)과 모듈 키(정규화 식별자)를 분리한다. report.industry는
  자유 텍스트라 모듈 키로 직결하지 않고 infer_industry로 약하게 매핑한다.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Mapping


@dataclass(frozen=True)
class IndustryModule:
    """한 업종의 오버라이드 묶음. 비어 있는 필드는 전역 기본값으로 폴백된다."""

    key: str                       # 정규화 식별자 (예: "automotive_parts")
    label: str = ""                # 사람이 읽는 이름
    aliases: tuple[str, ...] = ()  # DART 원문 업종명 추론용 별칭

    # ── 오버라이드 데이터 (PR1: 자동차부품은 전부 빈 스켈레톤) ──────────────
    lexicon_extra: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    # 예: {"vague_environmental": ("친환경 경량화", "탄소중립 부품")}
    d6_omission_sensitivity: Mapping[str, float] = field(default_factory=dict)
    d6_ratio_context_pairs: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    emission_factors: Mapping[str, float] = field(default_factory=dict)
    # 예: {"kWh_to_tco2": ..., "MJ_gas_to_tco2": ...}
    thresholds: Mapping[str, float] = field(default_factory=dict)
    # 2차 이후: 출력 양식 매핑(CBAM/협력사). PR1에서는 선언만 하고 배선하지 않는다.
    output_mappings: Mapping[str, Mapping[str, str]] = field(default_factory=dict)


# ====================================================================
# 레지스트리
# ====================================================================

_REGISTRY: dict[str, IndustryModule] = {}


def register(module: IndustryModule) -> None:
    _REGISTRY[module.key] = module


def get_module(key: str | None) -> IndustryModule | None:
    """키로 모듈 조회. 미등록/None이면 None(엔진은 전역 폴백)."""
    if not key:
        return None
    return _REGISTRY.get(key)


def all_keys() -> tuple[str, ...]:
    return tuple(_REGISTRY)


def _norm(text: str) -> str:
    return text.strip().lower().replace(" ", "")


def infer_industry(report_industry: str | None) -> str | None:
    """DART 원문 업종명 → industry_key. 약한 alias 부분매칭, 실패하면 None.

    의도적으로 단순하게 둔다(똑똑한 자동분류는 디버깅을 어렵게 함). 못 맞추면
    그냥 None을 돌려 전역 동작으로 떨어지게 한다.
    """
    if not report_industry:
        return None
    needle = _norm(report_industry)
    for mod in _REGISTRY.values():
        for alias in mod.aliases:
            if _norm(alias) in needle:
                return mod.key
    return None


def resolve_module(
    active_industry: str | None,
    report_industry: str | None = None,
) -> IndustryModule | None:
    """모듈 선택: 명시값 우선 → alias 추론 → None.

    - active_industry가 주어지면 그 키로만 조회한다(미등록이면 None, alias로 더
      넘어가지 않음 — 명시 지정은 결정적이어야 하므로).
    - 없으면 report_industry를 alias로 약하게 추론한다.
    """
    if active_industry:
        return get_module(active_industry)
    return get_module(infer_industry(report_industry))


# ====================================================================
# 타입별 리졸버 — 전부 순수 함수, module=None이면 전역 기본값 반환
# ====================================================================

def resolve_scalar(module: IndustryModule | None, attr: str, default):
    """스칼라: 업종값이 있으면 업종값, 없으면 전역 default."""
    if module is None:
        return default
    val = getattr(module, attr, None)
    return val if val is not None else default


def resolve_map(module: IndustryModule | None, attr: str, default_map: Mapping) -> dict:
    """맵: 전역 default_map을 복사한 뒤 업종 키만 덮어쓴다(전역 키 보존)."""
    merged = dict(default_map)
    if module is not None:
        override = getattr(module, attr, None) or {}
        merged.update(override)
    return merged


def resolve_terms(
    module: IndustryModule | None,
    attr: str,
    default_terms: Iterable[str],
) -> list[str]:
    """용어 목록: 전역 + 업종 추가(중복 제거, 순서 보존).

    업종 필드가 카테고리별 맵(Mapping[str, tuple])이면 값들을 펼쳐서 합친다.
    """
    terms: list[str] = list(default_terms)
    if module is not None:
        extra = getattr(module, attr, None) or {}
        if isinstance(extra, Mapping):
            for vals in extra.values():
                terms.extend(vals)
        else:
            terms.extend(extra)
    seen: set[str] = set()
    out: list[str] = []
    for t in terms:
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out
