"""모의 모드(LLM·Upstage 호출 없음) pymupdf 경로 스냅샷. 작업 디렉터리의 esgenie를 쓴다 — main 작업 폴더에서 돌리면 수정 전, 이 브랜치에서 돌리면 수정 후."""
import json, os, sys, hashlib
os.environ["ESGENIE_FORCE_MOCK"]="1"
sys.path.insert(0, os.getcwd())
from esgenie.ssot.ocr_router import _extract_structured_no_llm, route_document
R="/Users/heojeongmin/Documents/Claude/Projects/ESGenie"
old=[(f"{R}/시연증빙세트_한울정밀공업/01_전기요금청구서_2026-05.pdf","kepco_bill"),
     (f"{R}/시연증빙세트_한울정밀공업/02_도시가스요금고지서_2026-05.pdf","gas_bill"),
     (f"{R}/시연증빙세트_한울정밀공업/03_사업장폐기물_위탁처리명세_2026-04.pdf","waste_ledger")]
import glob
bm=sorted(glob.glob(f"{R}/output/pdf/한울정밀_촬영세트_BM개편_20260928/0[12]_*/*.pdf"))
out={"old_set":[], "bm_set":[]}
for p,k in old:
    e=_extract_structured_no_llm(p, doc_type=k)
    out["old_set"].append({"file":os.path.basename(p),"sha256":hashlib.sha256(open(p,'rb').read()).hexdigest(),"doc_type":k,
      "metrics":[(m.metric_hint,m.value,m.unit,m.kesg_code_guess,m.bbox,m.page) for m in e.metrics]})
for p in bm:
    d=route_document(p)
    rec={"file":os.path.basename(p),"route":[d.channel.value,d.doc_type,d.confidence,d.matched_keywords]}
    if d.channel.value=="structured":
        e=_extract_structured_no_llm(p, doc_type=d.doc_type)
        rec["metrics"]=[(m.metric_hint,m.value,m.unit,m.kesg_code_guess,m.bbox,m.page) for m in e.metrics]
    out["bm_set"].append(rec)
open(sys.argv[1],"w").write(json.dumps(out,ensure_ascii=False,indent=1))
