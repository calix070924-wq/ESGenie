"""§5(C1·C2·C3) 고정 입력의 0 판정을 지정한 코드 경로에서 실행해 기록한다(네트워크 차단, 모델 호출 없음).

사용: python probe_scope_cases.py <code_path> <out.json>
기대값은 이 파일의 EXPECTED에 원문 기준으로 먼저 적었다. 검사 대상 함수의 반환값을 정답으로 쓰지 않는다.
"""
from __future__ import annotations

import json
import socket
import sys
from dataclasses import asdict
from pathlib import Path

CODE, OUT = Path(sys.argv[1]).resolve(), Path(sys.argv[2])
sys.path.insert(0, str(CODE))


def _blocked(*a, **k):
    raise RuntimeError("network forbidden")


socket.socket.connect = _blocked
socket.create_connection = _blocked

import esgenie  # noqa: E402
assert CODE in Path(esgenie.__file__).resolve().parents, esgenie.__file__
from esgenie.ssot import ocr_router as R  # noqa: E402

KIMHAE = "김해 제1공장"
ACC = "산업재해 발생 건수 0건"

# (id, quote, request_period, request_site, 기대 상태, 기대 조건 설명)
# 기대 상태가 집합이면 그 가운데 하나. 'not_period' 등은 별도 검사 키다.
CASES = [
    # ── C1 규격 개정 연도 ─────────────────────────────────────────────────
    ("C1-a", f"GRI 403:2018 안전 현황\n{ACC}", "2026-04", KIMHAE,
     {"SOURCE_ONLY"}, {"period_not": "2018"}),
    ("C1-b", f"GRI 403: 2018 안전 현황\n{ACC}", "2026-04", KIMHAE,
     {"SOURCE_ONLY"}, {"period_not": "2018"}),
    ("C1-c", f"GRI 403 :   2018 안전 현황\n{ACC}", "2026-04", KIMHAE,
     {"SOURCE_ONLY"}, {"period_not": "2018"}),
    ("C1-d", f"GRI 403：2018 안전 현황\n{ACC}", "2026-04", KIMHAE,
     {"SOURCE_ONLY"}, {"period_not": "2018"}),
    ("C1-e", f"GRI 403 ： 2018 안전 현황\n{ACC}", "2026-04", KIMHAE,
     {"SOURCE_ONLY"}, {"period_not": "2018"}),
    ("C1-f", f"ISO 45001:2018 안전 현황\n{ACC}", "2026-04", KIMHAE,
     {"SOURCE_ONLY"}, {"period_not": "2018"}),
    ("C1-g", f"ISO 45001: 2018 안전 현황\n{ACC}", "2026-04", KIMHAE,
     {"SOURCE_ONLY"}, {"period_not": "2018"}),
    # 같은 제목에 실제 기간이 함께 있으면 실제 기간은 남는다
    ("C1-h", f"GRI 403: 2018 2026년 4월 김해 제1공장 안전 현황\n{ACC}", "2026-04", KIMHAE,
     {"SOURCE_ONLY", "CONFIRMED"}, {"period_has": "2026", "period_not": "2018"}),
    # 서술 안의 규격 인용(제목 아님)
    ("C1-i", "2026년 4월 김해 제1공장 산업재해 발생 건수 0건(GRI 403: 2018 기준)", "2026-04", KIMHAE,
     {"CONFIRMED"}, {"period_not": "2018"}),
    ("C1-j", "2026년 4월 김해 제1공장 산업재해 발생 건수 0건(GRI 403:2018 기준)", "2026-04", KIMHAE,
     {"CONFIRMED"}, {"period_not": "2018"}),
    ("C1-k", "2026년 4월 김해 제1공장 산업재해 발생 건수 0건(ISO 45001:2018 기준)", "2026-04", KIMHAE,
     {"CONFIRMED"}, {"period_not": "2018"}),
    # 대조: 실제 2018년 표기는 기간이다(규격 번호가 아님)
    ("C1-ctl-year", "2018년 김해 제1공장 산업재해 발생 건수 0건", "2026-04", KIMHAE,
     {"REJECTED"}, {"cause": "period_mismatch"}),
    # 대조: 규격 번호 뒤 수량은 수량이다
    ("C1-ctl-qty", f"2026년 4월 김해 제1공장 안전 현황\nISO 45001 부적합 3건\n{ACC}", "2026-04", KIMHAE,
     {"CONFIRMED"}, {}),
    # ── C2 FY·분기 ─────────────────────────────────────────────────────
    ("C2-a", f"FY2026 김해 제1공장 안전 현황\n{ACC}", "2026-04", KIMHAE,
     {"SOURCE_ONLY"}, {"site_has": "김해", "cause_not": "period_not_stated"}),
    ("C2-b", f"FY 2026 김해 제1공장 안전 현황\n{ACC}", "2026-04", KIMHAE,
     {"SOURCE_ONLY"}, {"site_has": "김해", "cause_not": "period_not_stated"}),
    # 문서에 회계연도 정의가 있으면 그 구간 — 연간 값으로 4월을 추정하지 않는다
    ("C2-c", f"FY2026 = 2026-01-01~2026-12-31\nFY2026 김해 제1공장 안전 현황\n{ACC}", "2026-04", KIMHAE,
     {"REJECTED"}, {"site_has": "김해", "period_has": "2026"}),
    ("C2-c2", f"FY2026 = 2026-01-01~2026-12-31\nFY2026 김해 제1공장 안전 현황\n{ACC}", "2026-01~2026-12", KIMHAE,
     {"CONFIRMED"}, {"site_has": "김해", "period_has": "2026"}),
    ("C2-d", f"2026년 1분기(1~3월) 김해 제1공장 안전 현황\n{ACC}", "2026-04", KIMHAE,
     {"REJECTED"}, {"cause": "period_mismatch"}),
    ("C2-d2", f"2026년 1분기(1~3월) 김해 제1공장 안전 현황\n{ACC}", "2026-01~2026-03", KIMHAE,
     {"CONFIRMED"}, {}),
    ("C2-e", f"Q1 김해 제1공장 안전 현황\n{ACC}", "2026-04", KIMHAE,
     {"SOURCE_ONLY"}, {"site_has": "김해", "cause_not": "period_not_stated"}),
    ("C2-e2", f"Q1 김해 제1공장 안전 현황\n{ACC}", "2026-01~2026-03", KIMHAE,
     {"SOURCE_ONLY"}, {"site_has": "김해", "cause_not": "period_not_stated"}),
    ("C2-f", f"FY26 김해 제1공장 안전 현황\n{ACC}", "2026-04", KIMHAE,
     {"SOURCE_ONLY"}, {"site_has": "김해", "cause_not": "period_not_stated"}),
    ("C2-f2", f"FY26 김해 제1공장 안전 현황\n{ACC}", "2026", KIMHAE,
     {"SOURCE_ONLY"}, {"site_has": "김해", "cause_not": "period_not_stated"}),
    # 상위 머리말의 4월 범위를 FY 제목 아래로 빌려 오지 않는다
    ("C2-g", f"2026년 4월 김해 제1공장 안전 현황\nFY2026 김해 제1공장 안전 현황\n{ACC}", "2026-04", KIMHAE,
     {"SOURCE_ONLY"}, {"site_has": "김해", "cause_not": "period_not_stated"}),
    # ── C3 규격 제목 + 사업장 ─────────────────────────────────────────
    ("C3-a", f"2026년 4월 김해 제1공장 안전 현황\nGRI 403-9 A2공장 안전 현황\n{ACC}", "2026-04", KIMHAE,
     {"REJECTED"}, {"cause": "site_mismatch", "site_has": "A2"}),
    ("C3-a-neg", "2026년 4월 김해 제1공장 안전 현황\nGRI 403-9 A2공장 안전 현황\n산업재해가 발생하지 않았다.",
     "2026-04", KIMHAE, {"REJECTED"}, {"cause": "site_mismatch", "site_has": "A2"}),
    # 같은 요청 사업장 + 명시적 실제 기간을 가진 직접 실적 제목
    ("C3-b", f"GRI 403-9 2026년 4월 김해 제1공장 안전 현황\n{ACC}", "2026-04", KIMHAE,
     {"CONFIRMED"}, {"site_has": "김해", "period_has": "2026"}),
    # 다른 사업장의 직접 실적 제목
    ("C3-c", f"GRI 403-9 2026년 4월 부산 제2공장 안전 현황\n{ACC}", "2026-04", KIMHAE,
     {"REJECTED"}, {"cause": "site_mismatch", "site_has": "부산"}),
    # 단순 규격 인용(범위 없음) — 앞 절 차용 금지, 확정 금지
    ("C3-d", f"2026년 4월 김해 제1공장 안전 현황\nGRI 403-9 산업재해\n{ACC}", "2026-04", KIMHAE,
     {"SOURCE_ONLY"}, {"site_not": "김해"}),
    ("C3-e", f"2026년 4월 김해 제1공장 안전 현황\nISO 45001:2018 안전 현황\n{ACC}", "2026-04", KIMHAE,
     {"SOURCE_ONLY"}, {"period_not": "2018"}),
    # 목차형 규격 인용 — 적용 관계 불명확
    ("C3-f", f"2026년 4월 김해 제1공장 안전 현황\nGRI 403-9 산업재해 ··· 45\n{ACC}", "2026-04", KIMHAE,
     {"SOURCE_ONLY", "UNRESOLVED"}, {"site_not": "김해"}),
    # 상위 문맥 변경 — 규격 이름이 붙은 상위 제목(사업장 없음) 아래 0
    ("C3-g", f"2026년 4월 김해 제1공장 안전 현황\nGRI 403 산업안전보건\n{ACC}", "2026-04", KIMHAE,
     {"SOURCE_ONLY"}, {"site_not": "김해"}),
    # 규격 제목 아래 자기 사업장만 있고 기간이 없으면 위 머리말 기간을 빌리지 않는다
    ("C3-h", f"2026년 4월 김해 제1공장 안전 현황\nGRI 403-9 김해 제1공장 안전 현황\n{ACC}", "2026-04", KIMHAE,
     {"SOURCE_ONLY"}, {"site_has": "김해"}),
    # 대조: 규격 없는 다른 사업장 하위 제목(PR69 8차와 동일)
    ("C3-ctl-child", f"2026년 4월 김해 제1공장\nA2공장 안전 현황\n{ACC}", "2026-04", KIMHAE,
     {"REJECTED"}, {"cause": "site_mismatch", "site_has": "A2"}),
    ("C3-ctl-own", f"2026년 4월 김해 제1공장 안전 현황\n{ACC}", "2026-04", KIMHAE,
     {"CONFIRMED"}, {"site_has": "김해"}),
]


