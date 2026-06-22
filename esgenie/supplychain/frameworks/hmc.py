"""현대자동차 협력사 ESG 실사 어댑터 — RBA-42 위에 얹는 OEM 폼.

> 실사 기둥의 substrate(RBA-self) 위에 얹는 *어댑터*다(SAQ가 K-ESG 위에 얹힌 것과
> 동일 패턴). 공시 기둥의 어댑터가 SAQ라면, 실사 기둥의 첫 어댑터가 이 현대차 폼이다.

근거
----
현대자동차 협력사 행동규범(2025)은 **윤리 / 환경 / 노동·인권 / 안전보건 / 경영시스템**
5개 영역으로 구성되며, RBA Code of Conduct v8.0의 5개 섹션(A 노동 / B 안전보건 /
C 환경 / D 윤리 / E 경영시스템)과 **1:1로 대응**한다. 따라서 본 어댑터는 RBA-42
substrate를 현대차 영역 라벨로 재배치하고, RBA 항목의 K-ESG 크로스워크를 그대로 실어
기존 responder/derive 엔진이 동일 증빙으로 답을 채우게 한다(추가 검출엔진 없음).

TODO(본선 후 정밀화): 현대차가 실제 송부하는 자가진단 질문지(SAQ) 문구·번호·하위문항이
확보되면, 본 어댑터의 text/qid/subset을 그 양식에 맞춰 교체한다. 현재는 행동규범 5영역
전 항목을 RBA 기준으로 빠짐없이 노출하는 완전본이다.
"""
from __future__ import annotations

from ...knowledge import rba_items
from ..schema import Framework, Question
from .rba_self import questions_for

# 현대차 영역 노출 순서(행동규범 목차 순서: 윤리·환경·노동인권·안전보건·경영시스템).
_HMC_AREA_ORDER = ["윤리", "환경", "노동·인권", "안전보건", "경영시스템"]


def _hmc_questions() -> tuple[Question, ...]:
    # 현대차 영역 순서로 정렬해 출력(영역 내부는 RBA 코드 순서 유지).
    ordered = sorted(
        rba_items.RBA_ITEMS,
        key=lambda it: (_HMC_AREA_ORDER.index(it.hmc_area), it.code),
    )
    return tuple(
        q
        for it in ordered
        for q in questions_for(it, section=it.hmc_area, qid_prefix="HMC")
    )


HMC = Framework(
    key="hmc",
    label="현대자동차 협력사 ESG 실사 응답서 (RBA v8.0 기반)",
    questions=_hmc_questions(),
)
