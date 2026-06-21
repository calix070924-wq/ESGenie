"""K-ESG 자가진단 — 캐노니컬 substrate 프레임워크 (STEP 5).

> 계획: `docs/다음작업_ESG커버리지_계획.md` STEP 5.

SAQ/현대차/KAP 같은 OEM 폼은 K-ESG 위에 얹는 *어댑터*다. 이 모듈은 그 아래 깔린
**캐노니컬 자가진단 출력** 자체를 만든다 — K-ESG 항목 목록(kesg_items)에서 문항을
프로그램으로 생성하므로 프레임워크에 독립적이고, 전 항목이 빠짐없이 출력에 존재한다.

도출유형(kesg_evidence_requirements) → 문항 qtype 매핑
  · quantitative → numeric        (값+증빙+D1)
  · disclosure   → yes_no_evidence (공시 존재 여부)
  · policy       → yes_no_evidence (정성 — 미해소 시 insufficient/hitl_required로 라우팅)

이렇게 만든 프레임워크는 기존 responder/derive/checklist/exporter가 그대로 처리한다
(추가 엔진 없음). 결과적으로 28(또는 61) 항목이 5개 status 중 하나로 귀결된다.
"""
from __future__ import annotations

from ...knowledge import kesg_items
from ...knowledge.kesg_evidence_requirements import derive_kind_for
from ..schema import Framework, Question

_AREA_SECTION: dict[str, str] = {
    "P": "정보공시",
    "E": "환경",
    "S": "사회",
    "G": "지배구조",
}


def _qtype_for(code: str) -> str:
    return "numeric" if derive_kind_for(code) == "quantitative" else "yes_no_evidence"


def _question_for(item: kesg_items.KESGItem) -> Question:
    return Question(
        qid=f"KESG-{item.code}",
        section=_AREA_SECTION.get(item.area, item.area),
        text=f"[{item.code}] {item.name} — {item.description}",
        qtype=_qtype_for(item.code),  # type: ignore[arg-type]
        evidence_required=True,
        kesg_codes=(item.code,),
        unit_hint=item.unit,
    )


def build_self_diagnosis(profile: kesg_items.Profile) -> Framework:
    """프로파일(sme=28 / full=61)에 맞춰 자가진단 프레임워크를 생성한다."""
    items = kesg_items.items_for_profile(profile)
    label = f"K-ESG 자가진단 — {kesg_items.PROFILE_LABELS[profile]}"
    key = "kesg28" if profile == "sme" else "kesg61"
    return Framework(
        key=key,
        label=label,
        questions=tuple(_question_for(it) for it in items),
    )


# 캐노니컬 1차 출력 — 중소기업 기본형 28 / 전체 61.
KESG28 = build_self_diagnosis("sme")
KESG61 = build_self_diagnosis("full")
