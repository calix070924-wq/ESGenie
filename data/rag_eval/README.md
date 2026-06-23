# RAG Eval Set

이 디렉터리의 평가지표는 로컬에 저장된 1차 원천자료만 사용한다.

출처
- `data/sample_dart/*.json`: DART 기반 정형 수치 스냅샷
- `data/test_docs/*.pdf`: OCR/SSOT 확장용 원천 증빙 PDF

라벨링 원칙
- retrieval qrels: `sample_dart`의 실제 K-ESG 코드 → `corp_{corp_code}_{code}` 청크로 직접 매핑
- grounding labels: 동일 원천 문구/숫자에서 faithful 문장을 만들고,
  negative는 숫자 1개 변경 또는 citation 제거로만 통제 생성
- 블로그, 뉴스, 2차 요약본, LLM 자유 생성 문장은 gold source로 사용하지 않음
