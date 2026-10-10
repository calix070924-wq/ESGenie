#!/usr/bin/env python3
"""양식의 문항 ID 목록을 평문 파일로 내보낸다.

왜 필요한가: 채점기의 구조 점검에는 "이 양식에 어떤 문항이 있는가"만 필요하다. 그런데
`esgenie.supplychain`을 거쳐 읽으면 내보내기 의존성(openpyxl·reportlab)까지 설치해야
한다. B가 채점기만 돌려 보려면 그 설치가 불필요한 장벽이다. 그래서 문항 ID 목록을
파일로 내보내 두고, 채점 CLI가 `--expected-qids`로 받게 한다.

**이 파일에는 문항 ID만 들어간다.** 문항 문구도 정답도 넣지 않는다 — 문구는 표본
패키지의 `questions.csv`에 있고, 정답은 어디에도 없다.
"""
from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from esgenie.eval import response_scoring as rs     # noqa: E402


def export(framework_key: str) -> str:
    qids = rs.framework_qids(framework_key)
    if not qids:
        raise SystemExit(f"양식 '{framework_key}'에 문항이 없다")
    header = [
        f"# {framework_key} 문항 ID 목록 — 문항 수 {len(qids)}",
        "# 자동 생성: scripts/export_framework_qids.py",
        "# 문항 ID만 들어 있다. 문항 문구와 정답은 들어 있지 않다.",
        "# 쓰는 곳: scripts/eval_response_quality.py --expected-qids <이 파일>",
    ]
    return "\n".join(header + list(qids)) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--framework", default="rba42")
    ap.add_argument("--out", required=True)
    ap.add_argument("--overwrite", action="store_true")
    args = ap.parse_args(argv)

    out = Path(args.out)
    if out.exists() and not args.overwrite:
        print(f"{out}가 이미 있다 (--overwrite로만 덮어쓴다)", file=sys.stderr)
        return 2

    text = export(args.framework)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    body = [l for l in text.splitlines() if not l.startswith("#")]
    print(f"wrote {out} — 문항 {len(body)}개")
    print(f"sha256: {hashlib.sha256(out.read_bytes()).hexdigest()}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
