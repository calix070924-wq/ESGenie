"""감사추적(provenance) 뷰 헬퍼 — UI에서 분리한 순수 로직.

audit_trace_v15 DataPoint 하나를 '주장 → SSOT 노드 → 원본 파일 → 문서 내 위치'
체인으로 가공하고, bbox를 오버레이용 백분율 박스로 정규화한다. Streamlit 비의존 →
단위 테스트 가능.
"""
from __future__ import annotations

from typing import Any, Optional


# ── 검증 상태 표시 ────────────────────────────────────────────────────
def verification_view(verification: str) -> dict[str, str]:
    """검증 상태 → 배지 라벨/톤(red|green|amber)."""
    v = (verification or "").lower()
    if v == "verified":
        return {"label": "검증됨 · 원본 대조 완료", "tone": "green"}
    if v in ("mismatch", "conflict"):
        return {"label": "불일치 · 증빙과 충돌", "tone": "red"}
    return {"label": "미검증 · 원본 대조 필요", "tone": "amber"}


# ── bbox → 오버레이용 백분율 박스 ─────────────────────────────────────
def bbox_to_pct(bbox: Optional[list[float]],
                page: Optional[tuple[float, float]] = None) -> Optional[dict[str, float]]:
    """[x0,y0,x1,y1] → {left,top,width,height} (0~100%).

    - 값이 모두 0~1이면 이미 정규화된 것으로 간주.
    - 아니면 page=(w,h)로 나눠 정규화. page 없으면 정규화 불가 → None.
    반환은 0~100으로 클램프. 잘못된 입력은 None.
    """
    if not bbox or len(bbox) != 4:
        return None
    try:
        x0, y0, x1, y1 = (float(v) for v in bbox)
    except (TypeError, ValueError):
        return None
    lo, hi = min(x0, x1), max(x0, x1)
    to, bo = min(y0, y1), max(y0, y1)
    normalized = all(0.0 <= v <= 1.0 for v in (x0, y0, x1, y1))
    if not normalized:
        if not page or page[0] <= 0 or page[1] <= 0:
            return None
        lo, hi = lo / page[0], hi / page[0]
        to, bo = to / page[1], bo / page[1]

    def clamp(v: float) -> float:
        return max(0.0, min(1.0, v))

    lo, hi, to, bo = clamp(lo), clamp(hi), clamp(to), clamp(bo)
    return {
        "left": round(lo * 100, 2),
        "top": round(to * 100, 2),
        "width": round(max(hi - lo, 0.0) * 100, 2),
        "height": round(max(bo - to, 0.0) * 100, 2),
    }


# ── 증빙 선택 ─────────────────────────────────────────────────────────
def primary_evidence(evs: list[Any]) -> Optional[Any]:
    """증빙 링크 중 위치추적용으로 표시할 1건 선택.

    bbox(문서 내 위치)가 있는 링크를 우선 — DART(공시, 좌표 없음)가 primary여도
    OCR 증빙의 위치를 노출하기 위함. 없으면 첫 링크.
    """
    if not evs:
        return None
    for e in evs:
        if getattr(e, "bbox", None):
            return e
    return evs[0]


# ── DataPoint → 추적 체인 ─────────────────────────────────────────────
def provenance_chain(dp: Any) -> list[dict[str, Any]]:
    """DataPoint → 4단계 체인 스텝 리스트(주장/노드/원본/위치)."""
    evs = list(getattr(dp, "evidence_files", []) or [])
    ev = primary_evidence(evs)
    val = f"{getattr(dp, 'value', '')} {getattr(dp, 'unit', '')}".strip()
    node_id = getattr(ev, "node_id", "") if ev else ""
    origin = getattr(ev, "origin", "") if ev else ""
    fname = getattr(ev, "file_name", "") if ev else ""
    bbox = getattr(ev, "bbox", None) if ev else None
    has_loc = bbox_to_pct(bbox) is not None or bbox is not None
    return [
        {"key": "claim", "label": "주장", "value": val or "—", "icon": "quote"},
        {"key": "node", "label": "SSOT 노드", "value": node_id or "—", "icon": "binary-tree"},
        {"key": "source", "label": "원본 파일",
         "value": fname or origin or "—", "icon": "file-text"},
        {"key": "location", "label": "문서 내 위치",
         "value": "bbox 연결됨" if has_loc else "좌표 미연결",
         "icon": "map-pin", "linked": has_loc},
    ]


def trust_summary(data_points: list[Any]) -> dict[str, Any]:
    """데이터포인트 집합 → 검증 비율·평균 D1·미검증 수."""
    n = len(data_points)
    verified = sum(1 for d in data_points
                   if (getattr(d, "verification", "") or "").lower() == "verified")
    avg_d1 = (sum(float(getattr(d, "d1_risk", 0.0) or 0.0) for d in data_points) / n) if n else 0.0
    return {
        "total": n,
        "verified": verified,
        "unverified": n - verified,
        "verified_ratio": round(verified / n, 3) if n else 0.0,
        "avg_d1_risk": round(avg_d1, 3),
    }
