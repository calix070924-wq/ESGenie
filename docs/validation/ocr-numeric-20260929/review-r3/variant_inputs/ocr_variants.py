"""R8 변형 입력(검증용 가상 변형본 — 원본 아님)을 실제 OCR(Upstage Document Parse)·실제 LLM 설정으로 문서별 추출 → 값 기록.

사용: ocr_variants.py <code_path> <env_file> <cache_dir(새 경로)> <out.json> <live|replay> <raw_dir> [파일 glob, 기본 R8*.pdf]
esgenie는 <code_path>에서 import한다(수정 전·후 작업 폴더 비교용). 비밀키 값은 남기지 않는다.
live: 실제 Upstage 호출, 응답 본문(JSON)만 <raw_dir>/<파일명>.json에 저장(키는 요청 헤더에만 있어 남지 않는다).
replay: 같은 PDF의 저장 응답을 requests.post 자리에 돌려준다 — 표 해석부터 출력까지는 매번 새로 계산.
"""
import hashlib
import json
import sys
import time
from pathlib import Path

code_path, env_file, cache_dir, out = map(Path, sys.argv[1:5])
mode, raw_dir = sys.argv[5], Path(sys.argv[6])
assert mode in ("live", "replay")
if cache_dir.exists() and any(cache_dir.iterdir()):
    raise SystemExit(f"비어 있지 않은 캐시 경로: {cache_dir}")
sys.path.insert(0, str(Path(__file__).resolve().parents[3].parent.parent / "scripts"))
import live_numeric_rehearsal as L  # noqa: E402  (live_env·import_from·UpstageCounter 재사용)

L.live_env(cache_dir)
loaded, settings = L.import_from(code_path, env_file)
from esgenie import llm_cache  # noqa: E402
from esgenie.ssot import ocr_router as R  # noqa: E402
import requests  # noqa: E402

_post, _current = requests.post, {}


class _Saved:
    status_code = 200

    def __init__(self, body):
        self._body = body

    def raise_for_status(self):
        return None

    def json(self):
        return self._body


def _post_hook(*a, **kw):
    # 라우팅 미리보기(첫 장)와 본 추출이 따로 호출된다 — 파일별 호출 순번으로 저장·재생한다.
    _current["n"] += 1
    raw = raw_dir / f"{_current['file']}.{_current['n']}.json"
    if mode == "replay":
        return _Saved(json.loads(raw.read_text(encoding="utf-8"))["response"])
    resp = _post(*a, **kw)
    if resp.status_code == 200:
        raw_dir.mkdir(parents=True, exist_ok=True)
        raw.write_text(json.dumps({"file": _current["file"], "call": _current["n"], "pdf_sha256": _current["sha256"],
                                   "model_requested": kw.get("data", {}).get("model"), "response": resp.json()},
                                  ensure_ascii=False, indent=1), encoding="utf-8")
    return resp


requests.post = _post_hook

commit = L.subprocess.run(["git", "-C", str(code_path), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
upstage = L.UpstageCounter()
llm_cache.reset_stats()
docs = []
for pdf in sorted(Path(__file__).parent.glob(sys.argv[7] if len(sys.argv) > 7 else "R8*.pdf")):
    upstage.reset()
    _current.update(file=pdf.name, n=0, sha256=hashlib.sha256(pdf.read_bytes()).hexdigest())
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
                        ("role", "row_label", "scope", "value_source", "header", "raw_text")},
                     "index_check": ((m.source_detail or {}).get("index_check") or {}).get("status")}
                    for m in ext.metrics],
        "review": [r.get("reason") for r in tm.get("review", [])],
        "absent_cells": [{k: a.get(k) for k in ("role", "header", "raw_text", "state", "table_id")}
                         for a in tm.get("absent_cells", [])],
        "money_rows": [r for r in tm.get("review", []) if r.get("reason") == "money_row_excluded"],
        "hitl_required": ext.router_meta.get("hitl_required"),
    })
record = {"code_path": str(code_path), "commit": commit, "ocr_mode": mode, "raw_dir": str(raw_dir), "imported_esgenie": str(loaded),
          "force_mock": settings.force_mock, "llm_key_present": bool(L.os.getenv("OPENAI_API_KEY") or L.os.getenv("AZURE_OPENAI_API_KEY")),
          "ocr_key_present": bool(L.os.getenv("UPSTAGE_API_KEY")), "cache_dir": str(cache_dir),
          "llm": llm_cache.stats(), "documents": docs}
out.write_text(json.dumps(record, ensure_ascii=False, indent=1), encoding="utf-8")
for d in docs:
    print(d["file"], d["doc_type"], d["engine"], d["upstage"], [(m["value"], m["unit"], m["code"], m["period"], m["scope"]) for m in d["metrics"]])
print("llm", record["llm"])
