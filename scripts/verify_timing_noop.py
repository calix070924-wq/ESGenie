#!/usr/bin/env python
"""계측이 동작을 바꾸지 않았는지 증명한다 (작업지시서 A §3.3 ①).

2026-10-11 강화(정민 보완 지시서 T1). 이전 판은 **모든 문자열**의 ISO 시각·절대경로를
치환하고 **모든 실수**를 소수 9자리로 반올림했으며, `seconds`·`duration` 같은 키를
**어느 위치에서든** 지웠다. 그래서 값의 작은 차이나 근거 인용문의 서로 다른 시각이
같은 결과로 정규화될 수 있었다. 이제는:

- **정규화하지 않는다.** 실수 반올림·문자열 치환을 하지 않는다.
- 비교에서 빼는 것은 **명시한 JSON 경로**뿐이다(`--allow`, 기본 목록은 아래). 경로가
  정확히 같을 때만 뺀다. 같은 이름의 키가 다른 위치에 있으면 그대로 비교한다.
- 뺀 경로와 그 이유, 양쪽 파일 sha256, 경로별 차이를 보고서로 남긴다.

하위 명령:

    # 덤프 — 같은 입력으로 응답서(sheet)와 파이프라인 산출물 요약을 그대로 떨군다
    python scripts/verify_timing_noop.py dump --out /tmp/after.json
    # 계측 전 코드에서 같은 명령 → /tmp/before.json
    # 비교 — 정규화 없이, 허용 경로만 빼고
    python scripts/verify_timing_noop.py compare /tmp/before.json /tmp/after.json \
        --report /tmp/noop_report.json

`result.json` 두 개를 직접 비교할 때도 `compare`를 쓴다. 기본 허용 경로는 생성 시각
`generated_at`(최상위)과 계측 필드 `timings`(최상위)뿐이다.

옛 사용법(`--out`만 주기)은 `dump`로 처리한다.

입력은 저장소에 들어 있는 공개 샘플(DART 캐시)만 쓴다. BM 원본·실제 응답은 쓰지 않는다.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

#: 기본 허용(제외) 경로와 이유. **경로가 정확히 일치할 때만** 뺀다.
#: `[*]`는 목록의 모든 원소를 뜻한다. 업무 데이터 경로는 넣지 않는다.
DEFAULT_ALLOWED: dict[str, str] = {
    "generated_at": "result.json 최상위 생성 시각(실행 메타데이터)",
    "timings": "계측으로 새로 생긴 최상위 필드 — 계측 전 코드에는 없다",
}


REPO = Path(__file__).resolve().parents[1]


def _use_code_root(root: Path) -> None:
    """어느 코드 트리의 `esgenie`를 쓸지 정한다. 다른 checkout을 잘못 import하지 않게
    맨 앞에 넣고, 실제로 불러온 위치를 덤프에 적는다."""
    sys.path.insert(0, str(root.resolve()))


def dump_payload(corp: str, areas: list[str], framework: str) -> dict[str, Any]:
    from esgenie.pipeline import run
    from esgenie.supplychain import respond_from_pipeline

    output = run(corp, areas=list(areas), save_traces=False)
    sheet = respond_from_pipeline(output, framework, enable_drafts=False)
    return {
        "sheet": sheet.to_dict(),
        "pipeline": {
            "nodes": len(output.evidence_graph.nodes),
            "text_nodes": len(output.evidence_graph.text_nodes),
            "edges": len(output.evidence_graph.edges),
            "coverage_pct": getattr(output.extraction, "coverage_pct", None),
            "profile_label": getattr(output.extraction, "profile_label", None),
            "sections": {area: {"final_score": v.final_score, "converged": v.converged,
                                "hitl_required": v.hitl_required, "final_text": v.final_text}
                         for area, v in output.sections.items()},
            "risk_rows": output.risk_rows,
            "policy_drafts": output.policy_drafts,
            "industry_module_key": output.industry_module_key,
            "requested_areas": output.requested_areas,
            "review_findings": [f.to_dict() for f in output.review_findings],
            "report_export": output.report_export,
            "item_retrievals": output.item_retrievals,
        },
    }


def _match(path: str, pattern: str) -> bool:
    """`a.b[3].c`가 `a.b[*].c`에 맞는지. 경로 전체가 같아야 한다."""
    p_parts = path.split(".")
    q_parts = pattern.split(".")
    if len(p_parts) != len(q_parts):
        return False
    for p, q in zip(p_parts, q_parts):
        if q.endswith("[*]"):
            base = q[:-3]
            if not (p.startswith(base + "[") and p.endswith("]") and p[len(base) + 1:-1].isdigit()):
                return False
        elif p != q:
            return False
    return True


def diff(a: Any, b: Any, allowed: list[str], path: str = "") -> tuple[list[dict], list[str]]:
    """정규화 없이 비교한다. (차이 목록, 실제로 뺀 경로 목록)."""
    diffs: list[dict] = []
    skipped: list[str] = []

    def walk(x: Any, y: Any, p: str) -> None:
        if p and any(_match(p, pat) for pat in allowed):
            skipped.append(p)
            return
        if type(x) is not type(y):
            diffs.append({"path": p or "<root>", "kind": "type",
                          "a": repr(x)[:120], "b": repr(y)[:120]})
            return
        if isinstance(x, dict):
            for k in sorted(set(x) | set(y)):
                sub = f"{p}.{k}" if p else str(k)
                if k not in x or k not in y:
                    if any(_match(sub, pat) for pat in allowed):
                        skipped.append(sub)
                        continue
                    diffs.append({"path": sub, "kind": "missing_in_a" if k not in x else "missing_in_b"})
                    continue
                walk(x[k], y[k], sub)
        elif isinstance(x, list):
            if len(x) != len(y):
                diffs.append({"path": p, "kind": "length", "a": len(x), "b": len(y)})
            for i, (u, v) in enumerate(zip(x, y)):
                walk(u, v, f"{p}[{i}]")
        elif x != y:
            diffs.append({"path": p or "<root>", "kind": "value",
                          "a": repr(x)[:120], "b": repr(y)[:120]})

    walk(a, b, path)
    return diffs, skipped


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def compare_files(a_path: Path, b_path: Path, allowed: dict[str, str]) -> dict[str, Any]:
    a = json.loads(a_path.read_text(encoding="utf-8"))
    b = json.loads(b_path.read_text(encoding="utf-8"))
    diffs, skipped = diff(a, b, list(allowed))
    return {
        "identical_except_allowed": not diffs,
        "a": {"path": str(a_path), "sha256": _sha(a_path)},
        "b": {"path": str(b_path), "sha256": _sha(b_path)},
        "allowed_paths": allowed,
        "skipped_paths": sorted(set(skipped)),
        "diff_count": len(diffs),
        "diffs": diffs[:500],
        "normalization": "없음 — 실수 반올림·문자열 시각/경로 치환을 하지 않는다",
    }


def _parse_allow(values: list[str] | None, no_default: bool) -> dict[str, str]:
    allowed = {} if no_default else dict(DEFAULT_ALLOWED)
    for item in values or []:
        path, _, why = item.partition("=")
        if not why.strip():
            raise SystemExit(f"--allow는 '경로=이유' 형식이어야 한다: {item!r}")
        allowed[path.strip()] = why.strip()
    return allowed


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] not in ("dump", "compare", "-h", "--help"):
        argv = ["dump", *argv]          # 옛 사용법: --out만 주는 경우

    ap = argparse.ArgumentParser(description="계측 전후 동일성 증명 (정규화 없음)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("dump", help="공개 샘플로 응답서·파이프라인 요약을 그대로 떨군다")
    d.add_argument("--corp", default="005930")
    d.add_argument("--areas", nargs="+", default=["E", "S", "G"])
    d.add_argument("--framework", default="rba42")
    d.add_argument("--out", type=Path, required=True)
    d.add_argument("--code-root", type=Path, default=REPO,
                   help="비교할 코드 트리(기본: 이 스크립트의 저장소). 계측 전 코드를 덤프할 때 쓴다")
    c = sub.add_parser("compare", help="두 JSON을 허용 경로만 빼고 비교한다")
    c.add_argument("a", type=Path)
    c.add_argument("b", type=Path)
    c.add_argument("--allow", action="append", metavar="경로=이유",
                   help="추가로 뺄 JSON 경로와 이유. 반복 가능. 예: report_export.exported_at=출력 시각")
    c.add_argument("--no-default-allow", action="store_true",
                   help="기본 허용 경로(generated_at·timings)도 빼지 않는다")
    c.add_argument("--report", type=Path)
    args = ap.parse_args(argv)

    if args.cmd == "dump":
        _use_code_root(args.code_root)
        payload = dump_payload(args.corp, args.areas, args.framework)
        import esgenie
        loaded = Path(esgenie.__file__).resolve().parents[1]
        if loaded != args.code_root.resolve():
            print(f"다른 checkout의 esgenie를 불러왔다: {loaded} (기대 {args.code_root})",
                  file=sys.stderr)
            return 2
        print(f"code root: {loaded}")
        args.out.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True,
                                       default=str), encoding="utf-8")
        print(f"wrote {args.out}")
        return 0

    report = compare_files(args.a, args.b, _parse_allow(args.allow, args.no_default_allow))
    if args.report:
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                               encoding="utf-8")
    print(f"a sha256 {report['a']['sha256']}")
    print(f"b sha256 {report['b']['sha256']}")
    print(f"뺀 경로 {len(report['skipped_paths'])}개 · 차이 {report['diff_count']}건")
    for item in report["diffs"][:20]:
        print(f"  {item['kind']}: {item['path']}")
    print("IDENTICAL (허용 경로 제외)" if report["identical_except_allowed"] else "DIFFERENT")
    return 0 if report["identical_except_allowed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
