"""검토자의 재현 스크립트를 수정 후 코드로 다시 돌려 '결함이 사라졌음'을 기록한다.

검토자 스크립트(outputs/reviews/hmc-integrity-second-review-20260921/reproduce.py)의
assert는 **결함이 존재함**을 고정한다. 수정 후에는 그 assert가 깨지는 것이 정상이다.
그래서 여기서는

  1. 같은 스크립트 원문을 출력 폴더만 이 회차 폴더로 바꿔 실행한다(검토자 기록 보존).
     -O로 실행되면 결함 고정 assert가 비활성화되어 관측값 전체가 JSON으로 남는다.
  2. 관측값을 **정상 동작 기준**으로 다시 검증한다(아래 EXPECTED).

  venv/bin/python -O docs/validation/hmc-response-integrity-followup2-20260921/run_review_probes.py
"""
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
REVIEW = Path('/Users/heojeongmin/Documents/Claude/Projects/ESGenie'
              '/outputs/reviews/hmc-integrity-second-review-20260921/reproduce.py')

source = REVIEW.read_text().replace(
    "OUT = Path(__file__).resolve().parent", f"OUT = Path({str(HERE)!r})")
assert "OUT = Path(" + repr(str(HERE)) in source, "출력 폴더 치환 실패 — 검토자 기록을 덮어쓸 위험"
os.environ.setdefault('ESGENIE_FORCE_MOCK', '1')
scope: dict = {}
exec(compile(source, str(REVIEW), 'exec'), scope)      # noqa: S102 — 검토자 원문 그대로 실행
results = json.loads((HERE / 'reproductions.json').read_text())

# 수정 후 정상 기준. 좌변은 관측 경로, 우변은 기대값.
checks = {
    # R1 — 별도 발행 문서 2건이 파생 배출량에서 두 번 더해지지 않는다(단일 문서와 동일).
    'R1_single_E31': (results['R1_single_control']['points']['E-3-1']['value'], .048),
    'R1_equal_E41': (results['R1_equal']['points']['E-4-1']['value'], .00036),
    'R1_equal_E31': (results['R1_equal']['points']['E-3-1']['value'], .048),
    'R1_conflict_E41_comparison': (results['R1_conflicting']['points']['E-4-1']['comparison'], 'mismatch'),
    'R1_conflict_E31': (results['R1_conflicting']['points']['E-3-1']['value'], .048),
    # 원측정값의 상충이 파생 배출량에 남는다.
    'R1_conflict_E31_comparison': (results['R1_conflicting']['points']['E-3-1']['comparison'], 'mismatch'),
    'R1_conflict_E31_verification': (results['R1_conflicting']['points']['E-3-1']['verification'], 'unverified'),
    'R1_conflict_E31_note': (any('원측정값' in n for n in
                                 results['R1_conflicting']['points']['E-3-1']['scope_notes']), True),
    # R2 — 라인 총량이 공장 전체 자료를 흡수하지 않는다. 3 TJ 대체도 13 TJ 합산도 금지.
    'R2_factory_control': (results['R2_factory_control']['points']['E-4-1']['value'], 10),
    'R2_narrow_total': (results['R2_narrow_total']['points']['E-4-1']['value'], 10),
    'R2_reversed': (results['R2_reversed']['points']['E-4-1']['value'], 10),
    'R2_narrow_total_note': (any('포괄 관계 미확인' in n for n in
                                 results['R2_narrow_total']['points']['E-4-1']['scope_notes']), True),
    # R3 — 같은 인증의 실제 취득만 근거가 된다.
    # 검토자 스크립트의 사례 순서: 0 부정, 1 다른 인증, 2 준비 중, 3 실제 취득.
    'R3_negated': (results['R3_certification'][0]['draft_status'] != 'draft_ready', True),
    'R3_other_certificate': (results['R3_certification'][1]['draft_status'] != 'draft_ready', True),
    'R3_preparation': (results['R3_certification'][2]['draft_status'] != 'draft_ready', True),
    'R3_real_acquisition': (results['R3_certification'][3]['draft_status'] == 'draft_ready'
                            and not results['R3_certification'][3]['gate']['soft_flags'], True),
    # R4 — ±10 TJ은 오차 0%·대조 완료가 아니다.
    'R4_difference_pct': (results['R4_opposite_sign']['edges'][0]['difference_pct'], 200.0),
    'R4_comparison': (results['R4_opposite_sign']['edges'][0]['comparison'], 'mismatch'),
    'R4_point_comparison': (results['R4_opposite_sign']['points']['E-4-1']['comparison'], 'mismatch'),
    'R4_point_verification': (results['R4_opposite_sign']['points']['E-4-1']['verification'], 'unverified'),
}
verdict = {k: (observed == expected, observed, expected) for k, (observed, expected) in checks.items()}
(HERE / 'after_fix_checks.json').write_text(json.dumps(
    {'head': results['head'], 'reproductions': str(HERE / 'reproductions.json'),
     'checks': {k: {'pass': ok, 'observed': o, 'expected': e} for k, (ok, o, e) in verdict.items()},
     'failed': [k for k, (ok, _, _) in verdict.items() if not ok]}, ensure_ascii=False, indent=2))
print(json.dumps({k: v[0] for k, v in verdict.items()}, ensure_ascii=False, indent=2))
if [k for k, (ok, _, _) in verdict.items() if not ok]:
    sys.exit(f"수정 후 기준 미충족: {[k for k, (ok, _, _) in verdict.items() if not ok]}")
