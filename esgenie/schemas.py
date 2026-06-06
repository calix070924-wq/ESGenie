"""v10 공유 데이터 스키마.

기존 레이어의 dataclass(ExtractionResult / GenerationResult / DetectionResult 등)는
하위 호환을 위해 각 모듈에 유지한다.
여기서는 v10 신설/확장 스키마만 정의한다.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any


# ---- 5축 위험 축 -------------------------------------------------------------

@dataclass
class AxisScore:
    """단일 위험 축 점수 (0.0 ~ 1.0, 높을수록 위험)."""
    score: float
    evidence: list[str] = field(default_factory=list)  # node_id 또는 chunk_id 목록
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RiskVector:
    """4축 위험 분해 결과 (D1·D2·D3·D5).

    각 축은 AxisScore(score, evidence, detail)를 갖는다.
    aggregate는 가중평균 종합 위험도와 레벨을 담는다.
    """
    D1_numeric: AxisScore      # 수치 주장 vs L0 노드값 오차
    D2_modifier: AxisScore     # 모호어/최상급 밀도
    D3_semantic: AxisScore     # SBERT 코사인 유사도 역수
    D5_timeseries: AxisScore   # 시계열 엣지 YoY·CAGR 모순

    aggregate: dict[str, Any] = field(default_factory=dict)
    # aggregate 예시:
    # { "risk_score": 0.42, "level": "medium", "top_axis": "D1_numeric" }

    def to_dict(self) -> dict[str, Any]:
        return {
            "D1_numeric":    self.D1_numeric.to_dict(),
            "D2_modifier":   self.D2_modifier.to_dict(),
            "D3_semantic":   self.D3_semantic.to_dict(),
            "D5_timeseries": self.D5_timeseries.to_dict(),
            "aggregate":     self.aggregate,
        }

    @property
    def risk_score(self) -> float:
        return float(self.aggregate.get("risk_score", 0.0))

    @property
    def level(self) -> str:
        return str(self.aggregate.get("level", "unknown"))

    @property
    def top_axis(self) -> str:
        return str(self.aggregate.get("top_axis", ""))

    def high_axes(self) -> list[str]:
        """level이 high인 축 이름 목록."""
        axes = {
            "D1_numeric":    self.D1_numeric,
            "D2_modifier":   self.D2_modifier,
            "D3_semantic":   self.D3_semantic,
            "D5_timeseries": self.D5_timeseries,
        }
        from .config import RISK_LEVEL_THRESHOLDS
        threshold = RISK_LEVEL_THRESHOLDS["medium"]
        return [name for name, ax in axes.items() if ax.score >= threshold]


# ---- L4 재생성 시도 기록 -----------------------------------------------------

@dataclass
class RefinementAttempt:
    """L4 재생성 1회 시도 기록."""
    attempt_no: int
    constraints_applied: list[str]  # 적용된 축별 제약 프롬프트 목록
    before_text: str
    after_text: str
    risk_vector: RiskVector | None
    timestamp: str                  # ISO 8601

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "attempt_no":           self.attempt_no,
            "constraints_applied":  self.constraints_applied,
            "before_text":          self.before_text,
            "after_text":           self.after_text,
            "risk_vector":          self.risk_vector.to_dict() if self.risk_vector else None,
            "timestamp":            self.timestamp,
        }
        return d


# ---- L5 감사 추적 ------------------------------------------------------------

@dataclass
class AuditSentence:
    """문장 단위 감사 레코드.

    최종 보고서의 한 문장에 대해 L0~L4의 증거·위험·재생성 이력을 묶는다.
    """
    sentence_id: str              # "{ticker}_{area}_{idx:03d}"
    sentence_text: str
    kesg_item_id: str | None      # 연결된 K-ESG 코드 (None 이면 미매핑)
    evidence_node_ids: list[str]  # L0 노드 ID
    retrieved_chunk_ids: list[str]  # L2 RAG 청크 ID
    risk_vector: RiskVector | None
    refinement_attempts: list[RefinementAttempt]
    hitl_status: str              # "ok" | "HITL_REQUIRED"
    timestamps: dict[str, str]    # {"created": ..., "finalized": ...}
    model_versions: dict[str, str]  # {"llm": ..., "embed": ...}

    def to_dict(self) -> dict[str, Any]:
        return {
            "sentence_id":          self.sentence_id,
            "sentence_text":        self.sentence_text,
            "kesg_item_id":         self.kesg_item_id,
            "evidence_node_ids":    self.evidence_node_ids,
            "retrieved_chunk_ids":  self.retrieved_chunk_ids,
            "risk_vector":          self.risk_vector.to_dict() if self.risk_vector else None,
            "refinement_attempts":  [r.to_dict() for r in self.refinement_attempts],
            "hitl_status":          self.hitl_status,
            "timestamps":           self.timestamps,
            "model_versions":       self.model_versions,
        }


@dataclass
class AuditTrace:
    """전체 보고서 영역(E/S/G)의 감사 추적 문서."""
    ticker: str
    corp_name: str
    area: str                    # "E" | "S" | "G"
    generated_at: str            # ISO 8601
    sentences: list[AuditSentence]
    summary: dict[str, Any]
    # summary 예시:
    # { "total_sentences": 12, "hitl_count": 1,
    #   "avg_risk_score": 0.23, "high_risk_axes": ["D2_modifier"] }

    def to_dict(self) -> dict[str, Any]:
        return {
            "ticker":        self.ticker,
            "corp_name":     self.corp_name,
            "area":          self.area,
            "generated_at":  self.generated_at,
            "sentences":     [s.to_dict() for s in self.sentences],
            "summary":       self.summary,
        }
