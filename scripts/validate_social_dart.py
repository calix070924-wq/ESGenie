"""DART 실제 공시 데이터로 S 영역 수치 추출 검증 스크립트.

순서:
1. DART API로 삼성전자/현대차/POSCO홀딩스 사업보고서 원문 수집
2. 원문에서 "사회" 섹션 텍스트 추출 → data/sample_dart/{corp_code}_raw_social.txt 저장
3. S 패턴 매칭 테스트 (문장형 + DART 표 형식)
4. build_evidence_graph() 전체 파이프라인 실행
5. 결과 리포트 출력
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv()

import requests
import io
import zipfile

from esgenie.config import SETTINGS, SAMPLE_DART_DIR
from esgenie.dart_client import CompanyReport, _dart_get, _fetch_report_zip_text
from esgenie.layer0_evidence_graph import (
    _S_HEADCOUNT_PATTERN,
    _S_RATIO_PATTERN,
    _S_MONEY_PATTERN,
    _S_COUNT_PATTERN,
    _S_DART_EMP_TOTAL_PATTERN,
    _S_DART_GENDER_PATTERN,
    _S_DART_PARENTAL_RATE_PATTERN,
    _S_DART_WELFARE_PATTERN,
    _S_LABEL_TO_KESG,
    _extract_social_nodes,
    build_evidence_graph,
    EvidenceGraph,
)

DART_BASE = "https://opendart.fss.or.kr/api"

TARGETS = [
    ("00126380", "삼성전자", "005930"),
    ("00164742", "현대자동차", "005380"),
    ("00155319", "POSCO홀딩스", "005490"),
]

OUTPUT_DIR = SAMPLE_DART_DIR


def fetch_annual_report_text(corp_code: str, year: int = 2024) -> str:
    """사업보고서 원문 텍스트를 DART에서 가져온다."""
    fin = _dart_get(
        "/fnlttSinglAcnt.json",
        corp_code=corp_code,
        bsns_year=str(year),
        reprt_code="11011",
        fs_div="CFS",
    )
    rcept_no = ""
    if fin and fin.get("list"):
        rcept_no = fin["list"][0].get("rcept_no", "")

    if rcept_no:
        text = _fetch_report_zip_text(rcept_no, max_chars=1_500_000)
        if text:
            return text

    for try_year in [year + 1, year]:
        ann = _dart_get(
            "/list.json",
            corp_code=corp_code,
            bgn_de=f"{try_year}0101",
            end_de=f"{try_year}0630",
            pblntf_ty="A",
            page_count="10",
        )
        if not ann or not ann.get("list"):
            continue
        for item in ann["list"]:
            if "사업보고서" in item.get("report_nm", ""):
                text = _fetch_report_zip_text(item.get("rcept_no", ""), max_chars=1_500_000)
                if text:
                    return text

    return ""


def extract_social_section(full_text: str) -> str:
    """전체 텍스트에서 S 관련 섹션 추출 (직원현황 표 + 사회 관련 문장)."""
    parts = []

    # 1) '직원 등의 현황' 섹션 (표 형식 데이터)
    emp_start = -1
    for marker in ("직원 등의 현황", "직원 등 현황", "직원현황"):
        emp_start = full_text.find(marker)
        if emp_start >= 0:
            break
    if emp_start >= 0:
        emp_end = full_text.find("미등기임원 보수", emp_start)
        if emp_end < 0:
            emp_end = emp_start + 15000
        parts.append(full_text[emp_start:emp_end])

    # 2) 산업재해/안전 관련
    for kw in ["산업재해", "재해율", "안전보건"]:
        idx = full_text.find(kw)
        if idx >= 0:
            parts.append(full_text[max(0, idx - 100):idx + 1000])
            break

    # 3) 복리후생비 (손익계산서)
    idx = full_text.find("복리후생비")
    if idx >= 0:
        parts.append(full_text[max(0, idx - 50):idx + 200])

    # 4) 육아휴직 사용률
    idx = full_text.find("육아휴직")
    if idx >= 0:
        parts.append(full_text[idx:idx + 1500])

    # 5) 일반 사회 키워드 문장
    social_keywords = [
        "신규 채용", "교육훈련비", "이직률", "여성 비율",
        "장애인 고용", "노조 가입", "봉사 참여", "개인정보",
    ]
    lines = full_text.split(". ")
    for line in lines:
        if any(kw in line for kw in social_keywords):
            parts.append(line)

    result = "\n".join(parts)
    return result[:300_000] if result else full_text[:300_000]


def test_patterns_on_text(text: str, corp_name: str) -> dict:
    """문장형 + DART 표 형식 패턴 매칭 결과를 반환."""
    results = {
        "headcount": [],
        "ratio": [],
        "money": [],
        "count": [],
        "dart_table": [],
        "unmatched_social_sentences": [],
    }

    # Phase A: 문장형 패턴
    for m in _S_HEADCOUNT_PATTERN.finditer(text):
        label = m.group("label")
        value = m.group("value")
        kesg = _find_kesg_code(label)
        results["headcount"].append({
            "label": label, "value": value, "unit": "명",
            "kesg_code": kesg, "context": text[max(0,m.start()-20):m.end()+20]
        })

    for m in _S_RATIO_PATTERN.finditer(text):
        label = m.group("label")
        value = m.group("value")
        kesg = _find_kesg_code(label)
        results["ratio"].append({
            "label": label, "value": value, "unit": "%",
            "kesg_code": kesg, "context": text[max(0,m.start()-20):m.end()+20]
        })

    for m in _S_MONEY_PATTERN.finditer(text):
        label = m.group("label")
        value = m.group("value")
        unit = m.group("unit")
        kesg = _find_kesg_code(label)
        results["money"].append({
            "label": label, "value": value, "unit": unit,
            "kesg_code": kesg, "context": text[max(0,m.start()-20):m.end()+20]
        })

    for m in _S_COUNT_PATTERN.finditer(text):
        label = m.group("label")
        value = m.group("value")
        kesg = _find_kesg_code(label)
        results["count"].append({
            "label": label, "value": value, "unit": "건",
            "kesg_code": kesg, "context": text[max(0,m.start()-20):m.end()+20]
        })

    # Phase B: DART 표 형식 패턴
    # 직원현황 섹션 분리
    emp_start = -1
    for marker in ("직원 등의 현황", "직원 등 현황"):
        emp_start = text.find(marker)
        if emp_start >= 0:
            break
    if emp_start >= 0:
        emp_end = text.find("미등기임원 보수", emp_start)
        if emp_end < 0:
            emp_end = emp_start + 15000
        emp_section = text[emp_start:emp_end]

        m = _S_DART_EMP_TOTAL_PATTERN.search(emp_section)
        if m:
            regular = int(m.group("regular").replace(",", ""))
            total = int(m.group("total").replace(",", ""))
            if total > 100 and regular <= total:
                ratio = round(regular / total * 100, 1)
                results["dart_table"].append({
                    "label": "정규직 비율 (표)", "value": f"{ratio}",
                    "unit": "%", "kesg_code": "S-2-2",
                    "detail": f"정규직 {regular:,} / 전체 {total:,}"
                })

        male_total = female_total = 0
        for gm in _S_DART_GENDER_PATTERN.finditer(emp_section):
            total_val = int(gm.group("total").replace(",", ""))
            if gm.group("gender") == "남":
                male_total = total_val
            else:
                female_total = total_val
        combined = male_total + female_total
        if combined > 100:
            female_ratio = round(female_total / combined * 100, 1)
            results["dart_table"].append({
                "label": "여성 비율 (표)", "value": f"{female_ratio}",
                "unit": "%", "kesg_code": "S-3-1",
                "detail": f"여성 {female_total:,} / 전체 {combined:,}"
            })

    # 육아휴직 사용률
    pm = _S_DART_PARENTAL_RATE_PATTERN.search(text)
    if pm:
        results["dart_table"].append({
            "label": "육아휴직 사용률 (표)", "value": pm.group("value"),
            "unit": "%", "kesg_code": "S-2-7",
            "detail": "육아휴직 사용률 전체"
        })

    # 복리후생비
    wm = _S_DART_WELFARE_PATTERN.search(text)
    if wm:
        val = int(wm.group("value").replace(",", ""))
        if val > 1000:
            results["dart_table"].append({
                "label": "복리후생비 (표)", "value": f"{val:,}",
                "unit": "백만원", "kesg_code": "S-2-5",
                "detail": f"복리후생비 {val:,}백만원"
            })

    # 매칭 안 된 S 관련 문장 (숫자 포함하는 것만)
    s_sentence_pattern = re.compile(
        r"[^.]{0,50}(?:채용|고용|퇴직|이직|교육훈련|복리후생|노조|여성|장애인|"
        r"재해|산재|안전|사망|봉사|개인정보|법규\s*위반|정규직|비정규직|"
        r"인력|직원|임직원|근로자)[^.]{0,100}"
    )
    matched_spans = set()
    for pat in [_S_HEADCOUNT_PATTERN, _S_RATIO_PATTERN, _S_MONEY_PATTERN, _S_COUNT_PATTERN]:
        for m in pat.finditer(text):
            matched_spans.add((m.start(), m.end()))

    for m in s_sentence_pattern.finditer(text):
        already_matched = any(
            s <= m.start() <= e or s <= m.end() <= e
            for s, e in matched_spans
        )
        if not already_matched:
            sentence = m.group().strip()
            if re.search(r"\d", sentence) and 10 < len(sentence) < 200:
                # 표 형식 데이터 (숫자열)는 제외
                if not re.match(r"^[\d,\s.\-]+$", sentence):
                    results["unmatched_social_sentences"].append(sentence)

    return results


def _find_kesg_code(label: str) -> str | None:
    """레이블에서 K-ESG 코드를 찾는다."""
    raw = label.replace(" ", "")
    for key, code in _S_LABEL_TO_KESG.items():
        if key.replace(" ", "") in raw or raw in key.replace(" ", ""):
            return code
    return None


def test_build_pipeline(corp_code: str, raw_text: str, report_year: int = 2024) -> dict:
    """build_evidence_graph 전체 파이프라인 테스트.

    raw_text를 통째로 하나의 snippet으로 전달해 DART 표 형식 패턴도 동작하게 한다.
    """
    # 전체 텍스트를 큰 청크로 나눠서 snippet으로 사용 (직원현황 섹션 포함)
    chunk_size = 5000
    chunks = [raw_text[i:i+chunk_size] for i in range(0, len(raw_text), chunk_size)]

    # raw_text_snippets 만으로 테스트
    report_text_only = CompanyReport(
        corp_code=corp_code,
        corp_name="",
        industry="",
        report_year=report_year,
        financials={},
        kesg_data={},
        raw_text_snippets=chunks,
        source="dart_text_only",
    )
    graph_text = build_evidence_graph(report_text_only)
    s_nodes_text = [n for n in graph_text.nodes.values() if n.metric.startswith("S-")]

    # kesg_data + raw_text_snippets 비교 (기존 샘플 사용)
    from esgenie.dart_client import load_sample_report
    try:
        sample = load_sample_report(corp_code)
        # 샘플의 raw_text_snippets에 DART 원문 청크 추가
        combined_snippets = sample.raw_text_snippets + chunks[:10]
        report_combined = CompanyReport(
            corp_code=corp_code,
            corp_name=sample.corp_name,
            industry=sample.industry,
            report_year=sample.report_year,
            financials=sample.financials,
            kesg_data=sample.kesg_data,
            raw_text_snippets=combined_snippets,
            source="combined",
        )
        graph_combined = build_evidence_graph(report_combined)
        s_nodes_combined = [n for n in graph_combined.nodes.values() if n.metric.startswith("S-")]
    except FileNotFoundError:
        s_nodes_combined = []

    return {
        "text_only_s_nodes": len(s_nodes_text),
        "text_only_nodes_detail": [(n.metric, n.value, n.unit, n.source) for n in s_nodes_text],
        "combined_s_nodes": len(s_nodes_combined),
        "combined_detail": [(n.metric, n.value, n.unit, n.source) for n in s_nodes_combined],
        "snippets_used": len(chunks),
    }


def main():
    print("=" * 80)
    print("DART 실제 공시 데이터 S 영역 수치 추출 검증")
    print("=" * 80)

    if SETTINGS.use_mock_dart:
        print("\n[ERROR] DART_API_KEY 없음. 실제 API 호출 불가.")
        sys.exit(1)

    all_results = {}

    for dart_corp_code, corp_name, stock_code in TARGETS:
        print(f"\n{'─' * 60}")
        print(f"[{corp_name}] corp_code={dart_corp_code}")
        print(f"{'─' * 60}")

        # Step 1: DART에서 원문 수집
        print(f"  → DART API 원문 수집 중...")
        full_text = fetch_annual_report_text(dart_corp_code, year=2024)

        if not full_text:
            print(f"  → 2024 실패, 2023 시도...")
            full_text = fetch_annual_report_text(dart_corp_code, year=2023)

        if not full_text:
            print(f"  [SKIP] {corp_name}: 원문 수집 실패")
            continue

        print(f"  → 원문 길이: {len(full_text):,} 자")

        # 사회 섹션 추출
        social_text = extract_social_section(full_text)
        print(f"  → 사회 섹션 길이: {len(social_text):,} 자")

        # 저장
        out_path = OUTPUT_DIR / f"{stock_code}_raw_social.txt"
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(social_text)
        print(f"  → 저장: {out_path}")

        # Step 2: 패턴 매칭 테스트
        print(f"\n  [패턴 매칭 결과]")
        pattern_results = test_patterns_on_text(social_text, corp_name)

        for pat_name in ["headcount", "ratio", "money", "count"]:
            matches = pattern_results[pat_name]
            print(f"    {pat_name}: {len(matches)}건")
            for item in matches[:5]:
                code_str = f" → {item['kesg_code']}" if item.get('kesg_code') else " → [매핑없음]"
                print(f"      {item['label']}: {item['value']}{item['unit']}{code_str}")

        # DART 표 형식
        dart_table = pattern_results["dart_table"]
        print(f"    dart_table: {len(dart_table)}건")
        for item in dart_table:
            print(f"      {item['label']}: {item['value']}{item['unit']} → {item['kesg_code']}")
            if item.get("detail"):
                print(f"        ({item['detail']})")

        # Step 3: 전체 파이프라인 테스트
        print(f"\n  [파이프라인 결과]")
        pipeline = test_build_pipeline(stock_code, social_text)
        print(f"    raw_text만: S 노드 {pipeline['text_only_s_nodes']}개 (snippet {pipeline['snippets_used']}개)")
        for m, v, u, src in pipeline["text_only_nodes_detail"][:15]:
            print(f"      {m}: {v} {u} ({src})")
        if pipeline["combined_s_nodes"]:
            print(f"    kesg_data + text 결합: S 노드 {pipeline['combined_s_nodes']}개")
            for m, v, u, src in pipeline["combined_detail"][:10]:
                print(f"      {m}: {v} {u} ({src})")

        # 매칭 안 된 문장
        unmatched = pattern_results["unmatched_social_sentences"]
        if unmatched:
            # 중복 제거
            unique_unmatched = list(dict.fromkeys(unmatched))
            print(f"\n  [패턴 미매칭 S 문장] ({len(unique_unmatched)}건, 상위 15개)")
            for sent in unique_unmatched[:15]:
                print(f"    • {sent[:120]}")

        all_results[corp_name] = {
            "pattern": pattern_results,
            "pipeline": pipeline,
        }

    # Step 4: 종합 리포트
    print("\n" + "=" * 80)
    print("종합 리포트")
    print("=" * 80)

    total_sentence_matched = 0
    total_dart_table = 0
    total_unmatched = 0
    pattern_coverage = {"headcount": 0, "ratio": 0, "money": 0, "count": 0}

    for corp_name, data in all_results.items():
        p = data["pattern"]
        for key in pattern_coverage:
            pattern_coverage[key] += len(p[key])
        total_sentence_matched += sum(len(p[k]) for k in pattern_coverage)
        total_dart_table += len(p["dart_table"])
        total_unmatched += len(set(p["unmatched_social_sentences"]))

    print(f"\n문장형 패턴 매칭:")
    for key, count in pattern_coverage.items():
        print(f"  {key:12s}: {count}건")
    print(f"  {'총 문장형':12s}: {total_sentence_matched}건")
    print(f"\nDART 표 형식 패턴: {total_dart_table}건")
    print(f"미매칭 문장: {total_unmatched}건")

    # 패턴 보완 필요 여부 분석
    all_unmatched = []
    for corp_name, data in all_results.items():
        all_unmatched.extend(set(data["pattern"]["unmatched_social_sentences"]))

    keyword_freq = {}
    for sent in all_unmatched:
        for kw in ["교육", "훈련", "복지", "급여", "임금", "산재", "재해",
                   "채용", "퇴직", "이직", "여성", "장애", "봉사", "안전",
                   "근로시간", "초과근무", "육아휴직", "출산"]:
            if kw in sent:
                keyword_freq[kw] = keyword_freq.get(kw, 0) + 1

    if keyword_freq:
        print(f"\n패턴 보완 후보 (미매칭 문장 빈출 키워드):")
        for kw, freq in sorted(keyword_freq.items(), key=lambda x: -x[1])[:10]:
            print(f"  {kw}: {freq}회")

    print("\n" + "=" * 80)
    print("결론")
    print("=" * 80)
    print("""
1. 문장형 패턴 (지속가능경영보고서 대상):
   - DART 사업보고서 원문은 표가 공백으로 평탄화된 형식이라 문장형 패턴 매칭률이 낮음
   - 이 패턴들은 기업이 별도 발행하는 지속가능경영보고서 텍스트에 최적화되어 있음

2. DART 표 형식 패턴 (사업보고서 원문 대상):
   - '직원 등의 현황' 표에서 정규직 비율(S-2-2), 여성 비율(S-3-1) 안정적 추출
   - 육아휴직 사용률, 복리후생비 등 추가 수치도 추출 가능
   - 구조화 API(empSttus.json) 결과와 교차 검증 가능

3. 권장 사항:
   - DART 구조화 API를 1순위로 사용 (이미 dart_client.py에서 처리)
   - 원문 텍스트 추출은 구조화 API에서 빠진 항목의 보완 역할
   - 문장형 패턴은 지속가능경영보고서 PDF/텍스트 입력 시 활성화
""")


if __name__ == "__main__":
    main()
