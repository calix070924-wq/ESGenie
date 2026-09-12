"""Illustrative onboarding example; not a pipeline replay or a new analysis."""
from .presenter import present_sheet, timestamp


def populate_example(project: dict) -> dict:
    project.update(company_name="한울정밀", year=2026, industry="금속가공", mode="example")
    project["documents"] = [
        {"id": "example-waste", "name": "폐기물 처리 내역.pdf", "role": "evidence", "size": 0, "pages": 1, "example": True},
        {"id": "example-power", "name": "전력 사용 내역.pdf", "role": "evidence", "size": 0, "pages": 1, "example": True},
        {"id": "example-survey", "name": "공급사 자가진단 설문.pdf", "role": "company_answer", "size": 0, "pages": 1, "example": True},
    ]
    def link(name, quote):
        return {"file_name": name, "relative_path": "", "origin": "ocr_structured", "page": 0,
                "node_id": "example", "quote": quote, "independent": True, "resolved": True}
    answers = [
        {"qid": "example-recycling", "section": "환경 · 폐기물", "question_text": "폐기물 중 재활용하는 비율은 얼마인가요?",
         "value": 29.3, "unit": "%", "period": 2026, "status": "flagged", "confidence_flags": ["period_inferred", "derived"],
         "flags": ["D1 불일치: 회사 응답 92% ↔ 증빙 계산값 29.3% (62.7%p 차이)"],
         "evidence_links": [link("폐기물 처리 내역.pdf", "시연 검증 결과 요약: 폐기물 총량 18.4 ton, 계산된 재활용률 29.3%. 원본 표는 추가 확인 필요.")],
         "self_reports": [{"value": 92, "unit": "%", "raw": "재활용률 92%", "source": "saq:공급사 자가진단 설문.pdf"}],
         "evidence_needed": ["폐기물 처리 내역", "재활용 업체 확인서"]},
        {"qid": "example-energy", "section": "환경 · 에너지", "question_text": "사용한 전력량을 확인해 주세요.",
         "value": 142560, "unit": "kWh", "period": 2026, "status": "self_reported",
         "confidence_flags": ["period_inferred", "partial_value"], "flags": [],
         "evidence_links": [link("전력 사용 내역.pdf", "시연 검증 결과 요약: 월 단위 전력 사용량 142,560 kWh. 에너지 환산값 0.513216 TJ. 2026년은 추정값.")],
         "evidence_needed": ["기간이 표시된 전기요금 고지서", "연간 전력 사용량 집계표"]},
        {"qid": "example-governance", "section": "회사 운영 · 윤리", "question_text": "윤리 규정과 운영 내용을 설명해 주세요.",
         "value": None, "status": "hitl_required", "flags": [], "evidence_links": [],
         "evidence_needed": ["윤리 규정", "관련 교육이나 운영 기록"]},
    ]
    sheet = {"framework_key": "rba42", "framework_label": "사용법 예시 · 대표 질문 3개", "corp_name": project["company_name"], "answers": answers, "gaps": []}
    project["result"] = {"sheet": sheet, "answers": present_sheet(sheet, project["documents"], {"폐기물 처리 내역.pdf", "전력 사용 내역.pdf"}),
                         "generated_at": timestamp(), "limitations": ["사용법을 보여주는 대표 항목 3개입니다. 전체 실사 결과나 원본 PDF가 아닙니다."],
                         "mode": "example"}
    project["result_revision"] = project["input_revision"]
    project["job"] = {"status": "complete", "stage": "예시 준비 완료", "error": ""}
    return project
