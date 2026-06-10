"""테스트 결정성 보장 — 실 API 키가 .env에 있어도 테스트는 항상 mock으로 돈다."""
import os

# esgenie.config가 import 되기 전에 설정해야 한다 (pytest가 conftest를 가장 먼저 로드).
os.environ["ESGENIE_FORCE_MOCK"] = "1"
