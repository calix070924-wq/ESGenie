"""Keep multi-part RBA conclusions pending human review.

These questions currently have evidence-presence checks (or, for E-6, a
training-presence check), not a complete assessment of their obligations.
Keep the linked facts, but do not present presence as a verified whole answer.
This is an explicit automation limit, not a claim that evidence is missing.
Human answers and completion remain in the existing review workflow.
"""
from .schema import Answer, Question


REVIEW_TOPICS = {
    "RBA-C-2": "오염·폐기물 발생 저감과 적용 자원의 절약 범위",
    "RBA-C-4": "폐기물 식별·관리·감축·책임있는 처리와 데이터 추적",
    "RBA-D-2": "제공·수수 및 직간접·제3자 범위의 반부패 절차",
    "RBA-D-6": "임직원·공급사 제보자의 비밀·익명·보복 방지 범위",
    "RBA-D-8": "개인정보 보호 대상의 적용 범위와 관련 법규 준수",
    "RBA-E-6": "관리자·근로자 교육의 대상·내용과 문항 요건의 연결",
}


def require_qualitative_review(answer: Answer, question: Question) -> Answer:
    """Limit automatic positive conclusions; preserve negatives and source facts."""
    topic = REVIEW_TOPICS.get(question.qid)
    if (not topic or question.qtype not in ("yes_no", "yes_no_evidence")
            or answer.value is not True or answer.status == "not_applicable"):
        return answer
    reason = ("관련 자료·회사 응답의 연결만으로 문항 전체 충족을 자동 확정하지 않습니다. "
              f"담당자 확인 필요: {topic}.")
    answer.value = None
    answer.status = "flagged"
    # The previous rationale may say '응답: 예' or '문항 요건 확인'. The actual
    # quotations, self-reports, scope and comparison diagnostics stay intact.
    answer.rationale = reason
    if reason not in answer.scope_notes:
        answer.scope_notes.append(reason)
    return answer
