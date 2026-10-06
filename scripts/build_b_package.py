#!/usr/bin/env python3
"""B 전달용 패키지 ZIP 빌더 — 정답이 섞이지 않는지 기계로 확인한다.

두 단계를 **구분**한다.

- `pre_review` — B의 독립 라벨링 **전에** 보내는 것. 공통 형식 초안, 합성 예시,
  표본 목록·추출 정보, 빈 라벨 양식, 라벨링 가이드만 들어간다.
  **정답 라벨·실제 기대값·실제 시스템 응답은 들어가지 않는다.**
- `final` — 독립 라벨링과 형식 합의가 끝난 **뒤** 보내는 것. 채점기·사용법·지표 정의·
  확정 라벨이 추가된다. 준비되지 않은 항목이 있으면 **빌드를 거부한다**
  (빈 파일로 채워 최종본이라고 하지 않는다).

담는 방식은 **허용 목록**이다. 목록에 없는 파일은 들어가지 않는다. 그 위에 금지 검사를
돌려, 허용 목록이 잘못 바뀌어도 정답·키가 섞이면 실패한다.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import subprocess
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from esgenie.eval import answer_format as af

REPO = Path(__file__).resolve().parents[1]

#: 독립 라벨링 **전** 전달본. 이 목록에 정답이 들어가는 파일은 없다.
PRE_REVIEW_FILES = (
    "docs/공통답안형식_v1초안_2026-10-06.md",
    "docs/독립라벨링_표본패키지_B_2026-10-06.md",
    "data/eval/examples/README.md",
    "data/eval/examples/common_v1_esgenie_excerpt.json",
    "data/eval/examples/common_v1_control_excerpt.json",
    "data/eval/sample/bm_rba42_v1/README.md",
    "data/eval/sample/bm_rba42_v1/sample.json",
    "data/eval/sample/bm_rba42_v1/questions.csv",
    "data/eval/sample/bm_rba42_v1/labels_blank.csv",
    "data/eval/sample/bm_rba42_numeric_census/README.md",
    "data/eval/sample/bm_rba42_numeric_census/sample.json",
    "data/eval/sample/bm_rba42_numeric_census/questions.csv",
    "data/eval/sample/bm_rba42_numeric_census/labels_blank.csv",
)

#: 독립 라벨링·형식 합의 **후** 최종본에 더해지는 것.
FINAL_EXTRA_FILES = (
    "docs/응답품질채점_사용법_2026-10-06.md",
    "docs/지표정의표_2026-10-06.md",
    "esgenie/eval/__init__.py",
    "esgenie/eval/answer_format.py",
    "esgenie/eval/esgenie_adapter.py",
    "esgenie/eval/response_scoring.py",
    "scripts/eval_response_quality.py",
    "scripts/eval_label_sample.py",
)

#: 최종본에만 들어가는 확정 라벨 디렉터리. 비어 있으면 최종본을 만들지 않는다.
FINAL_LABEL_DIR = "data/eval/labels"

#: 어느 단계에서도 패키지에 들어가면 안 되는 경로 조각.
DENIED_PARTS = (
    "docs/validation/",                      # 기대값이 인용되어 있다
    "UI연결용_수치범위_출력계약",             # 기대값이 적혀 있다
    "독립성_노출기록",                        # 노출 문항이 적혀 있다
    "result.json", "pipeline.json", "run_stats.json",  # 실제 실행 결과
    "output/reviews", "output/rehearsal", "outputs/",
    ".env", ".cache", "__pycache__", ".git/",
    "한울정밀", "촬영세트",                   # 원본 증빙
)

#: 키가 섞였는지 보는 표식. 값 자체는 적지 않는다.
KEY_MARKERS = ("API_KEY=", "SECRET_KEY=", "sk-ant-", "UPSTAGE_API_KEY")


class PackageError(RuntimeError):
    """패키지를 만들 수 없을 때. 조용히 빈 파일로 채우지 않는다."""


def commit_sha() -> str:
    try:
        out = subprocess.run(["git", "-C", str(REPO), "rev-parse", "HEAD"],
                             capture_output=True, text=True, check=True)
        sha = out.stdout.strip()
    except (subprocess.CalledProcessError, OSError) as exc:
        raise PackageError(f"커밋 SHA를 읽을 수 없다: {exc}") from exc
    dirty = subprocess.run(["git", "-C", str(REPO), "status", "--porcelain"],
                           capture_output=True, text=True).stdout.strip()
    return sha + (" (작업 디렉터리에 커밋되지 않은 변경이 있다)" if dirty else "")


def sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _label_csv_is_blank(path: Path) -> list[str]:
    """빈 라벨 서식에 값이 들어 있지 않은지 본다. 채워져 있으면 정답 유출이다."""
    filled: list[str] = []
    with path.open(encoding="utf-8-sig", newline="") as fh:
        for i, row in enumerate(csv.DictReader(fh), start=2):
            extra = {k: v for k, v in row.items()
                     if k not in ("stage", "qid") and (v or "").strip()}
            if extra:
                filled.append(f"{path.name}:{i} {sorted(extra)}")
    return filled


def audit(files: list[str], stage: str) -> list[str]:
    """담기 전 금지 검사. 문제를 **목록으로** 돌려준다 (조용히 고치지 않는다)."""
    problems: list[str] = []
    for rel in files:
        path = REPO / rel
        if not path.exists():
            problems.append(f"없는 파일: {rel}")
            continue
        if path.stat().st_size == 0:
            problems.append(f"빈 파일을 담으려 한다: {rel}")
            continue
        for part in DENIED_PARTS:
            if part in rel:
                problems.append(f"금지 경로가 포함됐다: {rel} (조각 '{part}')")

        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for marker in KEY_MARKERS:
            if marker in text:
                problems.append(f"키로 보이는 표식이 들어 있다: {rel} ('{marker}')")

        if rel.endswith("labels_blank.csv"):
            problems.extend(f"빈 서식에 값이 들어 있다: {p}" for p in _label_csv_is_blank(path))
        if stage == "pre_review" and rel.startswith(FINAL_LABEL_DIR):
            problems.append(f"독립 검토 전 전달본에 정답 라벨을 담으려 한다: {rel}")
    return problems


def collect(stage: str) -> list[str]:
    """담을 파일 목록. 최종본은 준비되지 않으면 거부한다."""
    if stage == "pre_review":
        return list(PRE_REVIEW_FILES)

    missing: list[str] = []
    label_dir = REPO / FINAL_LABEL_DIR
    labels = sorted(p for p in label_dir.glob("*.csv")) if label_dir.is_dir() else []
    if not labels:
        missing.append(f"확정 정답 라벨이 없다 ({FINAL_LABEL_DIR}/*.csv)")
    for rel in FINAL_EXTRA_FILES:
        if not (REPO / rel).exists():
            missing.append(f"없는 파일: {rel}")

    # 형식이 합의되어 확정 버전이 적혔는지 — 초안 상태로 최종본을 만들지 않는다.
    if af.FORMAT_VERSION.endswith("-draft"):
        missing.append(f"공통 답안 형식이 아직 초안이다 (format_version={af.FORMAT_VERSION}). "
                       "B 합의 후 확정 버전을 적어야 최종본을 만들 수 있다")
    if missing:
        raise PackageError(
            "최종본을 만들 수 없다. 미완료 항목을 빈 파일로 채우지 않는다:\n  - "
            + "\n  - ".join(missing))

    return list(PRE_REVIEW_FILES) + list(FINAL_EXTRA_FILES) + [
        str(p.relative_to(REPO)) for p in labels]


def _verification_state(stage: str) -> list[tuple[str, str]]:
    """검증 상태 — 완료와 미완료를 구분해 적는다. 추정치를 적지 않는다."""
    rows = [
        ("공통 답안 형식 정의·검증기", "구현 검증 완료 (합성 입력)"),
        ("ESGenie 어댑터 (페이지 0→1 변환 1회)", "구현 검증 완료 (합성 입력)"),
        ("채점 로직 (확정 규칙만)", "구현 검증 완료 (합성 입력)"),
        ("표본 추출 (고정 씨값·전수 집합)", "구현 검증 완료"),
        ("공통 답안 형식 B 합의", "**미완료 — 합의 대기**"),
        ("정답 라벨", "**미완료 — 원본 증빙 미확보, 사람 검토 전**"),
        ("실제 채점·지표 산출", "**미실행**"),
        ("지표 분자·분모 산식", "**미정 — 임의 구현하지 않았다**"),
    ]
    if stage == "pre_review":
        rows.append(("이 패키지의 정답 포함 여부", "없음 — 금지 검사를 통과했다"))
    return rows


def build_readme(stage: str, files: list[str], sha: str) -> str:
    title = ("독립 검토 전 전달본 (정답 없음)" if stage == "pre_review"
             else "독립 검토 완료 후 최종본")
    lines = [
        f"# ESGenie 평가 도구 — B 전달 패키지 ({title})",
        "",
        f"- 패키지 단계: `{stage}`",
        f"- 공통 답안 형식 버전: `{af.FORMAT_VERSION}`"
        + (" — **초안 / 합의 대기**" if af.FORMAT_VERSION.endswith("-draft") else ""),
        f"- 기준 커밋 SHA: `{sha}`",
        f"- 만든 시각: {datetime.now(timezone.utc).isoformat(timespec='seconds')}",
        f"- 양식: `rba42` (실사 응답서 48문항). 현대차 HMC 47문항은 섞지 않았다.",
        "",
    ]
    if stage == "pre_review":
        lines += [
            "## 먼저 읽어 주세요 — 독립성",
            "",
            "이 패키지에는 **정답이 없다.** A의 정답 라벨, ESGenie의 실제 응답, 기대값이",
            "적힌 검증·출력계약 문서는 들어 있지 않다. 들어 있지 않은지 기계 검사로",
            "확인했다(`scripts/build_b_package.py`의 금지 검사).",
            "",
            "- 독립 라벨을 제출하기 전에는 ESGenie 응답과 A의 라벨을 열지 말아 주세요.",
            "- PR #72 / main 저장소를 탐색하면 기대값이 적힌 문서를 보게 된다. 라벨 제출",
            "  전에는 `docs/validation/...`과 `docs/UI연결용_수치범위_출력계약_...`,",
            "  PR #71 본문을 열지 말아 주세요.",
            "- **안내만으로 독립성이 확보됐다고 보지 않는다.** 라벨 제출 시 열람 확인",
            "  3문항(가이드 §5)을 함께 적어 주시면, 노출된 문항은 문항 단위로 독립 검토",
            "  인정 여부를 따로 판단한다. 본 것을 적어도 라벨이 무효가 되지는 않는다.",
            "",
            "## 하실 일",
            "",
            "1. `docs/공통답안형식_v1초안_2026-10-06.md` 검토 → §9의 인터페이스 5개 항목 회신",
            "2. `data/eval/sample/*/labels_blank.csv` 작성 (가이드 §3의 열 설명 참고)",
            "   - 원본 증빙이 확보된 뒤에 작성한다. 현재 A도 원본을 받지 못했다.",
            "3. 열람 확인 3문항과 함께 제출",
            "",
            "## 들어 있지 않은 것 (의도적)",
            "",
            "| 빠진 것 | 이유 |",
            "|---|---|",
            "| 정답 라벨 | 아직 아무도 매기지 못했다(원본 증빙 미확보). 독립 라벨링 전에는",
            "전달하지 않는다 |",
            "| ESGenie 실제 응답 | 독립 라벨링 전에는 전달하지 않는다 |",
            "| 검증 문서·출력계약 문서 | ESGenie 기대값이 인용되어 있다 |",
            "| 채점기 코드·사용법·지표 정의 | 라벨링에 필요하지 않다. 최종본에서 전달한다 |",
            "| API 키·캐시·실행 기록 | 전달하지 않는다 |",
            "",
        ]
    else:
        lines += [
            "## 실행 방법",
            "",
            "```bash",
            "# 1) 공통 형식 문서 검증",
            "python -c \"from esgenie.eval import answer_format as af; "
            "print(af.validate_document(af.load_document('<문서.json>')) or 'OK')\"",
            "",
            "# 2) ESGenie result.json → 공통 형식",
            "python -m esgenie.eval.esgenie_adapter --result <result.json> --stage initial \\",
            "  --run-id <실행ID> --data-source <자료출처> --out <공통형식.json>",
            "",
            "# 3) 채점",
            "python scripts/eval_response_quality.py --labels <라벨.csv> \\",
            "  --answers <공통형식.json> --out <결과.json>",
            "```",
            "",
            "자세한 사용법은 `docs/응답품질채점_사용법_2026-10-06.md`,",
            "지표 정의는 `docs/지표정의표_2026-10-06.md`.",
            "",
        ]

    lines += ["## 파일 목록", "", "| 파일 | 크기(바이트) | sha256 |", "|---|---|---|"]
    for rel in files:
        path = REPO / rel
        lines.append(f"| `{rel}` | {path.stat().st_size} | `{sha256_of(path)}` |")

    lines += ["", "## 검증 상태", "", "| 항목 | 상태 |", "|---|---|"]
    lines += [f"| {name} | {state} |" for name, state in _verification_state(stage)]
    lines += [
        "",
        "테스트 재현:",
        "",
        "```bash",
        "python -m pytest tests/test_eval_answer_format.py tests/test_eval_label_sample.py \\",
        "  tests/test_eval_response_scoring.py tests/test_build_b_package.py -q",
        "```",
        "",
        "**\"채점기 납품 전체 완료\"가 아니다.** 지표 산식이 미정이고 실제 측정이 남아 있다.",
        "",
    ]
    return "\n".join(lines) + "\n"


def build(stage: str, out_path: Path, overwrite: bool = False) -> dict:
    if out_path.exists() and not overwrite:
        raise PackageError(f"이미 있다: {out_path} (덮어쓰려면 --overwrite)")

    files = collect(stage)
    problems = audit(files, stage)
    if problems:
        raise PackageError("금지 검사에서 문제가 나왔다. ZIP을 만들지 않는다:\n  - "
                           + "\n  - ".join(problems))

    sha = commit_sha()
    readme = build_readme(stage, files, sha)
    root = f"esgenie_eval_b_{stage}"

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(f"{root}/README.md", readme)
        for rel in files:
            zf.write(REPO / rel, f"{root}/{rel}")

    # 만든 뒤 실제로 열어 확인한다 — 목록만 믿지 않는다.
    with zipfile.ZipFile(out_path) as zf:
        bad = zf.testzip()
        if bad is not None:
            raise PackageError(f"ZIP이 손상됐다: {bad}")
        names = zf.namelist()
        expected = {f"{root}/README.md"} | {f"{root}/{rel}" for rel in files}
        if set(names) != expected:
            raise PackageError(f"ZIP 내용이 목록과 다르다: "
                               f"누락 {sorted(expected - set(names))}, "
                               f"초과 {sorted(set(names) - expected)}")
        for rel in files:
            inside = hashlib.sha256(zf.read(f"{root}/{rel}")).hexdigest()
            if inside != sha256_of(REPO / rel):
                raise PackageError(f"ZIP 안의 내용이 원본과 다르다: {rel}")
        for name in names:
            if name.endswith("labels_blank.csv"):
                rows = list(csv.DictReader(io.StringIO(zf.read(name).decode("utf-8-sig"))))
                for i, row in enumerate(rows, start=2):
                    if any((v or "").strip() for k, v in row.items()
                           if k not in ("stage", "qid")):
                        raise PackageError(f"ZIP 안의 빈 서식에 값이 있다: {name}:{i}")

    return {"stage": stage, "zip": str(out_path), "root": root,
            "commit": sha, "format_version": af.FORMAT_VERSION,
            "file_count": len(files) + 1,
            "zip_sha256": sha256_of(out_path),
            "files": [{"path": rel, "sha256": sha256_of(REPO / rel)} for rel in files]}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="B 전달용 패키지 ZIP을 만든다")
    parser.add_argument("--stage", choices=("pre_review", "final"), default="pre_review")
    parser.add_argument("--out", required=True, help="만들 ZIP 경로")
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--manifest", help="만든 내역을 JSON으로 쓸 경로")
    args = parser.parse_args(argv)

    try:
        result = build(args.stage, Path(args.out), args.overwrite)
    except PackageError as exc:
        print(f"패키지 오류: {exc}", file=sys.stderr)
        return 2

    if args.manifest:
        Path(args.manifest).write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"[{result['stage']}] 파일 {result['file_count']}개 → {result['zip']}")
    print(f"zip sha256: {result['zip_sha256']}")
    print(f"기준 커밋: {result['commit']}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
