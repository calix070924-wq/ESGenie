"""PR71 재검토 D — 09·13 원본 PDF의 인원번호별 출석대장을 직접 세어 고용형태·역할·날짜별 교육 인원 정답표를 만든다.

제품 코드(esgenie)를 쓰지 않는다. PyMuPDF로 원본 글자를 읽고 `HN-Gnn / 구분 / 시간 / 출석 상태` 4칸 묶음만 센다.
사용: python derive_training_groups.py <BM 세트 폴더> [out.json]
"""
import glob
import json
import re
import sys
from collections import Counter
from pathlib import Path

import fitz


def rows(pdf: str) -> list[tuple[str, str, str]]:
    lines = [ln.strip() for page in fitz.open(pdf) for ln in page.get_text().splitlines() if ln.strip()]
    out = []
    for i, ln in enumerate(lines):
        if re.fullmatch(r"HN-G\d{2}", ln) and i + 3 < len(lines) and lines[i + 1] in ("정규직", "기간제", "파견"):
            out.append((ln, lines[i + 1], lines[i + 3]))       # (인원번호, 구분, 출석 상태)
    return out


def main() -> None:
    pack = Path(sys.argv[1])
    first = rows(glob.glob(str(pack / "*" / "09_*.pdf"))[0])
    extra = rows(glob.glob(str(pack / "*" / "13_*.pdf"))[0])
    people = {pid: group for pid, group, _s in first}
    table = Counter()
    for pid, group, state in first:
        table[("대상", group, "2026-04-22")] += 1
        table[("참석" if state == "출석" else "미참석", group, "2026-04-22")] += 1
    for pid, group, state in extra:
        assert people.get(pid) == group, (pid, group)       # 같은 사람·같은 구분
        if state == "출석":
            table[("추가 참석", group, "2026-04-27")] += 1
    result = {"source": {"09": len(first), "13": len(extra)},
              "groups": [{"role": r, "group": g, "date": d, "count": n} for (r, g, d), n in sorted(table.items())]}
    text = json.dumps(result, ensure_ascii=False, indent=1)
    if len(sys.argv) > 2:
        Path(sys.argv[2]).write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
