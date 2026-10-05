"""§6.4 실보고서 표본 쪽의 0 판정을 제품 추출 경로로 실행한다.

사용: python run_real_zero_sample.py <code_path> <cache_dir> <selection.json> <out.json> [--offline]
- 실보고서는 `extract_unstructured`와 같은 pymupdf 쪽 텍스트를 쓰되 선택 쪽만 넣는다(`_extract_unstructured_text`).
- --offline: 소켓을 막고 LLM 호출을 금지한다(캐시 적중만). 미스는 재생 실패로 기록된다.
- 모델이 0으로 보고한 값마다 제품 판정(보존·거부·보류)과, 명시적 연간 요청(`{연도}년 연간`)으로 다시 판정한 결과를 남긴다.
"""
from __future__ import annotations

import json
import os
import socket
import sys
import time
from pathlib import Path

CODE, CACHE, SELECTION, OUT = (Path(a).resolve() for a in sys.argv[1:5])
OFFLINE = "--offline" in sys.argv
ROOT = Path("/Users/heojeongmin/Documents/Claude/Projects/ESGenie")
sys.path.insert(0, str(CODE))
from dotenv import load_dotenv  # noqa: E402
load_dotenv(ROOT / ".env")
os.environ.update(ESGENIE_FORCE_MOCK="0", ESGENIE_STRICT="1", ESGENIE_OCR_CACHE="1",
                  ESGENIE_OCR_CACHE_DIR=str(CACHE), ESGENIE_OCR_CACHE_REFRESH="0", HF_HUB_OFFLINE="1")
misses = []
if OFFLINE:
    def blocked(*a, **k):
        raise RuntimeError("offline: network forbidden")
    socket.socket.connect = blocked
    socket.create_connection = blocked
    from openai.resources.chat import completions

    def miss(self, *a, **k):
        misses.append(1)
        raise RuntimeError("offline: cache miss — live call forbidden")
    completions.Completions.create = miss

import esgenie  # noqa: E402
assert CODE in Path(esgenie.__file__).resolve().parents, esgenie.__file__
from esgenie import llm_cache  # noqa: E402
from esgenie.config import SETTINGS  # noqa: E402
from esgenie.ssot import ocr_router as R  # noqa: E402

SETTINGS.strict_llm = not OFFLINE
selection = json.loads(SELECTION.read_text(encoding="utf-8"))
llm_cache.reset_stats()
started = time.monotonic()
records = []
for report in selection["reports"]:
    pdf = ROOT / "data/real_reports" / report["pdf"]
    pages = R._extract_pages_pymupdf(str(pdf), max_pages=R._UNSTRUCTURED_MAX_PAGES)
    chosen = report["selected"]["pages"]
    page_texts = [(p, pages[p]) for p in chosen]
    ext = R._extract_unstructured_text(str(pdf), doc_type="ambiguous_fallback_vlm",
                                       raw_text="\n".join(t for _, t in page_texts), raw_text_source="pymupdf",
                                       upstage_error=None, page_texts=page_texts)
    rm = ext.router_meta
    for m in ext.metrics:
        if m.value != 0:
            continue
        b = m.boundary or {}
        zero = next((p for p in b.get("provenance", []) if isinstance(p, dict) and p.get("source") == "zero_evidence"), {})
        recovery = next((p for p in b.get("provenance", []) if isinstance(p, dict) and p.get("source") == "value_recovery"), None)
        year = next((int(t) for t in __import__("re").findall(r"(20\d{2})", f"{m.period} {m.metric_hint}")), None)
        annual = R._zero_verdict(m.quote, m.metric_hint, f"{year}년 연간", {}) if year and m.quote else None
        records.append({"pdf": report["pdf"], "page_index": m.page, "kept": True, "metric_hint": m.metric_hint,
                        "value": m.value, "unit": m.unit, "period": m.period, "quote": m.quote,
                        "status": zero.get("status"), "cause": zero.get("cause"),
                        "source_period": zero.get("source_period"), "source_site": zero.get("source_site"),
                        "evidence_column": zero.get("evidence_column"), "recovered_from_null": recovery is not None,
                        "annual_request": {"status": annual.status, "cause": annual.cause} if annual else None})
    for entry in rm.get("unvalued_records", []) or []:
        for issue in entry.get("records", []):
            if issue.get("reason") != "zero_not_in_evidence":
                continue
            year = next((int(t) for t in __import__("re").findall(r"(20\d{2})", f"{issue.get('period')} {issue.get('metric_hint')}")), None)
            annual = (R._zero_verdict(issue.get("quote", ""), issue.get("metric_hint", ""), f"{year}년 연간", {})
                      if year and issue.get("quote") else None)
            records.append({"pdf": report["pdf"], "page_index": entry.get("page"), "kept": False,
                            "metric_hint": issue.get("metric_hint"), "value": 0, "unit": issue.get("unit"),
                            "period": issue.get("period"), "quote": issue.get("quote"), "status": issue.get("status"),
                            "cause": issue.get("cause"), "source_period": issue.get("evidence_period"),
                            "source_site": issue.get("evidence_site"), "evidence_column": issue.get("evidence_column"),
                            "annual_request": {"status": annual.status, "cause": annual.cause} if annual else None})
    records.append({"pdf": report["pdf"], "summary": True, "chunks": rm.get("chunks"),
                    "cache_hits": rm.get("ocr_cache_hits"), "cache_misses": rm.get("ocr_cache_misses"),
                    "status": rm.get("extraction_status"), "metrics_total": len(ext.metrics)})
OUT.parent.mkdir(parents=True, exist_ok=True)
OUT.write_text(json.dumps({"code_path": str(CODE), "offline": OFFLINE, "elapsed_seconds": round(time.monotonic() - started, 1),
                           "llm": llm_cache.stats(), "llm_response_events": llm_cache.response_events(),
                           "offline_misses": len(misses), "records": records},
                          ensure_ascii=False, indent=2, default=str), encoding="utf-8")
zeros = [r for r in records if not r.get("summary")]
print(f"zero records {len(zeros)} kept {sum(r['kept'] for r in zeros)} llm {llm_cache.stats()} misses {len(misses)}")
