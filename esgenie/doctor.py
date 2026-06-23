"""환경 사전점검 (demo doctor) — 발표/시연 전 30초 안에 환경 상태 확인.

실행:
    python -m esgenie.doctor          # 전체 점검
    python -m esgenie.doctor --smoke  # + 파이프라인 스모크 실행 (mock)

점검 항목:
  1) 필수/선택 패키지 설치 여부 + 버전
  2) API 키 상태 (DART / OpenAI / Anthropic / Azure OCR / CLOVA legacy)
  3) 임베딩 백엔드 (SBERT 정상 vs 해시 폴백 — D3 품질 직결)
  4) 샘플 데이터·벤치마크 데이터셋 존재
  5) (--smoke) mock 파이프라인 E2E 1회 실행

존재 이유: 의존성 폴백(임베딩·LLM·OCR)은 '키 없이도 돌아가는 데모'를 보장하지만
조용히 일어나면 시연 머신마다 품질이 달라진다. 폴백을 없애는 대신 '보이게' 만든다.
"""
from __future__ import annotations

import argparse
import importlib
import sys
from typing import Any

GREEN, YELLOW, RED = "🟢", "🟡", "🔴"

# (모듈명, 표시명, 필수 여부, 없을 때 영향)
_PACKAGES: list[tuple[str, str, bool, str]] = [
    ("numpy",                 "numpy",                 True,  "전체 동작 불가"),
    ("pandas",                "pandas",                True,  "UI/엑셀 동작 불가"),
    ("streamlit",             "streamlit",             False, "CLI만 사용 가능"),
    ("plotly",                "plotly",                False, "UI 차트 미표시"),
    ("openpyxl",              "openpyxl",              False, "evidence_pack 엑셀 내보내기 불가"),
    ("sentence_transformers", "sentence-transformers", False, "D3 의미검증 → 해시 폴백 (품질 저하)"),
    ("faiss",                 "faiss-cpu",             False, "벡터 검색 → numpy 폴백 (속도 저하)"),
    ("fitz",                  "pymupdf",               False, "PDF 텍스트 추출 → mock/VLM 의존"),
    ("openai",                "openai",                False, "OpenAI 경로 비활성 (Anthropic/mock 폴백)"),
    ("anthropic",             "anthropic",             False, "Anthropic 경로 비활성 (mock 폴백)"),
    ("dotenv",                "python-dotenv",         False, ".env 자동 로드 안 됨 — 키를 환경변수로 직접 설정 필요"),
]


def check_packages() -> list[dict[str, Any]]:
    rows = []
    for module, name, required, impact in _PACKAGES:
        try:
            m = importlib.import_module(module)
            ver = getattr(m, "__version__", "?")
            rows.append({"name": name, "status": "ok", "version": ver,
                         "required": required, "note": ""})
        except Exception:
            rows.append({"name": name, "status": "missing", "version": "-",
                         "required": required, "note": impact})
    return rows


def check_keys() -> list[dict[str, Any]]:
    from .config import SETTINGS
    import os

    rows = [
        {"name": "DART_API_KEY",      "set": bool(SETTINGS.dart_api_key),
         "fallback": "샘플 DART 데이터 (삼성/현대/POSCO/SME 2사)"},
        {"name": "OPENAI_API_KEY",    "set": bool(SETTINGS.openai_api_key),
         "fallback": "Anthropic → mock LLM"},
        {"name": "ANTHROPIC_API_KEY", "set": bool(SETTINGS.anthropic_api_key),
         "fallback": "mock LLM (하이브리드 판정·벤치마크는 mock 수치)"},
        {
            "name": "AZURE_DOC_INTEL",
            "set": bool(os.getenv("AZURE_DOC_INTEL_ENDPOINT")) and bool(os.getenv("AZURE_DOC_INTEL_KEY")),
            "fallback": "pymupdf+정규식 → 정형 mock 샘플",
        },
        {
            "name": "UPSTAGE_API_KEY",
            "set": bool(os.getenv("UPSTAGE_API_KEY")),
            "fallback": "Upstage DP 경로 비활성 (Azure → CLOVA → pymupdf 폴백)",
        },
        {
            "name": "CLOVA_OCR_SECRET",
            "set": bool(os.getenv("CLOVA_OCR_SECRET")),
            "fallback": "레거시 OCR 경로 (Azure OCR 미설정 시 선택적 사용)",
        },
    ]
    if SETTINGS.force_mock:
        rows.append({"name": "ESGENIE_FORCE_MOCK", "set": True,
                     "fallback": "⚠ mock 강제 모드 — 키가 있어도 무시됨"})
    return rows


def check_embedding() -> dict[str, Any]:
    from .embeddings import backend_summary
    return backend_summary()