def check(v, status, extra):
    problems = []
    if v.status not in status:
        problems.append(f"status {v.status} ∉ {sorted(status)}")
    if "cause" in extra and v.cause != extra["cause"]:
        problems.append(f"cause {v.cause} ≠ {extra['cause']}")
    if "cause_not" in extra and v.cause == extra["cause_not"]:
        problems.append(f"cause == {extra['cause_not']}")
    if "period_not" in extra and extra["period_not"] in (v.evidence_period or ""):
        problems.append(f"period '{v.evidence_period}' contains {extra['period_not']}")
    if "period_has" in extra and extra["period_has"] not in (v.evidence_period or ""):
        problems.append(f"period '{v.evidence_period}' lacks {extra['period_has']}")
    if "site_has" in extra and extra["site_has"] not in (v.evidence_site or ""):
        problems.append(f"site '{v.evidence_site}' lacks {extra['site_has']}")
    if "site_not" in extra and extra["site_not"] in (v.evidence_site or ""):
        problems.append(f"site '{v.evidence_site}' borrowed {extra['site_not']}")
    return problems


rows = []
for cid, quote, period, site, status, extra in CASES:
    v = R._zero_verdict(quote, "산업재해 발생 건수", period, {"site": site} if site else {})
    d = {k: (str(val) if k == "source_scope" else val) for k, val in asdict(v).items()}
    problems = check(v, status, extra)
    rows.append({"id": cid, "quote": quote, "request_period": period, "request_site": site,
                 "expected_status": sorted(status), "expected_extra": extra,
                 "verdict": d, "pass": not problems, "problems": problems})
    print(f"{cid:14s} {'PASS' if not problems else 'FAIL'} {v.status}/{v.cause} "
          f"period={v.evidence_period!r} site={v.evidence_site!r} heading={v.scope_heading!r} "
          f"boundary={v.scope_boundary!r} {'; '.join(problems)}")
OUT.parent.mkdir(parents=True, exist_ok=True)
OUT.write_text(json.dumps({"code_path": str(CODE), "rows": rows,
                           "passed": sum(r["pass"] for r in rows), "total": len(rows)},
                          ensure_ascii=False, indent=2))
print(f"passed {sum(r['pass'] for r in rows)}/{len(rows)}")
