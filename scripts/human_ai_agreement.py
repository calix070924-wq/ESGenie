# -*- coding: utf-8 -*-
"""human_ai_agreement.py — 사람(장지민) 라벨 ↔ AI(passA) 라벨 일치율 (결정적·재현 가능).

DoD ①의 "2인 독립 + 일치율"을 재현 가능한 코드로 고정한다.
- 입력: unstructured_gold.json 의 문서별 `facts_gold_human`(사람) vs `facts_gold_ai`(AI passA).
- 출력(read-only): 문서별/전체 일치율. **파일을 쓰지 않는다** (수기 값·부작용 배제).

일치 판정(결정적):
  - 유의미 토큰 = 길이 2+ (한글/영숫자), 불용어 제외.
  - 토큰 A가 토큰 B에 "매칭" = 둘 중 짧은 쪽이 긴 쪽의 접두어(len 2+).
    (한국어 조사/어미 변화 흡수: 초과↔초과하지, 12시간↔12시간을, 연장근로↔연장근로는)
  - fact 쌍 점수 = 접두어매칭 토큰수 / min(|A|,|B|) + (공유 숫자 있으면 +0.15).
  - 점수 >= 0.5 이면 같은 사실로 간주. 문서별 그리디 1:1 매칭.
  - 일치율 = 2 * matched / (|사람 facts| + |AI facts|)  (Dice, compute_gold_agreement와 동일 형식).

사용:  python scripts/human_ai_agreement.py
"""
from __future__ import annotations
import json
import re
import sys
from pathlib import Path

GOLD_PATH = Path("data/benchmark_ocr/unstructured_gold.json")
_STOP = {"및", "등", "또는", "관련", "대한", "따른", "위한", "통한", "위해", "관리", "의무", "필수", "지정", "실시", "수행", "확보"}


def _tokens(text: str) -> list[str]:
    toks = re.findall(r"[0-9A-Za-z가-힣]+", text)
    return [t.lower() for t in toks if len(t) >= 2 and t not in _STOP]


def _nums(text: str) -> set[str]:
    return set(re.findall(r"\d+", text))


def _prefix_match(a: str, b: str) -> bool:
    lo, hi = (a, b) if len(a) <= len(b) else (b, a)
    return len(lo) >= 2 and hi.startswith(lo)


def _overlap(ta: list[str], tb: list[str]) -> int:
    used = set()
    n = 0
    for x in ta:
        for j, y in enumerate(tb):
            if j in used:
                continue
            if x == y or _prefix_match(x, y):
                used.add(j)
                n += 1
                break
    return n


def _score(fa: str, fb: str) -> float:
    ta, tb = _tokens(fa), _tokens(fb)
    if not ta or not tb:
        return 0.0
    ov = _overlap(ta, tb)
    base = ov / min(len(ta), len(tb))
    bonus = 0.15 if (_nums(fa) & _nums(fb)) else 0.0
    return base + bonus


def agreement(human: list[str], ai: list[str], threshold: float = 0.5) -> tuple[int, int, int]:
    """Returns (matched, n_human, n_ai). Greedy 1:1 best-match."""
    used = set()
    matched = 0
    for h in human:
        best, best_j = 0.0, -1
        for j, a in enumerate(ai):
            if j in used:
                continue
            s = _score(h, a)
            if s > best:
                best, best_j = s, j
        if best_j >= 0 and best >= threshold:
            used.add(best_j)
            matched += 1
    return matched, len(human), len(ai)


def main() -> None:
    if not GOLD_PATH.exists():
        print(f"ERROR: {GOLD_PATH} not found.")
        sys.exit(1)
    gold = json.loads(GOLD_PATH.read_text(encoding="utf-8"))
    # 스캔본은 디지털과 동일 원문 → 중복 집계 방지 위해 디지털 문서만 대상
    seen: set[tuple] = set()
    tot_m = tot_h = tot_a = 0
    print("=" * 68)
    print("사람(장지민) ↔ AI(passA) 라벨 일치율  [결정적·재현 가능]")
    print("=" * 68)
    for d in gold["docs"]:
        if d.get("channel_variant") != "digital":
            continue
        human = [f["text"] if isinstance(f, dict) else f for f in d.get("facts_gold_human", [])]
        ai = [f["text"] if isinstance(f, dict) else f for f in d.get("facts_gold_ai", [])]
        if not human and not ai:
            continue
        m, nh, na = agreement(human, ai)
        agr = 2 * m / (nh + na) if (nh + na) else 1.0
        tot_m += m
        tot_h += nh
        tot_a += na
        print(f"  {d['doc_id']:28} human={nh} ai={na} matched={m}  agreement={agr:.1%}")
    overall = 2 * tot_m / (tot_h + tot_a) if (tot_h + tot_a) else 0.0
    print("-" * 68)
    print(f"  전체: matched={tot_m}  (human={tot_h} + ai={tot_a})  →  agreement_human = {overall:.1%}")
    print("=" * 68)
    print(f"AGREEMENT_HUMAN={overall:.4f}")


if __name__ == "__main__":
    main()
