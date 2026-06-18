"""Runtime configuration loaded from .env / environment variables."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:  # dotenv is optional at runtime
    pass


ROOT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT_DIR / "data"
SAMPLE_DART_DIR = DATA_DIR / "sample_dart"
KESG_DIR = DATA_DIR / "kesg"
INDUSTRY_DIR = DATA_DIR / "industry"
BEST_REPORTS_DIR = DATA_DIR / "best_reports"


@dataclass
class Settings:
    openai_api_key: str | None
    anthropic_api_key: str | None
    dart_api_key: str | None
    openai_model: str
    anthropic_model: str
    embed_model: str
    azure_openai_endpoint: str | None = None  # AZURE_OPENAI_ENDPOINT 설정 시 AzureOpenAI 클라이언트 사용
    force_mock: bool = False   # ESGENIE_FORCE_MOCK=1 → 키가 있어도 mock 강제 (테스트 결정성)
    strict_llm: bool = False   # ESGENIE_STRICT=1 → API 실패 시 mock fallback 금지, 예외 raise (평가/운영 모드)
    active_industry: str | None = None  # ESGENIE_INDUSTRY=automotive_parts → 업종 모듈 명시 선택(추론보다 우선)

    @property
    def use_mock_llm(self) -> bool:
        if self.force_mock:
            return True
        return not (self.openai_api_key or self.anthropic_api_key)

    @property
    def use_mock_dart(self) -> bool:
        return not self.dart_api_key


def load_settings() -> Settings:
    return Settings(
        openai_api_key=os.getenv("OPENAI_API_KEY") or None,
        anthropic_api_key=os.getenv("ANTHROPIC_API_KEY") or None,
        dart_api_key=os.getenv("DART_API_KEY") or None,
        openai_model=os.getenv("OPENAI_MODEL", "gpt-4.1-mini"),
        anthropic_model=os.getenv("ANTHROPIC_MODEL", "claude-haiku-4-5-20251001"),
        embed_model=os.getenv("EMBED_MODEL", "paraphrase-multilingual-MiniLM-L12-v2"),
        azure_openai_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT") or None,
        force_mock=os.getenv("ESGENIE_FORCE_MOCK", "0") == "1",
        strict_llm=os.getenv("ESGENIE_STRICT", "0") == "1",
        active_industry=os.getenv("ESGENIE_INDUSTRY") or None,
    )


SETTINGS = load_settings()


# ---- v10: 5축 위험 탐지 임계치 & 가중치 --------------------------------------
# 환경변수로 오버라이드 가능: D1_THRESHOLD=0.10 python -m esgenie.pipeline ...

# D1: 수치 주장 vs L0 노드값 상대 오차 임계치 (비율, 0~1)
D1_THRESHOLD: float = float(os.getenv("D1_THRESHOLD", "0.15"))

# D2: 문장 내 모호어/최상급 밀도 임계치 (개수/문장, 정규화 기준)
D2_THRESHOLD: float = float(os.getenv("D2_THRESHOLD", "0.25"))

# D3: SBERT 코사인 유사도 하한 (이하이면 의미 괴리 위험)
D3_THRESHOLD: float = float(os.getenv("D3_THRESHOLD", "0.35"))

# D5: 시계열 YoY 모순 판정 비율 임계치 (0~1)
D5_THRESHOLD: float = float(os.getenv("D5_THRESHOLD", "0.20"))

# 축별 가중평균 가중치 (합계 = 1.0) — D4 업종 z-score 제거, 4축 운영
D_WEIGHTS: dict[str, float] = {
    "D1_numeric":    0.40,
    "D2_modifier":   0.25,
    "D3_semantic":   0.25,
    "D5_timeseries": 0.10,
}

# aggregate 위험 레벨 경계 (0~1 정규화 점수 기준)
RISK_LEVEL_THRESHOLDS: dict[str, float] = {
    "low":    0.25,   # score < 0.25
    "medium": 0.50,   # 0.25 <= score < 0.50
    "high":   1.00,   # score >= 0.50
}

# L4 재생성 최대 반복 횟수
MAX_REFINEMENT_ITER: int = int(os.getenv("MAX_REFINEMENT_ITER", "3"))


# ---- 하이브리드 검출 (룰 1차 + LLM 2차 판정) ---------------------------------
# 룰 점수가 이 값 이상인 축만 LLM 판정으로 보낸다 (비용 절감 — 전수 LLM 호출 방지)
JUDGE_TRIGGER: float = float(os.getenv("JUDGE_TRIGGER", "0.25"))

# 최종 점수 = JUDGE_RULE_WEIGHT * 룰점수 + (1 - JUDGE_RULE_WEIGHT) * LLM점수
JUDGE_RULE_WEIGHT: float = float(os.getenv("JUDGE_RULE_WEIGHT", "0.4"))
