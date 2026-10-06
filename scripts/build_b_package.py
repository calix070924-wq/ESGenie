#!/usr/bin/env python3
"""B 전달용 패키지 ZIP 빌더 — 정답이 섞이지 않는지 기계로 확인한다.

세 단계를 **구분**한다.

- `pre_review` — B의 독립 라벨링 **전에** 보내는 것. 공통 형식 초안, 합성 예시,
  표본 목록·추출 정보, 빈 라벨 양식, 라벨링 가이드만 들어간다.
  **정답 라벨·실제 기대값·실제 시스템 응답은 들어가지 않는다.**
- `tools` — 채점기와 사용법·지표 정의·**순수 합성** 입력 예시. 라벨 확정을 기다리지
  않고 보낸다(B가 실행 준비를 할 수 있어야 한다). 실제 라벨·실행 결과·실제 기대값이
  있는 테스트나 문서는 들어가지 않는다.
- `final` — 독립 라벨링과 형식 합의가 끝난 **뒤** 보내는 것. 도구 일체에 라벨 작성
  스크립트와 확정 라벨이 추가된다. 준비되지 않은 항목이 있으면 **빌드를 거부한다**
  (빈 파일로 채워 최종본이라고 하지 않는다).

**이미 전달한 ZIP은 덮어쓰지 않는다.** 같은 경로가 있으면 빌드를 거부하며(`--overwrite`를
주지 않는 한), 수정이 필요하면 새 버전 이름으로 만들고 전달 기록
(`docs/B전달기록_2026-10-06.md`)에 해시와 변경 내용을 적는다.

담는 방식은 **허용 목록**이다. 목록에 없는 파일은 들어가지 않는다. 그 위에 금지 검사를
돌려, 허용 목록이 잘못 바뀌어도 정답·키가 섞이면 실패한다.

**금지 검사가 잡지 못하는 것:** 허용된 문서 **본문에 인용된** 실제 기대값이다. 검사는
경로·키 표식·빈 서식만 본다. 실제 수치를 이 스크립트에 적어 두고 비교하면 그 수치가
저장소에 남으므로 그렇게 하지 않는다. 따라서 **전달 전에 사람이 압축 내용을 읽고
기대값 인용이 없는지 확인한다.** (실제로 이 경로로 한 건 — 형식 문서 §5의 근거 예시에
실제 증빙 문구가 들어가 있던 것 — 을 전달 전에 찾아 합성 자리값으로 바꿨다.)
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

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from esgenie.eval import answer_format as af     # noqa: E402

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

#: 채점기 도구 패키지 — **정답 라벨과 무관하게** 먼저 보낸다. B가 실행 준비를 할 수
#: 있어야 하므로 라벨 확정을 기다리지 않는다. 라벨·실행 결과·실제 기대값은 없다.
TOOLS_FILES = (
    "docs/공통답안형식_v1초안_2026-10-06.md",
    "docs/응답품질채점_사용법_2026-10-06.md",
    "docs/지표정의표_2026-10-06.md",
    "docs/지표결정요청표_2026-10-06.md",
    "esgenie/__init__.py",
    "esgenie/eval/__init__.py",
    "esgenie/eval/answer_format.py",
    "esgenie/eval/esgenie_adapter.py",
    "esgenie/eval/response_scoring.py",
    "esgenie/eval/independence.py",
    "scripts/eval_response_quality.py",
    "scripts/eval_independence_report.py",
    "data/eval/framework/rba42_qids.txt",
    "data/eval/examples/README.md",
    "data/eval/examples/common_v1_esgenie_excerpt.json",
    "data/eval/examples/common_v1_control_excerpt.json",
    "data/eval/examples/synthetic_run/README.md",
    "data/eval/examples/synthetic_run/qids_SYN.txt",
    "data/eval/examples/synthetic_run/labels_SYN.csv",
    "data/eval/examples/synthetic_run/answers_SYN_initial.json",
)

#: 독립 라벨링·형식 합의 **후** 최종본에 더해지는 것.
FINAL_EXTRA_FILES = TOOLS_FILES + ("scripts/eval_label_sample.py",)

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


def _unmarked_quotes(path: Path) -> list[str]:
    """합성 예시의 근거 인용문이 합성임을 표시하는지 본다.

    실제 증빙 문구가 예시 자리에 들어가면 그것만으로 정답 힌트가 된다. 기대값을 목록으로
    적어 비교하지 않고(그러면 수치가 저장소에 남는다), **인용문에 합성 표시가 있는지**만
    본다. `null`은 미상이라 통과한다.
    """
    marks = ("예시", "합성", "SYN", "자리값")
    bad: list[str] = []

    def walk(node: object, where: str) -> None:
        if isinstance(node, dict):
            for key, val in node.items():
                if key == "quote" and isinstance(val, str) and not any(m in val for m in marks):
                    bad.append(f"{where}.quote={val[:30]!r}")
                else:
                    walk(val, f"{where}.{key}")
        elif isinstance(node, list):
            for i, val in enumerate(node):
                walk(val, f"{where}[{i}]")

    try:
        walk(json.loads(path.read_text(encoding="utf-8")), path.name)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        bad.append(f"{path.name} 를 읽을 수 없다: {exc}")
    return bad


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

        if rel.endswith("_excerpt.json") or "/synthetic_run/" in rel and rel.endswith(".json"):
            problems.extend(f"합성 표시가 없는 근거 인용문: {rel} — {p}"
                            for p in _unmarked_quotes(path))
        if rel.endswith("labels_blank.csv"):
            problems.extend(f"빈 서식에 값이 들어 있다: {p}" for p in _label_csv_is_blank(path))
        if stage in ("pre_review", "tools") and rel.startswith(FINAL_LABEL_DIR):
            problems.append(f"정답 없는 전달본({stage})에 정답 라벨을 담으려 한다: {rel}")
        if stage == "tools" and rel.endswith(".csv") and "/examples/" not in rel:
            # 합성 예시 밖의 라벨 CSV는 실제 라벨일 수 있다. 도구 패키지에 넣지 않는다.
            problems.append(f"도구 패키지에 합성 예시가 아닌 CSV를 담으려 한다: {rel}")
    return problems


def _dedupe(*groups: tuple[str, ...] | list[str]) -> list[str]:
    """순서를 지키면서 중복을 없앤다 — 같은 파일을 두 번 담지 않는다."""
    seen: set[str] = set()
    out: list[str] = []
    for group in groups:
        for rel in group:
            if rel not in seen:
                seen.add(rel)
                out.append(rel)
    return out


def collect(stage: str) -> list[str]:
    """담을 파일 목록. 최종본은 준비되지 않으면 거부한다."""
    if stage == "pre_review":
        return list(PRE_REVIEW_FILES)
    if stage == "tools":
        missing = [rel for rel in TOOLS_FILES if not (REPO / rel).exists()]
        if missing:
            raise PackageError("도구 패키지를 만들 수 없다. 빈 파일로 채우지 않는다:\n  - "
                               + "\n  - ".join(missing))
        return list(TOOLS_FILES)

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

    return _dedupe(PRE_REVIEW_FILES, FINAL_EXTRA_FILES,
                   [str(p.relative_to(REPO)) for p in labels])


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
    if stage in ("pre_review", "tools"):
        rows.append(("이 패키지의 정답 포함 여부", "없음 — 금지 검사를 통과했다"))
    if stage == "tools":
        rows.insert(4, ("합성 입력으로 CLI 실행", "확인 완료 — 종료 코드 0, 행 4건 분류"))
        rows.insert(5, ("실제 평가 완료 여부", "**아니다 — 합성 실행 성공은 평가가 아니다**"))
        rows.append(("독립 검토 인정 제외 규칙", "구현 검증 완료 (확정 규칙)"))
    return rows


def build_readme(stage: str, files: list[str], sha: str) -> str:
    title = {"pre_review": "독립 검토 전 전달본 (정답 없음)",
             "tools": "채점 도구 패키지 (정답 없음)",
             "final": "독립 검토 완료 후 최종본"}[stage]
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
    elif stage == "tools":
        bare = sha.split()[0]
        lines += [
            "## 이 패키지가 무엇이고 무엇이 아닌가",
            "",
            "- **무엇인가:** 채점기 CLI와 그 코드, 사용법, 지표 정의표, 아직 결정되지 않은",
            "  산식 목록, 그리고 **순수 합성** 입력 예시 한 세트. B가 라벨 확정을 기다리지",
            "  않고 실행 준비를 할 수 있도록 먼저 보낸다.",
            "- **무엇이 아닌가:** 정답 라벨도, 실제 ESGenie/대조군 응답도, 실제 기대값이",
            "  인용된 문서도 들어 있지 않다. 들어 있지 않은지 기계 검사로 확인했다.",
            "- **합성 예시가 통과한 것은 평가가 아니다.** 실제 평가는 원본 증빙 확보 →",
            "  독립 라벨링 → 산식 확정 뒤의 일이다.",
            "",
            "## 실행 방법 (이 압축을 푼 폴더에서 그대로)",
            "",
            "저장소 설치도, `PYTHONPATH` 설정도 필요 없다. 압축을 푼 뒤 생긴",
            f"`esgenie_eval_b_{stage}/` 안에서 실행한다.",
            "",
            "```bash",
            "# 0) 합성 예시로 채점기가 도는지 확인 (종료 코드 0)",
            "python scripts/eval_response_quality.py \\",
            "  --labels data/eval/examples/synthetic_run/labels_SYN.csv \\",
            "  --answers data/eval/examples/synthetic_run/answers_SYN_initial.json \\",
            "  --expected-qids data/eval/examples/synthetic_run/qids_SYN.txt \\",
            "  --out /tmp/eval_syn.json",
            "",
            "# 1) 공통 형식 문서 검증 (오류 목록 또는 OK)",
            "python -c \"from esgenie.eval import answer_format as af; "
            "print(af.validate_document(af.load_document("
            "'data/eval/examples/common_v1_control_excerpt.json')) or 'OK')\"",
            "",
            "# 2) ESGenie result.json → 공통 형식 (실제 result.json이 있을 때)",
            "python -m esgenie.eval.esgenie_adapter --result <result.json> --stage initial \\",
            "  --run-id <실행ID> --data-source <자료출처> --out <공통형식.json>",
            "",
            "# 3) 실사 응답서 48문항으로 채점 (양식 문항 목록을 파일로 준다)",
            "python scripts/eval_response_quality.py --labels <라벨.csv> \\",
            "  --answers <공통형식.json> \\",
            "  --expected-qids data/eval/framework/rba42_qids.txt --out <결과.json>",
            "```",
            "",
            "기대 출력과 행별 해석은 `data/eval/examples/synthetic_run/README.md`에 적어",
            "두었다. 사용법은 `docs/응답품질채점_사용법_2026-10-06.md`.",
            "",
            "## 의존성",
            "",
            "| 항목 | 필요한 것 |",
            "|---|---|",
            "| 위 0~3번 경로 | **표준 라이브러리만.** 설치할 패키지가 없다 |",
            "| Python | 3.10 이상을 가정한다. 실제 확인은 3.14.6에서 했고 그 아래 버전은 "
            "확인하지 않았다 |",
            "| `--framework rba42` (파일 대신 제품 양식에서 문항을 읽는 방식) | 저장소 전체와"
            " `openpyxl`·`reportlab`이 필요하다. 그래서 `--expected-qids`를 넣었다 |",
            "| 테스트 재현 | 저장소 체크아웃이 필요하다 (아래) |",
            "",
            "## 테스트를 직접 돌려 보려면 — 저장소 체크아웃",
            "",
            "테스트 파일은 이 압축에 넣지 않았다(실제 기대값이 인용된 기록과 같은 트리에",
            "있다). 직접 재현하려면 저장소를 받아야 한다.",
            "",
            "```bash",
            "git clone <ESGenie 저장소> esgenie && cd esgenie",
            f"git checkout {bare}",
            "python -m venv .venv && . .venv/bin/activate",
            "pip install -r requirements.txt    # 테스트에는 제품 의존성이 필요하다",
            "python -m pytest tests/test_eval_answer_format.py \\",
            "  tests/test_eval_response_scoring.py tests/test_eval_label_sample.py \\",
            "  tests/test_eval_independence.py tests/test_build_b_package.py -q",
            "```",
            "",
            f"기준 커밋은 `{bare}` 이고, 이 패키지의 코드 파일 해시는 아래 파일 목록과",
            "같아야 한다. 다르면 같은 커밋이 아니다.",
            "",
            "## 현재 한계 — 할 수 있는 것과 아직 못 하는 것",
            "",
            "| 구분 | 내용 |",
            "|---|---|",
            "| **지금 할 수 있다** | 공통 형식 문서의 형식·필수값·행 식별자 검증 |",
            "| **지금 할 수 있다** | 라벨과 응답의 **행별 대조**: 값 일치/불일치, 근거 일치/"
            "불일치, 보류 타당성, 미해결 사유 |",
            "| **지금 할 수 있다** | 분류 결과의 **건수** 집계 (`bucket_counts` 등) |",
            "| **아직 못 한다** | 비율 지표 5종(D1~D5). 분자·분모 산식이 **미정**이라 "
            "구현하지 않았다 — `docs/지표결정요청표_2026-10-06.md` |",
            "| **아직 못 한다** | 종합 점수. 합치지 않는다 |",
            "| **아직 못 한다** | 실제 평가. 원본 증빙과 정답 라벨이 없다 |",
            "| 형식 | **v1 초안 / 합의 대기.** B 회신 전까지 합의 완료가 아니다 |",
            "",
            "산출할 수 없는 지표는 결과 JSON에서 **0이 아니라** `metrics_blocked_reason`",
            "으로 나온다. 0%로 적지 않는다.",
            "",
            "## 회신을 부탁하는 것",
            "",
            "1. `docs/공통답안형식_v1초안_2026-10-06.md` §9의 인터페이스 5개 항목",
            "2. `docs/지표결정요청표_2026-10-06.md` D1~D5에 대한 의견",
            "3. 위 0번 합성 실행이 B 환경에서도 종료 코드 0으로 끝나는지",
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
    if stage != "tools":   # tools 는 위에 체크아웃 절차와 함께 적었다
        lines += [
            "",
            "테스트 재현:",
            "",
            "```bash",
            "python -m pytest tests/test_eval_answer_format.py "
            "tests/test_eval_label_sample.py \\",
            "  tests/test_eval_response_scoring.py tests/test_eval_independence.py \\",
            "  tests/test_build_b_package.py -q",
            "```",
        ]
    lines += [
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
    parser.add_argument("--stage", choices=("pre_review", "tools", "final"),
                        default="pre_review")
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
