#!/usr/bin/env python3
"""독립 라벨링 표본 추출 — 정답이 들어가지 않는 표본 패키지를 만든다.

왜 별도 스크립트인가: B가 ESGenie의 답을 보기 전에 **같은 문항을 독립적으로**
라벨링해야 두 라벨을 비교할 수 있다. 그래서 이 스크립트는 **응답 파일도 정답 라벨도
입력으로 받지 않는다.** 입력은 양식(`framework`)과 난수 씨값뿐이다.

- 표본은 **문항 목록을 정렬한 뒤 고정 씨값으로** 뽑는다. 같은 씨값이면 같은 표본이다.
- 시스템의 정오답·보류 여부를 보고 고르지 않는다. 그런 정보를 읽을 경로가 없다.
- 내보내는 파일에 **정답·값·근거가 들어가지 않는다.** 빈 라벨 서식과 문항 정의만 나간다.

사용법은 `docs/독립라벨링_표본패키지_B_2026-10-06.md`에 있다.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import random
import sys
from math import floor
from pathlib import Path

from esgenie.eval import answer_format as af
from esgenie.eval import response_scoring as rs
from esgenie.supplychain.frameworks import get_framework

#: 검토 집합 식별자. 두 집합의 결과는 **분리해서 보고한다** — 합치면 공식 무작위 표본의
#: 일치율이 수치형 가중으로 흔들린다.
OFFICIAL_SET_ID = "bm_rba42_v1"            # 공식 무작위 표본 (단계별 10문항)
NUMERIC_CENSUS_SET_ID = "bm_rba42_numeric_census"  # 수치형 전수 (단계별 6문항)

#: 표본 패키지가 내보내는 파일들.
MANIFEST = "sample.json"
QUESTIONS = "questions.csv"
BLANK_LABELS = "labels_blank.csv"

#: 문항 정의로 내보내는 열 — 정답이 될 수 있는 값은 없다.
QUESTION_COLUMNS = ("qid", "section", "qtype", "unit_hint", "evidence_required", "text")


def _qid_list_digest(qids: tuple[str, ...]) -> str:
    """표본을 뽑은 대상 목록 자체의 지문 — 목록이 바뀌면 표본도 못 재현한다."""
    return hashlib.sha256("\n".join(qids).encode("utf-8")).hexdigest()


def strata_sizes(sizes: dict[str, int], total: int, k: int) -> dict[str, int]:
    """유형별 배분 — 비율로 내림 배분하고 남은 자리를 큰 나머지 순으로 준다.

    유형(`qtype`)으로만 층화한다. 수치형이 6문항뿐이어서 단순 무작위로는 표본에
    한 건도 안 들어갈 수 있고, 그러면 수치 비교 규칙을 전혀 보지 못한다.
    정오답과 무관한 기준이므로 표본 선택이 결과에 기울지 않는다.
    """
    if k > total:
        raise ValueError(f"표본 수 {k}가 문항 수 {total}보다 많다")
    base = {key: floor(k * n / total) for key, n in sizes.items()}
    base = {key: min(v, sizes[key]) for key, v in base.items()}
    # 남은 자리: 나머지가 큰 유형 → 유형 이름 순(동률을 씨값 없이 가른다).
    remainders = sorted(sizes, key=lambda key: (-((k * sizes[key]) % total), key))
    i = 0
    while sum(base.values()) < k:
        key = remainders[i % len(remainders)]
        if base[key] < sizes[key]:
            base[key] += 1
        i += 1
        if i > len(remainders) * total:  # 방어: 배분할 자리가 없다
            raise ValueError("층화 배분이 끝나지 않는다")
    return base


def draw(framework_key: str, per_stage: int, seed: int, set_id: str = OFFICIAL_SET_ID) -> dict:
    """공식 무작위 표본을 뽑는다. 같은 (양식, 수, 씨값)이면 항상 같은 결과다.

    **이 표본은 고정이다.** 기대값 노출 여부나 시스템 결과를 이유로 다시 뽑지 않는다
    (`tests/test_eval_label_sample.py::test_official_sample_is_locked`).
    """
    questions = get_framework(framework_key).questions
    by_type: dict[str, list[str]] = {}
    for q in questions:
        by_type.setdefault(q.qtype, []).append(q.qid)
    for qids in by_type.values():
        qids.sort()  # 정렬이 재현의 전제다 — 양식의 선언 순서에 의존하지 않는다.

    sizes = {key: len(v) for key, v in by_type.items()}
    quota = strata_sizes(sizes, len(questions), per_stage)

    rng = random.Random(seed)
    picked: list[str] = []
    for qtype in sorted(by_type):
        picked.extend(rng.sample(by_type[qtype], quota[qtype]))
    picked.sort()

    all_qids = tuple(sorted(q.qid for q in questions))
    return {
        "set_id": set_id,
        "selection": "random_sample",
        "framework": framework_key,
        "stages": list(af.STAGES),
        "per_stage": per_stage,
        "total_rows": per_stage * len(af.STAGES),
        "seed": seed,
        "method": ("유형별 층화 후 유형 안에서 무작위 추출. 각 유형의 문항 ID를 정렬한 뒤 "
                   "random.Random(seed).sample으로 뽑는다. 단계마다 같은 문항을 쓴다 — "
                   "같은 문항의 보완 전후를 비교할 수 있게 한다. "
                   "시스템의 정오답·보류 여부는 추출에 쓰지 않는다."),
        "framework_question_count": len(questions),
        "qid_list_sha256": _qid_list_digest(all_qids),
        "strata": {key: {"population": sizes[key], "sampled": quota[key]}
                   for key in sorted(sizes)},
        "qids": picked,
        "rows": [{"stage": stage, "qid": qid} for stage in af.STAGES for qid in picked],
        "notice": ("정답이 들어 있지 않다. 라벨 서식은 비어 있으며, 사람이 원본 증빙을 보고 "
                   "채운다. A의 정답 라벨과 ESGenie 응답은 이 패키지에 포함되지 않는다."),
    }


def census(framework_key: str, qtype: str, overlap_qids: tuple[str, ...] = (),
           set_id: str = NUMERIC_CENSUS_SET_ID) -> dict:
    """한 유형을 **전수** 검토 대상으로 잡는다. 무작위가 아니므로 씨값이 없다.

    수치형은 6문항뿐이라 비율 표본으로는 거의 들어오지 않는다. 값·단위·측정 경계 비교를
    보려면 전수가 필요하다. 다만 **공식 무작위 표본과 합치지 않는다** — 전수 집합은
    모집단을 대표하지 않으므로 일치율을 함께 세면 공식 표본 수치가 왜곡된다.

    `overlap_qids`에 공식 표본의 문항을 주면 겹치는 행을 표시한다. 겹친 행은 라벨을
    **다시 매기지 않고 재사용**한다(같은 문항·같은 단계·같은 원본이므로 라벨이 같다).
    """
    questions = get_framework(framework_key).questions
    qids = sorted(q.qid for q in questions if q.qtype == qtype)
    if not qids:
        raise ValueError(f"양식 '{framework_key}'에 유형 '{qtype}' 문항이 없다")

    overlap = sorted(set(qids) & set(overlap_qids))
    fresh = [q for q in qids if q not in set(overlap)]
    all_qids = tuple(sorted(q.qid for q in questions))
    return {
        "set_id": set_id,
        "selection": "census",
        "framework": framework_key,
        "qtype": qtype,
        "stages": list(af.STAGES),
        "per_stage": len(qids),
        "total_rows": len(qids) * len(af.STAGES),
        "seed": None,  # 전수는 무작위가 아니다
        "method": (f"유형 '{qtype}' 전수. 무작위 추출이 아니므로 씨값이 없다. "
                   "공식 무작위 표본과 **분리해서 보고한다** — 합치면 공식 표본의 "
                   "일치율이 수치형 가중으로 흔들린다. "
                   "시스템의 정오답·보류 여부는 선정에 쓰지 않는다."),
        "framework_question_count": len(questions),
        "qid_list_sha256": _qid_list_digest(all_qids),
        "strata": {qtype: {"population": len(qids), "sampled": len(qids)}},
        "qids": qids,
        "overlap_with": {
            "set_id": OFFICIAL_SET_ID,
            "qids": overlap,
            "note": "공식 표본과 겹치는 문항이다. 라벨을 다시 매기지 않고 재사용한다.",
        },
        "labels_needed_qids": fresh,
        "rows": [{"stage": stage, "qid": qid,
                  "reused_from_official_sample": qid in set(overlap)}
                 for stage in af.STAGES for qid in qids],
        "notice": ("정답이 들어 있지 않다. 라벨 서식은 비어 있으며, 사람이 원본 증빙을 보고 "
                   "채운다. A의 정답 라벨과 ESGenie 응답은 이 패키지에 포함되지 않는다."),
    }


def _write_questions(path: Path, framework_key: str, qids: list[str]) -> None:
    questions = {q.qid: q for q in get_framework(framework_key).questions}
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(QUESTION_COLUMNS)
        for qid in qids:
            q = questions[qid]
            writer.writerow([q.qid, q.section, q.qtype, q.unit_hint,
                             "Y" if q.evidence_required else "N", q.text])


def _write_blank_labels(path: Path, rows: list[dict]) -> None:
    """빈 라벨 서식 — `stage`·`qid`만 채우고 나머지는 비운다.

    비운 채로 채점기에 넣으면 `LabelError`로 거부된다. 빈 서식이 정답으로 오해되어
    쓰이는 일을 막기 위해 일부러 기본값을 넣지 않는다.
    """
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(rs.LABEL_COLUMNS)
        for row in rows:
            writer.writerow([row["stage"], row["qid"]] + [""] * (len(rs.LABEL_COLUMNS) - 2))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="정답 없는 독립 라벨링 표본 패키지를 만든다")
    parser.add_argument("--framework", default="rba42")
    parser.add_argument("--mode", choices=("sample", "census"), default="sample",
                        help="sample=공식 무작위 표본, census=한 유형 전수")
    parser.add_argument("--per-stage", type=int, default=10,
                        help="sample 모드의 단계별 문항 수 (기본 10 → 두 단계 20행)")
    parser.add_argument("--seed", type=int,
                        help="sample 모드의 재현용 난수 씨값. 기록에 남긴다")
    parser.add_argument("--qtype", help="census 모드에서 전수로 볼 문항 유형 (예: numeric)")
    parser.add_argument("--set-id", help="검토 집합 식별자 (기본은 모드별 기본값)")
    parser.add_argument("--overlap-with",
                        help="census 모드에서 겹치는 행을 표시할 공식 표본의 sample.json")
    parser.add_argument("--out", required=True, help="표본 패키지를 쓸 디렉터리")
    parser.add_argument("--overwrite", action="store_true",
                        help="이미 있는 표본을 덮어쓴다 (기본은 거부)")
    args = parser.parse_args(argv)

    out = Path(args.out)
    try:
        if args.mode == "sample":
            if args.seed is None:
                raise ValueError("sample 모드에는 --seed가 필요하다")
            sample = draw(args.framework, args.per_stage, args.seed,
                          set_id=args.set_id or OFFICIAL_SET_ID)
        else:
            if not args.qtype:
                raise ValueError("census 모드에는 --qtype이 필요하다")
            if args.seed is not None:
                raise ValueError("census는 무작위가 아니다 — --seed를 쓰지 않는다")
            overlap: tuple[str, ...] = ()
            if args.overlap_with:
                official = json.loads(Path(args.overlap_with).read_text(encoding="utf-8"))
                overlap = tuple(official["qids"])
            sample = census(args.framework, args.qtype, overlap,
                            set_id=args.set_id or NUMERIC_CENSUS_SET_ID)
    except (KeyError, ValueError, OSError) as exc:
        print(f"입력 오류: {exc}", file=sys.stderr)
        return 2

    targets = [out / name for name in (MANIFEST, QUESTIONS, BLANK_LABELS)]
    existing = [p for p in targets if p.exists()]
    if existing and not args.overwrite:
        # 표본을 조용히 바꾸면 B가 받은 목록과 달라진다.
        print("입력 오류: 이미 표본이 있다. 덮어쓰려면 --overwrite: "
              + ", ".join(str(p) for p in existing), file=sys.stderr)
        return 2

    out.mkdir(parents=True, exist_ok=True)
    (out / MANIFEST).write_text(
        json.dumps(sample, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    _write_questions(out / QUESTIONS, args.framework, sample["qids"])
    # 겹쳐서 재사용하는 행은 서식에 넣지 않는다 — 같은 문항을 두 번 매기게 하지 않는다.
    form_rows = [r for r in sample["rows"] if not r.get("reused_from_official_sample")]
    _write_blank_labels(out / BLANK_LABELS, form_rows)

    seed_text = "전수" if sample["seed"] is None else f"씨값 {sample['seed']}"
    reused = sample["total_rows"] - len(form_rows)
    print(f"[{sample['set_id']}] {sample['per_stage']}문항 × {len(sample['stages'])}단계 "
          f"= {sample['total_rows']}행 ({seed_text}), 새로 매길 행 {len(form_rows)}"
          + (f" · 재사용 {reused}" if reused else "") + f" → {out}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
