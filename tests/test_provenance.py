"""provenance 뷰 헬퍼 단위 테스트."""
from __future__ import annotations

from types import SimpleNamespace

from esgenie.provenance import (
    bbox_to_pct, verification_view, provenance_chain, trust_summary, primary_evidence,
)


def _ev(bbox=None, node="N1", origin="dart", fname="f.json"):
    return SimpleNamespace(bbox=bbox, node_id=node, origin=origin, file_name=fname)


def _dp(value=3420.0, unit="tCO2eq", verif="unverified", d1=0.7, evs=None):
    return SimpleNamespace(kesg_code="E-3-1", kesg_name="온실가스", value=value, unit=unit,
                           period=2024, confidence=1.0, verification=verif, d1_risk=d1,
                           evidence_files=evs if evs is not None else [_ev()])


# ── bbox_to_pct ──────────────────────────────────────────────────────
def test_bbox_none_or_malformed():
    assert bbox_to_pct(None) is None
    assert bbox_to_pct([1, 2, 3]) is None
    assert bbox_to_pct(["a", "b", "c", "d"]) is None


def test_bbox_normalized():
    b = bbox_to_pct([0.1, 0.2, 0.5, 0.6])
    assert b == {"left": 10.0, "top": 20.0, "width": 40.0, "height": 40.0}


def test_bbox_pixel_needs_page():
    assert bbox_to_pct([100, 200, 300, 400]) is None          # page 없으면 정규화 불가
    b = bbox_to_pct([100, 200, 300, 400], page=(1000, 2000))
    assert b == {"left": 10.0, "top": 10.0, "width": 20.0, "height": 10.0}


def test_bbox_reversed_coords_normalized():
    b = bbox_to_pct([0.5, 0.6, 0.1, 0.2])
    assert b["left"] == 10.0 and b["top"] == 20.0 and b["width"] == 40.0


def test_bbox_clamped():
    # 페이지보다 살짝 큰 픽셀 박스 → 0~100%로 클램프
    b = bbox_to_pct([-10, 0, 1100, 500], page=(1000, 1000))
    assert b["left"] == 0.0 and b["width"] == 100.0


# ── verification_view ────────────────────────────────────────────────
def test_verification_tones():
    assert verification_view("verified")["tone"] == "green"
    assert verification_view("unverified")["tone"] == "amber"
    assert verification_view("mismatch")["tone"] == "red"
    assert verification_view("")["tone"] == "amber"


# ── provenance_chain ─────────────────────────────────────────────────
def test_chain_with_bbox_linked():
    dp = _dp(evs=[_ev(bbox=[0.1, 0.1, 0.4, 0.2])])
    chain = provenance_chain(dp)
    assert [s["key"] for s in chain] == ["claim", "node", "source", "location"]
    assert chain[0]["value"] == "3420.0 tCO2eq"
    assert chain[3]["linked"] is True


def test_chain_no_evidence():
    dp = _dp(evs=[])
    chain = provenance_chain(dp)
    assert chain[1]["value"] == "—"
    assert chain[3]["linked"] is False


def test_primary_evidence_prefers_bbox():
    dart = _ev(bbox=None, node="N_dart", fname="x.json")
    ocr = _ev(bbox=[0.1, 0.1, 0.3, 0.2], node="N_ocr", fname="kepco.pdf")
    # DART가 먼저 와도 bbox 있는 OCR 링크를 선택
    assert primary_evidence([dart, ocr]) is ocr
    assert primary_evidence([dart]) is dart
    assert primary_evidence([]) is None


def test_chain_prefers_bbox_link():
    dart = _ev(bbox=None, node="N_dart", fname="x.json")
    ocr = _ev(bbox=[0.1, 0.1, 0.3, 0.2], node="N_ocr", fname="kepco.pdf")
    dp = _dp(evs=[dart, ocr])
    chain = provenance_chain(dp)
    assert chain[1]["value"] == "N_ocr"      # SSOT 노드가 OCR 것
    assert chain[3]["linked"] is True


# ── trust_summary ────────────────────────────────────────────────────
def test_trust_summary():
    dps = [_dp(verif="verified", d1=0.2), _dp(verif="unverified", d1=0.8),
           _dp(verif="unverified", d1=0.5)]
    s = trust_summary(dps)
    assert s["total"] == 3 and s["verified"] == 1 and s["unverified"] == 2
    assert s["verified_ratio"] == 0.333
    assert s["avg_d1_risk"] == 0.5


def test_trust_summary_empty():
    s = trust_summary([])
    assert s["total"] == 0 and s["verified_ratio"] == 0.0 and s["avg_d1_risk"] == 0.0
