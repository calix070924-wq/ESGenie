# -*- coding: utf-8 -*-
"""dump_clauses_for_human_judge.py — 사람 recall 판정용 시트 생성.

각 gold 문서에서 AI가 뽑은 조항(clauses)을 추출해, gold 핵심사실과 나란히 놓은
CSV를 만든다. 사람이 문서별로 "이 사실이 AI 조항에 담겼나(O/X)"만 채우면 진짜 recall이 나온다.

- 추출 = gpt-4.1-mini(Azure). Claude 불필요. 판정은 사람.
- 실행:  ESGENIE_STRICT=1 python scripts/dump_clauses_for_human_judge.py

출력: data/benchmark_ocr/human_recall_judge.csv
  컬럼: doc_id, fact_id, fact_text, ai_clauses(참고), heuristic_hint, covered(사람이 채움 O/X)
"""
from __future__ import annotations
import csv
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from esgenie.ssot import ocr_router as R

GOLD = Path("data/benchmark_ocr/unstructured_gold.json")
OUT = Path("data/benchmark_ocr/human_recall_judge.csv")
_STOP = {"의", "를", "을", "이", "가", "에", "는", "은", "로", "으로", "및", "등", "한다", "있다", "위한", "따라", "한"}


def _toks(t: str) -> set[str]:
    return {x.lower() for x in re.findall(r"[\w\d가-힣]+", t) if len(x) >= 2 and x not in _STOP}


def _heuristic_covered(fact: str, clause_texts: list[str], thr: float = 0.4) -> bool:
    ft = _toks(fact)
    if not ft:
        return True
    for c in clause_texts:
        if len(ft & _toks(c)) >= max(1, len(ft) * thr):
            return True
    return False


def main() -> None:
    gold = json.loads(GOLD.read_text(encoding="utf-8"))
    rows = []
    n_hint_cov = 0
    for d in gold["docs"]:
        f = d["file"]
        if not Path(f).exists():
            print(f"  ⚠ 파일 없음: {f}")
            continue
        dec = R.route_document(f)
        ext = R.extract_document(f, dec)
        clause_texts = [c.text for c in ext.clauses]
        clauses_joined = "  ▸ ".join(f"[{i}] {t}" for i, t in enumerate(clause_texts))
        for fact in d.get("facts_gold", []):
            ftext = fact["text"] if isinstance(fact, dict) else fact
            fid = fact.get("id", "") if isinstance(fact, dict) else ""
            hint = "O" if _heuristic_covered(ftext, clause_texts) else "X"
            if hint == "O":
                n_hint_cov += 1
            rows.append({
                "doc_id": d["doc_id"],
                "fact_id": fid,
                "fact_text": ftext,
                "ai_clauses": clauses_joined,
                "heuristic_hint": hint,
                "covered": "",  # 사람이 O/X 로 채움
            })
        print(f"  {d['doc_id']:28} facts={len(d.get('facts_gold',[]))} clauses={len(clause_texts)}")

    cols = ["doc_id", "fact_id", "fact_text", "ai_clauses", "heuristic_hint", "covered"]
    with open(OUT, "w", encoding="utf-8-sig", newline="") as fp:
        w = csv.DictWriter(fp, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)
    print(f"\n생성: {OUT}  ({len(rows)}개 사실)")
    print(f"참고(휴리스틱 힌트): 커버 {n_hint_cov}/{len(rows)} — 사람이 'covered' 칸에 최종 O/X 를 채우세요.")


if __name__ == "__main__":
    main()
