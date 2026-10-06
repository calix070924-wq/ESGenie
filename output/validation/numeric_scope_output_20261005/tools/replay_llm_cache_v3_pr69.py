"""3차 검토 보완 §9 — 저장된 LLM 원본 응답 재생, 새 호출 없음(v2를 복사해 기록 항목만 늘렸다).

사용: python replay_llm_cache_v3.py <code_path> <out_dir> <src_cache_dir>
- v2 대비: 지표의 period·boundary 전체, 조항의 section·text·page·quote, 0 판정 기록(status·cause·page)을 남긴다.
- 원본 캐시(<src_cache_dir>)는 읽기만 하고 <out_dir>/cache_copy로 복사해 쓴다.
- 네트워크 차단, LLMClient 호출 시 예외 — 캐시 미스는 '재생 불가'로 기록된다(실호출로 대체하지 않는다).
- 원문은 원본 PDF에서 파이프라인과 같은 pymupdf 경로로 다시 뽑는다.
"""
from __future__ import annotations

import dataclasses, hashlib, json, os, shutil, socket, sys
from pathlib import Path

CODE, OUT = Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve()
ROOT = Path("/Users/heojeongmin/Documents/Claude/Projects/ESGenie")
SRC_CACHE = Path(sys.argv[3]).resolve()
PACK = ROOT / "output/pdf/한울정밀_촬영세트_BM개편_20260928"
if OUT.exists() and any(OUT.iterdir()):
    raise SystemExit(f"비어 있지 않은 출력 경로(덮어쓰기 금지): {OUT}")
OUT.mkdir(parents=True, exist_ok=True)
cache = OUT / "cache_copy"
shutil.copytree(SRC_CACHE, cache)

sys.path.insert(0, str(CODE))
from dotenv import load_dotenv
load_dotenv(ROOT / ".env")
os.environ.update(ESGENIE_FORCE_MOCK="0", ESGENIE_STRICT="1", ESGENIE_OCR_CACHE="1",
                  ESGENIE_OCR_CACHE_DIR=str(cache), HF_HUB_OFFLINE="1")


def blocked(*a, **kw):
    raise RuntimeError("replay: network forbidden")


socket.socket.connect = blocked
socket.create_connection = blocked

import esgenie
assert CODE in Path(esgenie.__file__).resolve().parents, esgenie.__file__
from esgenie.config import SETTINGS
from esgenie.ssot import ocr_cache, ocr_router as R


import esgenie.llm


class NoLiveCall(esgenie.llm.LLMClient):
    """캐시 키(연결 식별 포함)는 실제 클라이언트대로 만들고, 호출만 막는다."""

    def complete(self, **kw):
        raise RuntimeError("replay: cache miss — live LLM call forbidden")


esgenie.llm.LLMClient = NoLiveCall
R._get_upstage_key = lambda: None
SETTINGS.strict_llm = False     # 미스는 예외가 아니라 기록으로 남긴다

before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in cache.glob("*.json")}
metas = [json.loads(p.read_text())["meta"] for p in sorted(SRC_CACHE.glob("*.json"))]
results = []
for meta in sorted({(m["source_file"], m["doc_type"]) for m in metas}):
    name, doc_type = meta
    pdf = next(PACK.glob(f"0*/{name}"))
    try:
        ext = R.extract_unstructured(str(pdf), doc_type=doc_type)
        d = ext.to_dict()
        rm = d.get("router_meta", {})
        results.append({
            "source_file": name, "doc_type": doc_type,
            "status": rm.get("extraction_status"), "cache": rm.get("ocr_cache"),
            "metrics": [(m["metric_hint"], m["value"], m["unit"], m.get("page"), m.get("quote"))
                        for m in d["metrics"]],
            "metric_scopes": [{"metric_hint": m["metric_hint"], "value": m["value"], "period": m.get("period"),
                               "boundary": m.get("boundary") or {}} for m in d["metrics"]],
            "clauses": len(d.get("clauses") or []),
            "clause_items": [{"section": c.get("section"), "text": c.get("text"), "page": c.get("page"),
                              "quote": c.get("quote"), "kesg_code": c.get("kesg_code_guess")}
                             for c in d.get("clauses") or []],
            "unvalued_records": rm.get("unvalued_records", []),
            "value_reconciliations": rm.get("value_reconciliations", []),
            "router_meta_keys": sorted(rm)})
    except Exception as e:
        results.append({"source_file": name, "doc_type": doc_type, "error": repr(e)})

after = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in cache.glob("*.json")}
src = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in SRC_CACHE.glob("*.json")}
record = {"kind": "LLM 캐시 재생(새 OCR·LLM 호출 없음)", "code_path": str(CODE),
          "cache_source": str(SRC_CACHE), "cache_copy_unchanged": before == after,
          "source_cache_unchanged": src == before, "files": results}
(OUT / "replay.json").write_text(json.dumps(record, ensure_ascii=False, indent=2, default=str))
for r in results:
    print(r["source_file"], r.get("status"), r.get("cache"), r.get("error", ""),
          len(r.get("metrics", [])), "unvalued", len(r.get("unvalued_records", [])),
          "recon", len(r.get("value_reconciliations", [])))
print("cache unchanged", before == after, src == before)
