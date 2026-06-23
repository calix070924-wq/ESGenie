# -*- coding: utf-8 -*-
"""ocr_accuracy_eval.py — 시연증빙 실파일 OCR 추출 정확도 채점.

정답값(시연세트 README/PDF 인쇄값) 대비 추출 metric을 비교해 정확도를 낸다.
- 샌드박스: Upstage egress 차단 → pymupdf+regex 폴백 경로로 '파이프라인' 정확도 측정.
- Mac(키 유효): engine=upstage_dp 로 닫혀 '실제 OCR 엔진' 정확도까지 측정.
"""
import sys, glob
sys.path.insert(0, '.')
from esgenie.config import SETTINGS
from esgenie.ssot import ocr_router as R

D = '시연증빙세트_한울정밀공업/'
# (파일패턴, 기대 doc_type, [(code, 기대값, 단위, 허용오차)])
GT = [
    (D+'01_*.pdf', 'kepco_bill', [('E-4-1', 142560.0, 'kWh', 0.5)]),
    (D+'02_*.pdf', 'gas_bill',   [('E-4-1', 360772.0, 'MJ', 0.5)]),
    (D+'03_*.pdf', 'waste_ledger', [('E-6-1', 18.4, 'ton', 0.05),
                                    ('E-6-2', 29.3, '%', 0.05)]),
]

def find(metrics, code):
    return [m for m in metrics if m.kesg_code_guess == code]

total=hit=0; dup=0; engine_seen=set()
print(f"{'파일':28} {'라우팅':14} {'코드':6} {'기대':>10} {'추출':>12} 판정")
print('-'*82)
for pat, exp_type, items in GT:
    f = sorted(glob.glob(pat))[0]
    dec = R.route_document(f); ext = R.extract_document(f, dec)
    engine_seen.add((ext.router_meta or {}).get('engine') or 'fallback')
    name = f.split('/')[-1][:26]
    route_ok = '✅' if dec.doc_type==exp_type else '❌'
    for code, want, unit, tol in items:
        total+=1
        cands = find(ext.metrics, code)
        if len(cands)>1: dup+=1
        got = cands[0].value if cands else None
        ok = got is not None and abs(got-want) <= tol
        if ok: hit+=1
        dupflag = f" ⚠중복x{len(cands)}" if len(cands)>1 else ""
        print(f"{name:28} {exp_type:12}{route_ok} {code:6} {want:>10} {str(got):>12} {'✅' if ok else '❌'}{dupflag}")

print('-'*82)
print(f"엔진: {engine_seen}")
print(f"핵심 ESG 수치 정확도: {hit}/{total} = {100*hit/total:.0f}%   | 중복노드(false positive): {dup}건")
