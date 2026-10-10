#!/usr/bin/env python3
"""응답 품질 채점 CLI — 공통 답안 형식 v1 입력.

ESGenie와 일반 AI 대조군을 **같은 채점 로직**으로 본다. 판정 로직은
`esgenie/eval/response_scoring.py`에 한 곳만 있고 이 스크립트는 입출력만 한다
(로직을 복제하지 않는다).

입력 두 가지:

1. `--answers`  공통 형식 문서(대조군·ESGenie 공용). 단계는 `meta.stage`에서 읽는다.
2. `--esgenie-result STAGE=PATH`  ESGenie `result.json`.
   `esgenie/eval/esgenie_adapter.py`로 공통 형식으로 바꾼 뒤 같은 로직에 넣는다.

사용법과 5회 반복 실행 처리는 `docs/응답품질채점_사용법_2026-10-06.md`를 본다.

산출물은 실행별로 따로 쓴다. 기존 파일을 덮어쓰지 않고(`--overwrite` 없으면 거부),
여러 실행의 같은 qid를 하나로 합치지 않는다. 단계마다 문서는 하나만 받는다.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from esgenie.eval import answer_format as af          # noqa: E402
from esgenie.eval import esgenie_adapter as adapter   # noqa: E402
from esgenie.eval import response_scoring as rs       # noqa: E402


def _split_stage_path(ap: argparse.ArgumentParser, spec: str) -> tuple[str, str]:
    stage, sep, path = spec.partition("=")
    if not sep or not path:
        ap.error(f"--esgenie-result 형식은 STAGE=PATH다 (받은 값 '{spec}')")
    if stage not in af.STAGES:
        ap.error(f"단계 '{stage}'는 {af.STAGES} 중 하나가 아니다")
    return stage, path


def _read_qid_file(path: str) -> tuple[str, ...]:
    """문항 ID 목록 파일을 읽는다 — 한 줄에 하나, `#`은 주석.

    제품 패키지(`esgenie.supplychain`)를 설치하지 않고도 채점할 수 있게 하기 위한
    입구다. **문항 ID만 들어 있고 정답은 없다.** 비었거나 중복이면 거부한다.
    """
    lines = Path(path).read_text(encoding="utf-8").splitlines()
    qids = [s for s in (line.split("#", 1)[0].strip() for line in lines) if s]
    if not qids:
        raise ValueError(f"문항 ID 목록이 비어 있다: {path}")
    dupes = sorted({q for q in qids if qids.count(q) > 1})
    if dupes:
        raise ValueError(f"문항 ID 목록에 중복이 있다: {path} {dupes}")
    return tuple(qids)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--labels", required=True, help="정답 라벨 CSV 경로")
    ap.add_argument("--answers", action="append", default=[], metavar="PATH",
                    help="공통 형식 응답 문서(단계마다 하나). 반복 지정 가능")
    ap.add_argument("--esgenie-result", action="append", default=[], metavar="STAGE=PATH",
                    help="ESGenie result.json. 어댑터로 공통 형식으로 바꿔 채점한다")
    ap.add_argument("--run-id", help="--esgenie-result를 쓸 때 필수. 실행마다 달라야 한다")
    ap.add_argument("--dataset-tag", choices=list(af.DATASET_TAGS),
                    help="--esgenie-result를 쓸 때 필수. §4 원문 자료 집합 구분")
    ap.add_argument("--material-kind", default=None, choices=list(af.MATERIAL_KINDS),
                    help="입력 자료 구성 — dataset_tag와 다른 축이다(섞어 적지 않는다)")
    ap.add_argument("--data-source", help="사람이 읽는 자유 설명. 집계에 쓰지 않는다")
    ap.add_argument("--system", default="esgenie", help="--esgenie-result 변환 시 기록할 시스템 이름")
    ap.add_argument("--model", default=None, help="--esgenie-result 변환 시 기록할 모델")
    ap.add_argument("--framework", default="rba42", help="문항 구성을 읽을 양식 키")
    ap.add_argument("--expected-qids", metavar="PATH",
                    help="문항 ID 목록 파일(한 줄에 하나, '#'은 주석). 주면 양식 대신 "
                         "이것을 쓴다 — 제품 패키지를 설치하지 않고도 채점할 수 있다")
    ap.add_argument("--write-common", metavar="DIR",
                    help="변환한 공통 형식 문서를 이 디렉터리에 남긴다(원본 추적용)")
    ap.add_argument("--out", required=True, help="채점 결과 JSON 경로(실행별로 다른 경로)")
    ap.add_argument("--overwrite", action="store_true",
                    help="기존 결과 파일을 덮어쓴다. 기본은 거부한다")
    args = ap.parse_args(argv)

    if not args.answers and not args.esgenie_result:
        ap.error("--answers 또는 --esgenie-result 중 하나는 있어야 한다")
    if args.esgenie_result and not (args.run_id and args.dataset_tag):
        ap.error("--esgenie-result를 쓰면 --run-id와 --dataset-tag가 필요하다")

    out = Path(args.out)
    if out.exists() and not args.overwrite:
        ap.error(f"{out}가 이미 있다. 실행별 결과를 덮어쓰지 않는다(--overwrite로만 허용)")

    documents: list[dict] = []
    try:
        for path in args.answers:
            documents.append(af.load_document(path))
        for spec in args.esgenie_result:
            stage, path = _split_stage_path(ap, spec)
            doc = adapter.convert_result_file(
                path, stage=stage, run_id=args.run_id, framework=args.framework,
                dataset_tag=args.dataset_tag, material_kind=args.material_kind,
                data_source=args.data_source, system=args.system, model=args.model)
            if args.write_common:
                target = Path(args.write_common) / f"{args.system}_{args.run_id}_{stage}.json"
                if target.exists() and not args.overwrite:
                    ap.error(f"{target}가 이미 있다. 실행별 변환 결과를 덮어쓰지 않는다")
                af.dump_document(doc, target)
                print(f"wrote {target}")
            documents.append(doc)
    except (af.FormatError, OSError, ValueError) as exc:
        print(f"입력 오류: {exc}", file=sys.stderr)
        return 2

    try:
        if args.expected_qids:
            qids = _read_qid_file(args.expected_qids)
            qid_source = f"file:{args.expected_qids}"
        else:
            qids = rs.framework_qids(args.framework)
            qid_source = f"framework:{args.framework}"
    except (OSError, ValueError, KeyError, ImportError) as exc:
        print(f"문항 구성을 읽을 수 없다: {exc}", file=sys.stderr)
        return 2

    issues: list[dict] = []
    for doc in documents:
        key = af.document_key(doc.get("meta") or {})
        for issue in af.validate_document(doc, qids):
            issues.append({"document": "/".join(key), **issue})

    try:
        labels = rs.load_labels(args.labels)
        report = rs.score_documents(labels, documents, qids)
    except (rs.LabelError, af.FormatError, OSError) as exc:
        print(f"채점 전 입력 오류: {exc}", file=sys.stderr)
        return 2

    payload = report.to_dict()
    payload["format_issues"] = issues
    payload["label_file"] = str(args.labels)
    payload["expected_qids_source"] = qid_source
    payload["expected_qid_count"] = len(qids)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                   encoding="utf-8")
    print(f"wrote {out}")
    print(f"행 {len(report.rows)}건 · 집계 {report.bucket_counts} · 형식 문제 {len(issues)}건")
    if issues:
        print("형식 문제가 있다. 결과를 그대로 쓰지 말고 입력을 고쳐라.", file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
