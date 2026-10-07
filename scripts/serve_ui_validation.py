"""Deterministic browser validation adapter; reads real test PDFs without AI calls."""
from copy import deepcopy
import os
from pathlib import Path
import fitz
import uvicorn
from esgenie.web.app import create_app
from esgenie.web.presenter import timestamp


def fixture_runner(project, directory):
    answers = []
    for document in project['documents']:
        if not document.get('included', True) or document.get('error') or document['role'] != 'evidence':
            continue
        with fitz.open(directory / document['path']) as pdf:
            quote = pdf[0].get_text().strip()
        import re
        found = re.search(r'(\d+(?:\.\d+)?)\s*kWh', quote)
        value = float(found.group(1)) if found else None
        answers.append({'qid': f'q-{document["id"]}', 'question_text': document['name'], 'section': '환경',
                        'value': value, 'unit': 'kWh', 'status': 'flagged', 'confidence_flags': ['partial_value'],
                        'boundary_label': '2026년 1월 · A공장 · 사용 전력', 'scope_notes': ['월간 자료 · 연간 실적 미확인'],
                        'evidence_links': [{'file_name': document['name'], 'quote': quote, 'page': 0,
                                            'relative_path': '', 'origin': 'ocr_structured', 'independent': True}]})
    return {'sheet': {'framework_key': 'rba42', 'framework_label': '브라우저 검증용 RBA 참고양식', 'answers': answers},
            'answers': [], 'generated_at': timestamp(), 'limitations': ['자동화 검증용 분석 어댑터. 실제 AI 정확도 검증이 아닙니다.'],
            'mode': 'live'}


if __name__ == '__main__':
    app = create_app(data_root=Path(os.getenv('ESGENIE_WEB_DATA_DIR', 'outputs/browser-test-projects')), runner=fixture_runner, available=lambda: True)
    uvicorn.run(app, host='127.0.0.1', port=8771)
