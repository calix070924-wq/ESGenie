import sys, json, glob, os
from esgenie.ssot import ocr_router as R
R._get_openai_key=lambda:None; R._get_anthropic_key=lambda:None; R._get_upstage_key=lambda:None
ROOT="/Users/heojeongmin/Documents/Claude/Projects/ESGenie"
files=sorted(glob.glob(ROOT+"/output/pdf/한울정밀_촬영세트_BM개편_20260928/0[12]_*/*.pdf"))+sorted(glob.glob(ROOT+"/시연증빙세트_한울정밀공업/*.pdf"))
out={}
for f in files:
    d=R.route_document(f, preview_text=R._extract_text_pymupdf(f, max_pages=1)).doc_type
    if d not in ("kepco_bill","gas_bill","water_bill","waste_ledger"): out[os.path.basename(f)]=d; continue
    ext=R._extract_structured_no_llm(f,doc_type=d)
    out[os.path.basename(f)]={"doc_type":d,"metrics":sorted((m.value,m.unit,m.kesg_code_guess or "",m.page if m.page is not None else -1) for m in ext.metrics)}
json.dump(out,sys.stdout,ensure_ascii=False,indent=1,sort_keys=True)
