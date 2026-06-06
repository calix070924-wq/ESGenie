"""Layer 4 — 자가 검증 루프 (Iterative Refinement).

v10 변경:
- detect_risk_vector() 기반 5축 제약 프롬프트 주입
- 최대 MAX_REFINEMENT_ITER(3)회 재생성, 모두 실패 시 status="HITL_REQUIRED"
- 각 시도의 (번호, 제약, before/after, risk_vector)를 refinement_attempts에 기록
- 하위 호환: verify_and_refine() 시그니처 동일, 신규 인자는 default=None
"""
from __future__ import annotations

import datetime
from dataclasses import dataclass, field
from typing import Any

from .config import MAX_REFINEMENT_ITER, SETTINGS
from .dart_client import CompanyReport
from .layer2_rag import GenerationResult, HybridRAG
from .layer3_detect import DetectionResult, detect, detect_risk_vector, risk_band
from .schemas import RefinementAttempt, RiskVector

DEFAULT_THRESHOLD = 30.0

# ---- 5축 제약 프롬프트 템플릿 ------------------------------------------------
_AXIS_CONSTRAINTS: dict[str, str] = {
    "D1_numeric": (
        "수치 정확성: 다음 DART 실측 수치만 사용하고 임의로 변경하지 말 것. "
        "클레임 수치와 DART 수치가 다른 경우 DART 수치를 우선 사용하라."
    ),
    "D2_modifier": (
        "표현 절제: '최고 수준', '혁신적', '압도적', '선도적' 등 정량 근거 없는 "
        "최상급·모호 수식어를 사용하지 말 것. 정량 수치로 대체하라."
    ),
    "D3_semantic": (
        "근거 충실성: 생성 문장은 검색된 DART 원문 및 K-ESG 가이드라인의 "
        "의미 범위 내에서 서술하라. 원문에 없는 내용을 추가하지 말 것."
    ),
    "D4_industry": (
        "업종 적합성: 동종 업계 평균 대비 ±1σ 범위 내에서 표현하라. "
        "업종 평균을 현저히 벗어난 수치나 주장은 명시적 근거와 함께 제시하라."
    ),
    "D5_timeseries": (
        "시계열 일관성: 전년 대비 추세(증가/감소)가 실제 DART 데이터와 일치해야 한다. "
        "방향이 다른 주장은 즉시 수정하라."
    ),
}


# ---- 데이터클래스 -----------------------------------------------------------

@dataclass
class VerificationStep:
    iteration: int
    generation: GenerationResult
    detection: DetectionResult
    instruction: str


@dataclass
class VerificationResult:
    area: str
    steps: list[VerificationStep]
    final: VerificationStep
    iterations_used: int = 0
    converged: bool = False
    hitl_required: bool = False         # v10 신설
    refinement_attempts: list[RefinementAttempt] = field(default_factory=list)  # v10 신설
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def final_text(self) -> str:
        return self.final.generation.text

    @property
    def final_score(self) -> float:
        return self.final.detection.risk_score

    @property
    def final_band(self) -> str:
        return risk_band(self.final_score)


# ---- 내부 헬퍼 --------------------------------------------------------------

def _feedback_instruction(det: DetectionResult) -> str:
    """기존 단일 점수 기반 피드백 지시문 (legacy 경로 유지)."""
    parts: list[str] = []
    mism = [h for h in det.highlights if h["type"] == "mismatch"]
    if mism:
        parts.append("다음 수치 과장을 바로잡고 DART 원본 수치를 그대로 사용할 것:")
        for h in mism[:5]:
            parts.append(f"- \"{h['claim']}\" → DART 값 {h['dart_value']} ({h['delta_pct']:+.1f}% 편차)")
    vague = [h for h in det.highlights if h["type"] == "vague"]
    if vague:
        phrases = sorted({p for h in vague for p in h["phrases"]})
        parts.append(f"과장 수식어({', '.join(phrases[:6])}) 제거. 정량 근거가 있는 표현으로 대체.")
    if det.semantic_similarity < 0.3:
        parts.append("생성 문장과 DART 원문의 의미 유사도가 낮다. DART 사실관계에 더 밀착해 서술.")
    if not parts:
        parts.append("수치 근거만 사용하고 불필요한 수식어를 피할 것.")
    return "\n".join(parts)


def _axis_constraint_instruction(
    risk_vector: RiskVector | None,
    detection: DetectionResult,
) -> tuple[str, list[str]]:
    """5축 분해 결과에서 high 축의 제약 프롬프트를 조합해 반환.

    Returns:
        (조합된 지시문 문자열, 적용된 축 이름 목록)
    """
    # risk_vector가 없으면 기존 피드백 지시문으로 폴백
    if risk_vector is None:
        instruction = _feedback_instruction(detection)
        return instruction, ["legacy"]

    high_axes = risk_vector.high_axes()
    if not high_axes:
        # 모든 축이 low여도 기존 방식 피드백 유지
        instruction = _feedback_instruction(detection)
        return instruction, []

    parts: list[str] = ["=== 재작성 제약 (위험 축 기준) ==="]
    for axis in high_axes:
        parts.append(f"[{axis}] {_AXIS_CONSTRAINTS.get(axis, '')}")
    parts.append("=== 위 모든 제약을 동시에 준수해 재작성하라. ===")
    return "\n".join(parts), high_axes


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _make_refinement_attempt(
    attempt_no: int,
    before_text: str,
    after_text: str,
    constraints: list[str],
    risk_vector: RiskVector | None,
) -> RefinementAttempt:
    return RefinementAttempt(
        attempt_no=attempt_no,
        constraints_applied=constraints,
        before_text=before_text,
        after_text=after_text,
        risk_vector=risk_vector,
        timestamp=_now_iso(),
    )


