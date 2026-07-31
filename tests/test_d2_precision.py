"""D2 모호어 축 정밀도 — 문맥 오탐 제거 회귀 테스트.

배경 (라이브 실측 `outputs/audit_trace_00164788_E_20260727_003507.json`):
  D1을 0으로 만든 뒤 E 섹션 위험이 전부 D2에서 나왔고, 발화 근거 3건이 모두 오탐이었다.
    (나) `친환경 인증 제품 및 서비스 항목의 공개가 필요하다`  ← K-ESG E-9-1 항목명
    (다) `탄소중립추진팀 중심으로`                            ← 부서 고유명사

핵심 원칙: **어휘 사전에서 단어를 빼지 않는다.** `친환경`·`탄소중립`은 진짜 그린워싱의
핵심 신호다. 문맥 면제이지 사전 삭제가 아니므로, 각 면제마다 **음성 테스트**(면제
문맥 밖에서는 여전히 발화)를 짝지어 과차단을 고정한다.

분모(`max(D2_THRESHOLD*4, 1)` = 1.0, 히트 1개면 만점)는 이번 범위가 아니다 —
`docs/D2_영향조사_2026-07-29.md` §3: 분모를 4로 올리면 held-out test 룰 단독 F1이
0.532 → 0.255로 떨어진다(실제 양성 83건 중 41건이 모호어 1~2개).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from esgenie.config import D2_THRESHOLD
from esgenie.knowledge.greenwash_lexicon import ALL_VAGUE, vague_matches
from esgenie.layer3_detect import _score_d2_modifier

ROOT = Path(__file__).resolve().parents[1]

# 라이브 트레이스 문장 2·3 전문 (00164788 현대모비스 E 섹션, 2026-07-27)
TRACE_SENT_2 = (
    "현대모비스는 글로벌SHE지원팀과 탄소중립추진팀 중심으로 환경경영 체계를 운영하며, "
    "다양한 부서와 협업하여 환경 개선 활동을 모니터링하고 있다. 또한, 원부자재 사용 "
    "효율화 및 친환경 원부자재 사용, 에너지 절감과 재생에너지 도입, 용수 재활용 및 저장, "
    "온실가스 감축, 폐기물 처리 및 재활용 향상, 환경오염물질 관리, 환경 법규 위반 개선 "
    "활동 등을 추진하고 있다."
)
TRACE_SENT_3 = (
    "단기적으로 재생에너지 사용 비율 확대와 온실가스 배출량 검증 체계 구축을 목표로 "
    "하고 있다. 향후 공시 보완 과제로는 환경경영 목표 수립과 추진체계, 온실가스 배출량 "
    "검증, 재사용 용수 비율, 친환경 인증 제품 및 서비스 항목의 공개가 필요하다."
)


def _d2(sentence: str) -> float:
    return _score_d2_modifier(sentence).score


# ---- 1. (나) K-ESG 항목명 면제 ---------------------------------------------

class TestKesgItemNameExemption:
    """항목명 구간의 모호어는 면제 — L2가 미공시를 정직하게 밝힐 때 D2가 오르는 자책골."""

    def test_item_name_in_disclosure_gap_sentence(self):
        """E-9-1 '친환경 인증 제품/서비스' 항목명 언급 → D2 = 0."""
        assert _d2("친환경 인증 제품 및 서비스 항목의 공개가 필요하다") == 0.0

    def test_search_term_is_not_an_exemption_source(self):
        """`search_terms`는 면제 출처가 **아니다** — E-1-1 '탄소중립 목표'는 여전히 발화.

        E-1-1의 name은 '환경경영 목표 수립'(모호어 없음)이고 '탄소중립 목표'는 search_term이다.
        search_terms로 면제하면 벤치 양성 GOLD-40 '친환경 인증 침대로 가족 건강을 지키세요'가
        E-9-1 search_term '친환경 인증'으로 통째로 면제된다. 항목명만 쓰는 이유가 이것이다.
        """
        assert _d2("탄소중립 목표 항목의 공개가 필요하다") > 0.0

    def test_bench_positive_not_exempted_by_search_term(self):
        """벤치 양성 GOLD-40 — '친환경 인증'이 광고 문구여도 면제되지 않는다."""
        assert _d2("친환경 인증 침대로 가족 건강을 지키세요") == 1.0

    def test_negative_bare_eco_claim_still_fires(self):
        """음성: 항목명 문맥 밖의 '친환경'은 여전히 발화해야 한다 — 과차단 방지."""
        assert _d2("당사는 친환경 기업입니다") > 0.0

    def test_negative_lexicon_not_deleted(self):
        """음성: 어휘 자체가 사전에서 빠지지 않았다."""
        for term in ("친환경", "탄소중립", "녹색"):
            assert term in ALL_VAGUE
            assert term in vague_matches(f"우리는 {term} 그 자체입니다")


# ---- 2. (다) 조직 고유명사 면제 ---------------------------------------------

class TestOrgProperNounExemption:
    """모호어 직후 조직 접미가 바로 붙으면 부서명 — 그린워싱 수사가 아니다."""

    def test_carbon_neutral_task_force(self):
        assert _d2("탄소중립추진팀 중심으로 운영한다.") == 0.0

    @pytest.mark.parametrize("org", [
        "탄소중립추진팀", "탄소중립부", "탄소중립실", "탄소중립본부",
        "탄소중립위원회", "탄소중립센터", "탄소중립추진단", "친환경사업부",
    ])
    def test_org_suffixes(self, org: str):
        assert _d2(f"{org}에서 관리한다.") == 0.0

    def test_negative_verb_form_still_fires(self):
        """음성: '탄소중립을 추진한다'는 접미가 바로 붙지 않아 여전히 발화."""
        assert _d2("탄소중립을 추진하고 있다") > 0.0

    def test_negative_spaced_form_still_fires(self):
        """음성: '탄소중립 추진팀'처럼 공백이 있으면? — 붙은 경우만 면제이므로 발화."""
        assert _d2("탄소중립 달성을 위해 전사적으로 추진한다") > 0.0


# ---- 3. 라이브 트레이스 실측 문장 -------------------------------------------

class TestLiveTraceSentences:
    """트레이스 문장 2·3 — 수정 전에는 둘 다 D2 = 1.0 (만점 오탐)."""

    def test_sentence_2_carbon_neutral_exempted(self):
        """'탄소중립추진팀'(부서명)은 면제된다 — 히트 2개 → 1개.

        점수는 1.0에 머문다: 분모=1이라 남은 히트 1개로도 만점이다(§4). 남은 히트는
        '친환경 원부자재 사용'인데 이건 항목명도 부서명도 아니라 (나)(다) 어디에도
        해당하지 않는다 — 근거 없는 환경 라벨이라 D2가 잡는 게 오히려 맞다.
        점수를 내리려면 분모 재설계가 필요하고, 그건 §5 이월 과제다.
        """
        assert vague_matches(TRACE_SENT_2) == ["친환경"]

    def test_sentence_3_drops_to_zero(self):
        """'친환경 인증 제품 및 서비스' 항목명뿐 → D2 = 0."""
        assert _d2(TRACE_SENT_3) == 0.0


# ---- 4. (가) 분모 — 수정하지 않음을 고정 -------------------------------------

class TestDenominatorUnchanged:
    """분모는 이번 범위가 아니다. **현재 동작을 고정**해 조용한 변경을 막는다.

    바꾸려면 `docs/D2_영향조사_2026-07-29.md` §5의 착수 조건(held-out 실키 전건
    재측정)을 먼저 충족할 것. 이 테스트가 깨지면 벤치 F1을 다시 재야 한다는 신호다.
    """

    def test_denominator_is_one(self):
        assert max(D2_THRESHOLD * 4, 1) == 1.0

    def test_single_hit_is_full_score(self):
        """히트 1개 → 1.0. 정보량 없는 이진 검출기지만, held-out F1이 이 위에 서 있다."""
        assert _d2("당사는 혁신적입니다") == 1.0


# ---- 5. 진성 그린워싱 불변 ---------------------------------------------------

class TestTrueGreenwashUnaffected:
    """면제 규칙이 진짜 양성을 깎지 않는지 — dev 벤치 양성 표본으로 고정."""

    def test_superlative_eco_claim(self):
        assert _d2("세계 최고 수준의 친환경 기업입니다") == 1.0

    def test_dev_bench_positives_unchanged(self):
        """dev 양성 중 D2가 발화하던 케이스는 전건 동일 점수를 유지해야 한다."""
        bench = json.loads((ROOT / "data" / "benchmark_v2" / "dev.json")
                           .read_text(encoding="utf-8"))
        positives = [c for c in bench["cases"] if c["label"] == "greenwash"]
        firing = [c for c in positives if vague_matches(c["sentence"])]
        assert firing, "dev 양성에 모호어 케이스가 있어야 표본이 성립한다"
        for case in firing:
            assert _d2(case["sentence"]) == 1.0, f"{case['id']} D2 하락"

    def test_dev_bench_clean_not_worsened(self):
        """음성 방향: clean 케이스가 새로 발화하지 않는다 (면제는 점수를 올리지 않음)."""
        bench = json.loads((ROOT / "data" / "benchmark_v2" / "dev.json")
                           .read_text(encoding="utf-8"))
        for case in bench["cases"]:
            if case["label"] != "clean":
                continue
            if not vague_matches(case["sentence"]):
                assert _d2(case["sentence"]) == 0.0, f"{case['id']} 없던 발화 생성"
