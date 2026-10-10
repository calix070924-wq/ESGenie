#!/usr/bin/env python
"""계측이 동작을 바꾸지 않았는지 증명한다 (작업지시서 A §3.3 ①).

**무엇을 비교하는가:** 같은 입력으로 파이프라인을 돌려 `result.json`에 들어가는 내용
(= 실사 응답서 `sheet.to_dict()`)과 파이프라인 산출물 요약을 정규화해 JSON으로 떨군다.
계측 **이전 커밋**과 **이후 커밋**에서 각각 돌려 두 파일을 `diff`하면 동일해야 한다.

**왜 두 커밋에서 돌리는가:** 계측을 끄는 플래그를 만들지 않았다. 플래그를 만들면
"끈 경로"가 실제 실행 경로와 달라져 증명이 약해진다. 대신 git worktree로 계측 전
커밋을 그대로 돌린다.

`timings`는 **비교 대상에서 뺀다.** 계측으로 새로 생긴 필드이므로 계측 전 커밋에는
존재하지 않는다. 그 외의 어떤 필드도 빼지 않는다.

실행 시각·경로처럼 **실행마다 당연히 달라지는 값**은 정규화한다. 무엇을 정규화했는지
`_normalized` 목록에 남긴다 — 조용히 지우지 않는다.

입력은 저장소에 들어 있는 공개 샘플(DART 캐시)만 쓴다. **BM 실제 응답·원본 세트는
쓰지 않는다**(라벨 확정 전 열람 금지).

사용법:
    python scripts/verify_timing_noop.py --out /tmp/noop_after.json
    # 계측 전 커밋 worktree에서
    python scripts/verify_timing_noop.py --out /tmp/noop_before.json
    diff /tmp/noop_before.json /tmp/noop_after.json && echo IDENTICAL
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

# 실행마다 달라지는 값. 무엇을 지웠는지 보고할 수 있게 목록으로 둔다.
_VOLATILE_KEYS = {
    "generated_at", "created_at", "timestamp", "produced_at", "exported_at",
    "elapsed", "elapsed_seconds", "duration", "seconds",
    # 계측으로 새로 생긴 필드 — 계측 전 커밋에는 없다
    "timings",
}
_ISO_RE = re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(\.\d+)?")
_ABS_PATH_RE = re.compile(r"/(?:private/)?(?:tmp|var|Users)/[^\s\"']+")


def _norm(value: Any) -> Any:
    """휘발성 키를 지우고 타임스탬프·절대경로를 자리표로 바꾼다."""
    if isinstance(value, dict):
        return {k: _norm(v) for k, v in sorted(value.items()) if k not in _VOLATILE_KEYS}
    if isinstance(value, list):
        return [_norm(v) for v in value]
    if isinstance(value, str):
        return _ABS_PATH_RE.sub("<PATH>", _ISO_RE.sub("<TS>", value))
    if isinstance(value, float):
        # 부동소수 표기 흔들림이 아니라 값 자체를 본다
        return round(value, 9)
    return value


def main() -> None:
    ap = argparse.ArgumentParser(description="계측 전후 result.json 동일성 증명용 덤프")
    ap.add_argument("--corp", default="005930", help="DART 샘플 종목코드 (기본 005930)")
    ap.add_argument("--areas", nargs="+", default=["E", "S", "G"])
    ap.add_argument("--framework", default="rba42")
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()

    from esgenie.pipeline import run
    from esgenie.supplychain import respond_from_pipeline

    output = run(args.corp, areas=list(args.areas), save_traces=False)
    sheet = respond_from_pipeline(output, args.framework, enable_drafts=False)

    payload = {
        # result.json에 실제로 들어가는 내용
        "sheet": sheet.to_dict(),
        # 파이프라인 산출물 — 응답서 밖의 결과도 바뀌지 않았는지 함께 본다
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
        "_normalized": sorted(_VOLATILE_KEYS),
        "_note": "timings는 계측으로 새로 생긴 필드이므로 비교 대상에서 뺐다. 그 외에 뺀 필드는 없다.",
    }
    args.out.write_text(
        json.dumps(_norm(payload), ensure_ascii=False, indent=2, sort_keys=True, default=str),
        encoding="utf-8",
    )
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