def check_data() -> list[dict[str, Any]]:
    from .config import DATA_DIR, SAMPLE_DART_DIR
    checks = [
        ("샘플 DART", SAMPLE_DART_DIR, list(SAMPLE_DART_DIR.glob("*.json"))),
        ("벤치마크 데이터셋", DATA_DIR / "benchmark",
         list((DATA_DIR / "benchmark").glob("*.json"))),
        ("K-ESG 가이드라인", DATA_DIR / "kesg", list((DATA_DIR / "kesg").glob("*.json"))),
    ]
    return [{"name": n, "path": str(p), "files": len(fs), "ok": len(fs) > 0}
            for n, p, fs in checks]


def run_smoke() -> dict[str, Any]:
    """mock 파이프라인 E2E 1회 — 데모 직전 최종 확인."""
    import os
    import time
    os.environ.setdefault("ESGENIE_FORCE_MOCK", "1")
    try:
        from .pipeline import run
        t0 = time.time()
        out = run("005930", ["E"], save_traces=False)
        return {
            "ok": True,
            "elapsed_sec": round(time.time() - t0, 2),
            "coverage": f"{out.extraction.coverage_pct:.1f}% ({out.extraction.profile_label})",
            "risk": {a: round(v.final_score, 1) for a, v in out.sections.items()},
        }
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def diagnose(smoke: bool = False) -> dict[str, Any]:
    """전체 진단 결과 dict (UI/테스트에서 재사용)."""
    report: dict[str, Any] = {
        "python": sys.version.split()[0],
        "packages": check_packages(),
        "keys": check_keys(),
        "embedding": check_embedding(),
        "data": check_data(),
    }
    if smoke:
        report["smoke"] = run_smoke()

    pkg_fail = [p for p in report["packages"] if p["status"] == "missing" and p["required"]]
    emb_warn = report["embedding"]["embedding_backend"] != "sbert"
    data_fail = [d for d in report["data"] if not d["ok"]]
    smoke_fail = smoke and not report.get("smoke", {}).get("ok", True)

    if pkg_fail or data_fail or smoke_fail:
        report["verdict"] = "fail"
    elif emb_warn:
        report["verdict"] = "warn"
    else:
        report["verdict"] = "ok"
    return report


# ---- CLI 출력 ----------------------------------------------------------------

def _print_report(r: dict[str, Any]) -> None:
    print("=" * 62)
    print("ESGenie 환경 사전점검 (demo doctor)")
    print("=" * 62)
    print(f"Python {r['python']}")

    print("\n[패키지]")
    for p in r["packages"]:
        if p["status"] == "ok":
            print(f"  {GREEN} {p['name']:<24} {p['version']}")
        else:
            mark = RED if p["required"] else YELLOW
            print(f"  {mark} {p['name']:<24} 미설치 — {p['note']}")

    print("\n[API 키]")
    for k in r["keys"]:
        warn = k["fallback"].startswith("⚠")
        mark = YELLOW if (warn or not k["set"]) else GREEN
        suffix = f" — {k['fallback']}" if warn else ("" if k["set"] else f" → 폴백: {k['fallback']}")
        print(f"  {mark} {k['name']:<20} {'설정됨' if k['set'] else '없음'}{suffix}")

    print("\n[임베딩 백엔드]")
    e = r["embedding"]
    mark = GREEN if e["embedding_backend"] == "sbert" else YELLOW
    print(f"  {mark} backend={e['embedding_backend']} | model={e['embed_model']} | faiss={e['faiss']}")
    print(f"     {e['quality_note']}")

    print("\n[데이터]")
    for d in r["data"]:
        print(f"  {GREEN if d['ok'] else RED} {d['name']:<16} {d['files']}개 파일")

    if "smoke" in r:
        print("\n[파이프라인 스모크 (mock)]")
        s = r["smoke"]
        if s["ok"]:
            print(f"  {GREEN} E2E 통과 — {s['elapsed_sec']}s, 커버리지 {s['coverage']}, 위험도 {s['risk']}")
        else:
            print(f"  {RED} 실패 — {s['error']}")

    verdict_msg = {
        "ok":   f"{GREEN} 데모 준비 완료",
        "warn": f"{YELLOW} 동작은 하지만 품질 경고 있음 (위 노란 항목 확인)",
        "fail": f"{RED} 데모 불가 — 빨간 항목부터 해결",
    }
    print("\n" + "=" * 62)
    print(f"종합: {verdict_msg[r['verdict']]}")
    print("=" * 62)


def _cli() -> None:
    parser = argparse.ArgumentParser(description="ESGenie 환경 사전점검")
    parser.add_argument("--smoke", action="store_true", help="mock 파이프라인 E2E 1회 포함")
    args = parser.parse_args()
    report = diagnose(smoke=args.smoke)
    _print_report(report)
    sys.exit(0 if report["verdict"] != "fail" else 1)


if __name__ == "__main__":
    _cli()
