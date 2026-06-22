"""RBA 행동규범 자가진단 — 협력사 실사 기둥의 캐노니컬 substrate (STEP 5, 실사 측).

> 공시 기둥의 `kesg_self`와 **병렬**. K-ESG-self가 공시 substrate라면, 이 모듈은
> 대기업 협력사 실사 응답서의 substrate를 만든다.

현대차·삼성·SK·LG 등 OEM 폼은 이 RBA-42 위에 얹는 *어댑터*다(SAQ가 K-ESG 위에
얹힌 것과 동일 패턴). 이 모듈은 그 아래 깔린 **캐노니컬 자가진단 출력** 자체를
RBA 항목 목록(knowledge/rba_items)에서 프로그램으로 생성하므로, 42개 조항이
빠짐없이 출력에 존재한다.

엔진 재사용
----------
Question에 RBAItem의 K-ESG 크로스워크(`kesg_codes`)를 실어 보내면, 기존
responder/derive(mapping.py)가 코드 기반으로 동일 증빙·data_point를 끌어와 답을
채운다(추가 검출엔진 없음).
  · 크로스워크가 있는 32개 → K-ESG 증빙 풀에서 자동 응답(정량은 data_point 공유).
  · RBA 고유 10개(근로시간·유해물질·분쟁광물·IP 등, kesg_codes 빈 튜플)
    → primary_code 없음 → derive가 insufficient(증빙 업로드 시 해소)로 라우팅.

qtype 매핑: 정량 → numeric(값+증빙+D1), 그 외 → yes_no_evidence(존재형).
"""
from __future__ import annotations

from ...knowledge import rba_items
from ..schema import Framework, Question


def _qtype_for(item: rba_items.RBAItem) -> str:
    return "numeric" if item.data_type == "정량" else "yes_no_evidence"


def _match_codes(item: rba_items.RBAItem) -> tuple[str, ...]:
    """문항이 증빙풀에서 매칭할 코드.

    K-ESG 크로스워크 코드(있으면)를 앞에 두고(primary_code=정량 data_point 경로 유지),
    RBA 코드 자신을 항상 뒤에 덧붙인다. 그래야 K-ESG 증빙풀과 RBA clause 태깅 증빙을
    둘 다 본다. 크로스워크 없는 고유 조항은 RBA 코드 단독.
    """
    return tuple(dict.fromkeys([*item.kesg_codes, item.code]))


def questions_for(
    item: rba_items.RBAItem, *, section: str, qid_prefix: str,
) -> list[Question]:
    """한 RBA 조항을 양식 문항으로 펼친다.

    metrics가 있으면 '조항 존재형 1문항 + 지표별 수치행 N개'로 분해한다
    (예: C-4 고형폐기물 → 관리체계 존재 + 재활용률(%) + 배출량(톤)). 자가주장 D1
    검증이 각 지표 코드에 정확히 걸리도록, 수치행의 kesg_codes를 단일 지표코드로 둔다.
    metrics가 없으면 정량→numeric / 그 외→yes_no_evidence 단일 문항.
    """
    base_text = f"[{item.code}] {item.name_ko} — {item.description}"
    if item.metrics:
        out = [Question(
            qid=f"{qid_prefix}-{item.code}",
            section=section,
            text=base_text,
            qtype="yes_no_evidence",  # 조항 차원: 관리체계·방침 존재
            evidence_required=True,
            kesg_codes=_match_codes(item),
        )]
        for code, label, unit in item.metrics:
            out.append(Question(
                qid=f"{qid_prefix}-{item.code}-{code}",
                section=section,
                text=f"[{item.code}·수치] {label}",
                qtype="numeric",
                evidence_required=True,
                kesg_codes=(code,),
                unit_hint=unit,
            ))
        return out
    return [Question(
        qid=f"{qid_prefix}-{item.code}",
        section=section,
        text=base_text,
        qtype=_qtype_for(item),  # type: ignore[arg-type]
        evidence_required=True,
        kesg_codes=_match_codes(item),  # 크로스워크 있으면 K-ESG, 없으면 RBA 고유코드
        unit_hint=item.unit,
    )]


def build_rba_self() -> Framework:
    """RBA v8.0 42개 조항 자가진단 프레임워크를 생성한다(일부 조항은 수치행으로 분해)."""
    questions = [
        q for it in rba_items.RBA_ITEMS
        for q in questions_for(it, section=it.section_ko, qid_prefix="RBA")
    ]
    return Framework(
        key="rba42",
        label="RBA 행동규범 자가진단 (v8.0, 42항목)",
        questions=tuple(questions),
    )


# 캐노니컬 실사 substrate — RBA 전체 42항목.
RBA42 = build_rba_self()
