"""Upstage DP 출력 시뮬레이션 → S 추출 호환성 검증.

실제 API 없이 삼성전자 직원현황 데이터를
Upstage 응답 형식(HTML 표)으로 래핑해서 파이프라인 통과 여부 확인.

실행: python -m scripts.validate_upstage_compat
  또는 python scripts/validate_upstage_compat.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from esgenie.layer0_evidence_graph import (
    EvidenceGraph,
    _extract_social_from_html_table,
)
from esgenie.ssot.ocr_router import _call_upstage_dp, _get_upstage_key


def test_upstage_dp_requires_key() -> bool:
    """_call_upstage_dp() 함수 존재 및 UPSTAGE_API_KEY 없을 때 RuntimeError 발생."""
    import os
    original = os.environ.pop("UPSTAGE_API_KEY", None)
    try:
        _call_upstage_dp("/tmp/nonexistent.pdf")
        print("  FAIL: RuntimeError가 발생하지 않음")
        return False
    except RuntimeError as e:
        if "UPSTAGE_API_KEY" in str(e):
            print(f"  PASS: RuntimeError 정상 발생 — {e}")
            return True
        print(f"  FAIL: 예상과 다른 RuntimeError — {e}")
        return False
    except Exception as e:
        print(f"  FAIL: 예상과 다른 예외 — {type(e).__name__}: {e}")
        return False
    finally:
        if original:
            os.environ["UPSTAGE_API_KEY"] = original


def test_html_table_extraction() -> bool:
    """_extract_social_from_html_table()이 HTML을 받아 S-2-2, S-3-1 노드를 반환."""
    html = """<table>
<tr><td>합 계</td><td>128,846</td><td>385</td><td>634</td><td>129,480</td><td>13.0</td></tr>
<tr><td>성별합계 남</td><td>94,416</td><td>-</td><td>497</td><td>-</td><td>94,913</td></tr>
<tr><td>성별합계 여</td><td>34,430</td><td>-</td><td>385</td><td>137</td><td>34,567</td></tr>
</table>"""

    graph = EvidenceGraph(corp_code="005930", corp_name="삼성전자")
    seen: set[str] = set()
    nodes = _extract_social_from_html_table(
        html=html,
        corp_code="005930",
        report_year=2024,
        graph=graph,
        seen_metrics=seen,
    )

    ok = True
    s22 = [n for n in nodes if n.metric == "S-2-2"]
    s31 = [n for n in nodes if n.metric == "S-3-1"]

    if not s22:
        print("  FAIL: S-2-2 노드 미생성")
        ok = False
    else:
        expected = round(128846 / 129480 * 100, 1)  # 99.5%
        actual = s22[0].value
        if abs(actual - expected) < 0.2:
            print(f"  PASS: S-2-2 = {actual}% (expected ~{expected}%)")
        else:
            print(f"  FAIL: S-2-2 = {actual}% (expected ~{expected}%)")
            ok = False

    if not s31:
        print("  FAIL: S-3-1 노드 미생성")
        ok = False
    else:
        expected = round(34567 / (94913 + 34567) * 100, 1)  # 26.7%
        actual = s31[0].value
        if abs(actual - expected) < 0.2:
            print(f"  PASS: S-3-1 = {actual}% (expected ~{expected}%)")
        else:
            print(f"  FAIL: S-3-1 = {actual}% (expected ~{expected}%)")
            ok = False

    return ok


def test_existing_azure_patterns() -> bool:
    """기존 Azure 기반 텍스트 패턴(_S_DART_EMP_TOTAL_PATTERN 등) 정상 동작 유지."""
    from esgenie.layer0_evidence_graph import (
        _S_DART_EMP_TOTAL_PATTERN,
        _S_DART_GENDER_PATTERN,
    )

    azure_text = "합 계 128,846 - 634 - 129,480 13.0"
    m = _S_DART_EMP_TOTAL_PATTERN.search(azure_text)
    if not m:
        print("  FAIL: _S_DART_EMP_TOTAL_PATTERN 매칭 실패")
        return False
    print(f"  PASS: _S_DART_EMP_TOTAL_PATTERN 매칭 — regular={m.group('regular')}, total={m.group('total')}")

    gender_text = "성별합계 남 94,416 - 497 - 94,913"
    gm = _S_DART_GENDER_PATTERN.search(gender_text)
    if not gm:
        print("  FAIL: _S_DART_GENDER_PATTERN 매칭 실패")
        return False
    print(f"  PASS: _S_DART_GENDER_PATTERN 매칭 — gender={gm.group('gender')}, total={gm.group('total')}")

    return True


def test_existing_tests_import() -> bool:
    """기존 테스트에서 사용하는 모든 import가 정상 동작하는지 확인."""
    try:
        from esgenie.layer0_evidence_graph import (
            EvidenceGraph,
            EvidenceNode,
            _S_HEADCOUNT_PATTERN,
            _S_RATIO_PATTERN,
            _S_MONEY_PATTERN,
            _S_COUNT_PATTERN,
            _S_LABEL_TO_KESG,
            _METRIC_KEYWORDS,
            _extract_social_nodes,
            build_evidence_graph,
        )
        print("  PASS: 모든 기존 import 정상")
        return True
    except ImportError as e:
        print(f"  FAIL: import 오류 — {e}")
        return False


def main() -> None:
    print("=" * 60)
    print("Upstage DP 호환성 검증")
    print("=" * 60)

    results: list[tuple[str, bool]] = []

    print("\n[1] _call_upstage_dp() API 키 미설정 시 RuntimeError")
    results.append(("upstage_dp_requires_key", test_upstage_dp_requires_key()))

    print("\n[2] _extract_social_from_html_table() → S-2-2, S-3-1 추출")
    results.append(("html_table_extraction", test_html_table_extraction()))

    print("\n[3] 기존 Azure 텍스트 패턴 정상 동작")
    results.append(("existing_azure_patterns", test_existing_azure_patterns()))

    print("\n[4] 기존 모듈 import 호환성")
    results.append(("existing_imports", test_existing_tests_import()))

    print("\n" + "=" * 60)
    passed = sum(1 for _, ok in results if ok)
    total = len(results)
    print(f"결과: {passed}/{total} 통과")
    if passed == total:
        print("ALL PASS")
    else:
        failed = [name for name, ok in results if not ok]
        print(f"FAILED: {failed}")
        sys.exit(1)


if __name__ == "__main__":
    main()
