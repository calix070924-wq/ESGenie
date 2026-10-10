#!/usr/bin/env python3
"""독립 검토 인정 상태를 집합·사람별로 센다.

표본 패키지의 `sample.json`과 노출 기록 CSV만 읽는다. **라벨도 응답도 읽지 않는다** —
일치·불일치를 보고 제외 여부를 정하지 않기 위해서다(구조적으로 볼 수 없다).

내는 것: 원래 표본 행 수 / 독립 확인 완료 / 노출 제외 / 확인 대기와 그 이유.
**일치율은 내지 않는다.** 라벨이 없고, 확인 대기가 남아 있으면 분모가 확정되지 않는다.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from esgenie.eval import independence as ind     # noqa: E402


def sample_rows(sample_json: Path) -> tuple[str, list[tuple[str, str]]]:
    doc = json.loads(sample_json.read_text(encoding="utf-8"))
    rows = [(r["stage"], r["qid"]) for r in doc["rows"]]
    return doc.get("set_id", sample_json.parent.name), rows


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--sample", action="append", required=True, metavar="PATH",
                    help="표본 패키지의 sample.json. 반복 지정 가능")
    ap.add_argument("--exposures", required=True, help="노출 기록 CSV")
    ap.add_argument("--labeler", action="append", required=True,
                    help="라벨 작성자 ID. 반복 지정 가능")
    ap.add_argument("--out", help="결과 JSON 경로")
    args = ap.parse_args(argv)

    try:
        exposures = ind.load_exposures(args.exposures)
    except (ind.ExposureError, OSError) as exc:
        print(f"노출 기록 오류: {exc}", file=sys.stderr)
        return 2

    reports = []
    for sample in args.sample:
        try:
            set_id, rows = sample_rows(Path(sample))
        except (OSError, KeyError, json.JSONDecodeError) as exc:
            print(f"표본을 읽을 수 없다: {sample} — {exc}", file=sys.stderr)
            return 2
        for labeler in args.labeler:
            rep = ind.classify(rows, exposures, labeler=labeler, set_id=set_id)
            reports.append(rep)
            print(f"[{set_id} / {labeler}] 표본 {rep.sample_rows}행 · "
                  f"독립 확인 {rep.independent_rows} · 노출 제외 {rep.excluded_rows} · "
                  f"확인 대기 {rep.pending_rows}")
            if rep.excluded_qids:
                print(f"  제외 문항: {', '.join(rep.excluded_qids)}")
            if rep.agreement_blocked_reason:
                print(f"  {rep.agreement_blocked_reason}")

    print()
    print(ind.label_usability_note())

    if args.out:
        payload = {"reports": [r.to_dict() for r in reports],
                   "label_usability": ind.label_usability_note(),
                   "exposure_log": args.exposures}
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                                  encoding="utf-8")
        print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
