import json
import os
import runpy
import sys

os.environ['ESGENIE_FORCE_MOCK'] = '1'
os.environ['HF_HUB_OFFLINE'] = '1'
os.environ['TRANSFORMERS_OFFLINE'] = '1'
sys.path.insert(0, sys.argv[1])
ns = runpy.run_path('/private/tmp/ESGenie-ocr-numeric-20260929/tests/test_pr68_review_r1_r5.py')
R = ns['R']
R._get_openai_key = lambda: None
R._get_upstage_key = lambda: None
R._get_anthropic_key = lambda: None
cases = [
    ('empty_electric_missing_multiplier', [[
        ['사용량(kWh)', '전월지침', '당월지침'], ['-', '1,000', '1,250']]], 'kepco_bill'),
    ('empty_heat_with_only_previous', [[
        ['가스사용량(MJ)', '전월지침(m3)'], ['-', '31,580']]], 'gas_bill'),
    ('same_site_different_period', [
        [['사업장', '기간', '사용량(kWh)'], ['김해 제1공장', '2026년 4월', '1,000']],
        [['사업장', '기간', '사용량(kWh)'], ['김해 제1공장', '2026년 5월', '1,000']]], 'kepco_bill'),
    ('different_meters_same_site', [
        [['사업장', '계량기', '사용량(kWh)'], ['김해 제1공장', '전력계 A', '1,000']],
        [['사업장', '계량기', '사용량(kWh)'], ['김해 제1공장', '전력계 B', '1,000']]], 'kepco_bill'),
]
out = []
for name, rows, kind in cases:
    e = ns['_run'](rows, kind)
    out.append({'case': name, 'metrics': [vars(m) for m in e.metrics], 'meta': e.router_meta})
print(json.dumps(out, ensure_ascii=False, indent=2))
