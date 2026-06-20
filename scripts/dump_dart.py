# -*- coding: utf-8 -*-
"""
dump_dart.py — 특정 상장사의 DART 사업보고서에서 우리가 '실제로 받은 텍스트'를 까본다.

G-1-2(사외이사 비율) 같은 항목이 왜 안 잡히는지 진단용.
  ① 사업보고서 원문 텍스트 길이 + 800k 컷오프에 걸렸는지
  ② 거버넌스 키워드(사외이사·이사회·배당성향·출석률·이사의 수 등) 존재/위치/문맥
  ③ _regex_extract_kesg 가 뽑아낸 코드들

반드시 **로컬 PC**에서 (DART_API_KEY 필요). Claude 샌드박스는 외부망 차단.

사용:
    python3 -m scripts.dump_dart 화신
    python3 -m scripts.dump_dart 화신 2024     # 사업연도 지정
"""
from __future__ import annotations
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def main():
    sys.path.insert(0, str(REPO))
    from esgenie import dart_client as dc

    q = sys.argv[1] if len(sys.argv) > 1 else "화신"
    years = [int(sys.argv[2])] if len(sys.argv) > 2 else [2024, 2025, 2023]

    hits = dc.search_companies(q)
    if not hits:
        sys.exit(f"[!] '{q}' DART 검색 결과 없음")
    corp_code = hits[0]["corp_code"]
    corp_name = hits[0]["corp_name"]
    print(f"대상: {corp_name} (corp_code={corp_code})")
    print("=" * 78)

    text = ""
    used_year = None
    for y in years:
        fin = dc._dart_get("/fnlttSinglAcnt.json", corp_code=corp_code,
                           bsns_year=str(y), reprt_code="11011", fs_div="CFS") or {}
        lst = fin.get("list") or []
        rcept = lst[0].get("rcept_no", "") if lst else ""
        if not rcept:
            print(f"  {y}년: 재무제표/rcept_no 없음")
            continue
        text = dc._fetch_report_zip_text(rcept)
        used_year = y
        print(f"  {y}년: rcept_no={rcept}  텍스트 {len(text):,}자")
        if text:
            break

    if not text:
        sys.exit("[!] 사업보고서 원문 텍스트를 못 받음")

    CUTOFF = 800_000
    print(f"\n800k 컷오프: {'걸림 — 뒷부분 잘렸을 수 있음 ⚠' if len(text) >= CUTOFF else '여유 있음'}")
    print("=" * 78)

    kws = ["사외이사", "이사회", "이사의 수", "등기이사", "사내이사",
           "현금배당성향", "배당성향", "출석률", "여성"]
    print("[거버넌스 키워드 존재/위치]")
    for kw in kws:
        idx = text.find(kw)
        if idx < 0:
            print(f"  ✗ {kw:10} — 없음")
        else:
            ctx = text[max(0, idx - 20): idx + 50].replace("\n", " ")
            pos = f"{idx:,}/{len(text):,}"
            cut = " (컷오프 이후!)" if idx >= CUTOFF else ""
            print(f"  ✓ {kw:10} @ {pos}{cut}  …{ctx}…")

    print("\n[_regex_extract_kesg 결과 (원문 정규식)]")
    res = dc._regex_extract_kesg(text)
    for code, v in sorted(res.items()):
        print(f"   {code}: {v.get('value')}{v.get('unit')}  ({v.get('note','')})")

    print("\n[구조화 API 결과 (배당·사외이사) — 견고 추출]")
    gov = dc._fetch_structured_governance(corp_code, used_year or years[0])
    if not gov:
        print("   (없음 — 배당/사외이사 구조화 응답 비어있음. 연도/보고서 확인)")
    for code, v in sorted(gov.items()):
        print(f"   {code}: {v.get('value')}{v.get('unit')}  ({v.get('note','')})")

    print("\n[최종 병합 (구조화 우선)]")
    final = dc._extract_kesg_from_dart(corp_code, used_year or years[0])
    for code, v in sorted(final.items()):
        print(f"   {code}: {v.get('value')}{v.get('unit')}  ({v.get('note','')})")

    print("\n판정 가이드:")
    print("  · '사외이사'가 '없음' → 컷오프/원문 미수록. max_chars↑ 또는 구조화 엔드포인트 필요.")
    print("  · '있음'인데 G-1-2 미추출 → 표로 라벨-숫자 분리됨. 표 파싱 보강 필요.")
    print("  · '컷오프 이후!'로 찍히면 → 800k 잘림이 원인. 확정.")


if __name__ == "__main__":
    main()
