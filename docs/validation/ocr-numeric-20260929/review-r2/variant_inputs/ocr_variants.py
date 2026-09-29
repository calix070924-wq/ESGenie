"""변형 입력을 실제 OCR(Upstage Document Parse)·실제 LLM 설정으로 문서별 추출 → 값 기록.

사용: ocr_variants.py <code_path> <env_file> <cache_dir(새 경로)> <out.json>
esgenie는 <code_path>에서 import한다(수정 전·후 작업 폴더 비교용). 비밀키 값은 남기지 않는다.
"""
import hashlib
import json
import sys
import time
from pathlib import Path

code_path, env_file, cache_dir, out = map(Path, sys.argv[1:5])
if cache_dir.exists() and any(cache_dir.iterdir()):
    raise SystemExit(f"비어 있지 않은 캐시 경로: {cache_dir}")
sys.path.insert(0, str(Path(__file__).resolve().parents[3].parent.parent / "scripts"))
import live_numeric_rehearsal as L  # noqa: E402  (live_env·import_from·UpstageCounter 재사용)

L.live_env(cache_dir)
loaded, settings = L.import_from(code_path, env_file)
from esgenie import llm_cache  # noqa: E402
from esgenie.ssot import ocr_router as R  # noqa: E402

commit = L.subprocess.run(["git", "-C", str(code_path), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
upstage = L.UpstageCounter()
llm_cache.reset_stats()
docs = []
for pdf in sorted(Path(__file__).parent.glob("R*.pdf")):
    upstage.reset()
    started = time.monotonic()
    decision = R.route_document(str(pdf))
    ext = R.extract_document(str(pdf), decision)
    tm = ext.router_meta.get("table_metrics") or {}
    docs.append({
        "file": pdf.name, "sha256": hashlib.sha256(pdf.read_bytes()).hexdigest(),
        "doc_type": decision.doc_type, "engine": ext.router_meta.get("engine"), "seconds": round(time.monotonic() - started, 2),
        "upstage": {k: v for k, v in upstage.summary().items() if k != "events"},
        "metrics": [{"hint": m.metric_hint, "value": m.value, "unit": m.unit, "code": m.kesg_code_guess,
                     "period": m.period, "page": m.page,
                     **{k: (m.source_detail or {}).get(k) for k in
                        ("role", "row_label", "scope", "value_source", "duplicate_status", "header", "raw_text")},
                     "index_check": ((m.source_detail or {}).get("index_check") or {}).get("status")}
                    for m in ext.metrics],
        "review": [r.get("reason") for r in tm.get("review", [])],
        "absent_cells": [{k: a.get(k) for k in ("role", "header", "raw_text", "state", "table_id")}
                         for a in tm.get("absent_cells", [])],
        "hitl_required": ext.router_meta.get("hitl_required"),
    })
record = {"code_path": str(code_path), "commit": commit, "imported_esgenie": str(loaded),
          "force_mock": settings.force_mock, "llm_key_present": bool(L.os.getenv("OPENAI_API_KEY") or L.os.getenv("AZURE_OPENAI_API_KEY")),
          "ocr_key_present": bool(L.os.getenv("UPSTAGE_API_KEY")), "cache_dir": str(cache_dir),
          "llm": llm_cache.stats(), "documents": docs}
out.write_text(json.dumps(record, ensure_ascii=False, indent=1), encoding="utf-8")
for d in docs:
    print(d["file"], d["doc_type"], d["engine"], d["upstage"], [(m["value"], m["unit"], m["code"], m["period"], m["scope"]) for m in d["metrics"]])
print("llm", record["llm"])
