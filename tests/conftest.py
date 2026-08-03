"""테스트 결정성 보장 — 실 API 키가 .env에 있어도 테스트는 항상 mock으로 돈다."""
import os
import tempfile

# esgenie.config가 import 되기 전에 설정해야 한다 (pytest가 conftest를 가장 먼저 로드).
os.environ["ESGENIE_FORCE_MOCK"] = "1"

# OCR LLM 캐시를 tmp로 격리한다(2026-07-27). FORCE_MOCK에서는 ocr_cache가 읽기·쓰기를
# 모두 건너뛰지만, 캐시 디렉터리를 명시 오버라이드하지 않으면 캐시 경로를 직접 만지는
# 테스트가 실제 data/_cache/ocr/를 오염시킬 수 있다 — 이중 안전장치다.
os.environ.setdefault(
    "ESGENIE_OCR_CACHE_DIR",
    os.path.join(tempfile.gettempdir(), "esgenie_test_ocr_cache"),
)

# 라이브 LLM 응답 캐시도 같은 이유로 tmp에 격리한다. 현재 FORCE_MOCK을 내리는 테스트는
# 각자 tmp_path를 지정하지만, 모든 후속 테스트 작성자가 그 규칙을 지킨다고 가정하지 않는다.
os.environ.setdefault(
    "ESGENIE_LLM_CACHE_DIR",
    os.path.join(tempfile.gettempdir(), "esgenie_test_llm_cache"),
)
