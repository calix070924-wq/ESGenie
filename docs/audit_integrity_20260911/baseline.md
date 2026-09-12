# 검증 신뢰성 개선 기준 상태

- 브랜치: `codex/fix-audit-integrity-20260911`
- worktree: `/private/tmp/ESGenie-audit-integrity-20260911` (원본 보존 및 쓰기 허용 경로)
- 최신 origin/main: `d3a06e22f41c5c2b0fb4fa4ad61cbe7d089cc2e6` (fetch 완료, 원본 HEAD와 같음)
- 원본의 수정·미추적 파일 467개 해시를 별도 임시 파일에 보존. `.env`는 복사·출력·Git 추가하지 않음.
- 기반 이식: `.env.example`, `esgenie/openai_client.py`, `esgenie/llm.py`, `scripts/probe_judge_models.py`, `tests/test_openai_endpoint.py`. Azure `/openai/v1/chat/completions` 호환성에 필요.
- `scripts/run_demo_hanwool.py`: D1/D6 표기 정정 및 기존 수치 변경 이력만 이식. 01~07 최소 입력과 05~07 SAQ 분리는 main에도 이미 포함.
- 메모·UI 편집·영상 시나리오 등 원본의 무관한 작업은 이식하지 않음.
- 기존 가상환경을 읽기 전용으로 재사용하고, 구현 import는 새 worktree에 고정.
- 새 재현 62개를 `tests/audit_integrity/`, 이전 30개를 `tests/audit_20260909/`에 포함. 기대값 변경 없이 경로만 적응.
- 수정 전: 92개 중 41 통과, 51 실패. 원 보고서와 동일(62개: 29/33, 이전 30개: 12/18). 외부 소켓 접속 차단.
- 실패 상세: `baseline.log`, `baseline.xml`. 기존 실행 원본은 worktree `outputs/audit_20260911_ry_stjqv/`에 별도 보관.
- API 문서: https://developers.openai.com/api/docs/models/gpt-4.1-mini (Chat Completions, Structured outputs 지원 확인). 모델/배포명 유지.

## Python 환경

```text
3.13.13 (v3.13.13:01104ce1beb, Apr  7 2026, 14:43:30) [Clang 16.0.0 (clang-1600.0.26.6)]
pytest=9.1.1; openai=2.43.0; httpx=0.28.1; pymupdf=1.27.2.3; reportlab=5.0.0; openpyxl=3.1.5
```