# ---- 공개 API ---------------------------------------------------------------

def verify_and_refine(
    report: CompanyReport,
    area: str,
    rag: HybridRAG,
    *,
    threshold: float = DEFAULT_THRESHOLD,
    max_iter: int = MAX_REFINEMENT_ITER,
    demo_greenwash: bool = False,
    evidence_graph: Any | None = None,   # v10: EvidenceGraph | None
    industry_stats: dict[str, Any] | None = None,  # v10: 업종 벤치마크
) -> VerificationResult:
    """반복 검증 루프.

    v10 추가 동작:
    - evidence_graph / industry_stats 가 제공되면 detect_risk_vector() 로
      5축 제약을 도출하고 해당 축에 맞는 프롬프트를 주입한다.
    - max_iter 초과 시 hitl_required=True 로 마킹.
    - 각 시도가 RefinementAttempt 로 기록된다.
    """
    steps: list[VerificationStep] = []
    refinement_attempts: list[RefinementAttempt] = []

    # --- 초안 생성 (iteration 0) ---
    gen = rag.generate_section(report, area, demo_greenwash=demo_greenwash)
    det = detect(gen.text, report)

    # 5축 벡터 계산 (evidence_graph 있을 때만)
    rv: RiskVector | None = None
    if evidence_graph is not None:
        rv = _compute_text_risk_vector(gen.text, evidence_graph, gen, industry_stats)
        det.risk_vector = rv

    steps.append(VerificationStep(iteration=0, generation=gen, detection=det, instruction=""))

    converged = det.risk_score <= threshold
    i = 0

    while not converged and i < max_iter:
        i += 1
        before_text = gen.text

        # 5축 제약 지시문 조합
        instruction, applied_axes = _axis_constraint_instruction(rv, det)

        # 재생성
        gen = rag.generate_section(report, area, extra_instruction=instruction)
        det = detect(gen.text, report)

        if evidence_graph is not None:
            rv = _compute_text_risk_vector(gen.text, evidence_graph, gen, industry_stats)
            det.risk_vector = rv

        refinement_attempts.append(_make_refinement_attempt(
            attempt_no=i,
            before_text=before_text,
            after_text=gen.text,
            constraints=applied_axes,
            risk_vector=rv,
        ))
        steps.append(VerificationStep(iteration=i, generation=gen, detection=det, instruction=instruction))

        if det.risk_score <= threshold:
            converged = True
            break

    hitl_required = not converged and i >= max_iter

    return VerificationResult(
        area=area,
        steps=steps,
        final=steps[-1],
        iterations_used=i,
        converged=converged,
        hitl_required=hitl_required,
        refinement_attempts=refinement_attempts,
        metadata={
            "threshold": threshold,
            "max_iter":  max_iter,
            "hitl_status": "HITL_REQUIRED" if hitl_required else "ok",
        },
    )


def _compute_text_risk_vector(
    text: str,
    evidence_graph: Any,
    gen: GenerationResult,
    industry_stats: dict[str, Any] | None,
) -> RiskVector:
    """전체 텍스트를 문장 단위로 분해해 가장 높은 RiskVector를 반환.

    간소화: 각 문장의 벡터를 계산하고 aggregate risk_score 최댓값의 벡터를 대표로 사용.
    """
    import re
    sents = [s.strip() for s in re.split(r"(?<=[.!?。\n])\s+", text.strip()) if s.strip()]

    # RAG 청크를 retrieved_chunks 형식으로 변환
    chunks = [
        {"id": f"kesg_{i}", "text": doc.text}
        for i, (doc, _) in enumerate(gen.context.kesg_hits + gen.context.corp_hits)
    ]

    # D3 VectorIndex를 한 번만 빌드하고 모든 문장에서 재사용
    d3_index = None
    if chunks:
        from esgenie.embeddings import IndexedDoc, VectorIndex
        d3_index = VectorIndex()
        d3_index.build([
            IndexedDoc(text=c["text"], meta={"id": c["id"]}) for c in chunks
        ])

    best_rv: RiskVector | None = None
    for sent in sents:
        rv = detect_risk_vector(
            sent,
            evidence_graph=evidence_graph,
            retrieved_chunks=chunks or None,
            industry_stats=industry_stats,
            _d3_index=d3_index,
        )
        if best_rv is None or rv.risk_score > best_rv.risk_score:
            best_rv = rv

    if best_rv is None:
        # 빈 텍스트 폴백
        from .schemas import AxisScore
        zero = AxisScore(score=0.0)
        best_rv = RiskVector(
            D1_numeric=zero, D2_modifier=zero, D3_semantic=zero,
            D4_industry=zero, D5_timeseries=zero,
            aggregate={"risk_score": 0.0, "level": "low", "top_axis": ""},
        )
    return best_rv
