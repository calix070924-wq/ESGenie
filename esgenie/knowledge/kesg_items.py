"""K-ESG 가이드라인 v1.0 기반 61개 진단 항목 정의.

출처: 산업통상자원부·관계부처 합동 「K-ESG 가이드라인」 (2021).
본 MVP는 공개된 가이드라인의 항목 코드/명칭을 요약 참조한다.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Area = Literal["P", "E", "S", "G"]


@dataclass(frozen=True)
class KESGItem:
    code: str        # 예: "E-1-1"
    area: Area       # P / E / S / G
    category: str    # 대분류
    name: str        # 항목명
    data_type: str   # 정량 / 정성 / 혼합
    description: str


# 정보공시 (Public disclosure) — 5개
P_ITEMS = [
    KESGItem("P-1-1", "P", "정보공시 형식", "ESG 정보공시 방식", "정성",
             "ESG 정보를 공시하는 방식(보고서, 홈페이지, DART 등)"),
    KESGItem("P-1-2", "P", "정보공시 형식", "ESG 정보공시 주기", "정성",
             "ESG 정보 공시 주기 (연간/반기/수시)"),
    KESGItem("P-1-3", "P", "정보공시 형식", "ESG 정보공시 범위", "정성",
             "연결/별도 기준 등 공시 범위"),
    KESGItem("P-2-1", "P", "정보공시 내용", "ESG 핵심이슈 및 KPI", "정성",
             "중대성 평가 결과 및 KPI 제시 여부"),
    KESGItem("P-3-1", "P", "정보공시 검증", "ESG 정보공시 검증", "정성",
             "제3자 검증기관 검증 여부 및 수준"),
]

# 환경 (Environmental) — 17개
E_ITEMS = [
    KESGItem("E-1-1", "E", "환경경영 목표", "환경경영 목표 수립", "정성",
             "중장기 환경경영 전략 및 목표 수립 여부"),
    KESGItem("E-1-2", "E", "환경경영 목표", "환경경영 추진체계", "정성",
             "환경경영 전담 조직/인력 운영"),
    KESGItem("E-2-1", "E", "원부자재", "원부자재 사용량", "정량",
             "연간 원부자재 총 사용량 (톤)"),
    KESGItem("E-2-2", "E", "원부자재", "재생 원부자재 비율", "정량",
             "재활용 원부자재 사용 비율 (%)"),
    KESGItem("E-3-1", "E", "온실가스", "온실가스 배출량 (Scope1 + Scope2)", "정량",
             "직접/간접 온실가스 배출량 (tCO2eq)"),
    KESGItem("E-3-2", "E", "온실가스", "온실가스 배출량 (Scope3)", "정량",
             "기타 간접 배출량 (tCO2eq)"),
    KESGItem("E-3-3", "E", "온실가스", "온실가스 배출량 검증", "정성",
             "제3자 검증 여부 및 검증기관"),
    KESGItem("E-4-1", "E", "에너지", "에너지 사용량", "정량",
             "연간 총 에너지 사용량 (TJ)"),
    KESGItem("E-4-2", "E", "에너지", "재생에너지 사용 비율", "정량",
             "총 사용량 대비 재생에너지 비율 (%)"),
    KESGItem("E-5-1", "E", "용수", "용수 사용량", "정량",
             "연간 취수량 (ton)"),
    KESGItem("E-5-2", "E", "용수", "재사용 용수 비율", "정량",
             "총 취수량 대비 재사용 비율 (%)"),
    KESGItem("E-6-1", "E", "폐기물", "폐기물 배출량", "정량",
             "연간 폐기물 배출량 (톤)"),
    KESGItem("E-6-2", "E", "폐기물", "폐기물 재활용 비율", "정량",
             "총 폐기물 대비 재활용 비율 (%)"),
    KESGItem("E-7-1", "E", "오염물질", "대기오염물질 배출량", "정량",
             "NOx, SOx, 먼지 등 (kg)"),
    KESGItem("E-7-2", "E", "오염물질", "수질오염물질 배출량", "정량",
             "BOD, COD 등 (kg)"),
    KESGItem("E-8-1", "E", "환경 법/규제", "환경 법규 위반", "정량",
             "환경 법규 위반 건수 및 과징금"),
    KESGItem("E-9-1", "E", "친환경 투자", "친환경 인증 제품/서비스", "정량",
             "친환경 인증 제품 비율 (%)"),
]

# 사회 (Social) — 22개
S_ITEMS = [
    KESGItem("S-1-1", "S", "목표", "목표 수립 및 공시", "정성",
             "사회적 책임 목표 수립"),
    KESGItem("S-2-1", "S", "노동", "신규 채용 및 고용 유지", "정량",
             "신규 채용 인원 및 이직률"),
    KESGItem("S-2-2", "S", "노동", "정규직 비율", "정량",
             "전체 직원 대비 정규직 비율"),
    KESGItem("S-2-3", "S", "노동", "자발적 이직률", "정량", "자발적 이직률 (%)"),
    KESGItem("S-2-4", "S", "노동", "교육훈련비", "정량", "1인당 연간 교육훈련비"),
    KESGItem("S-2-5", "S", "노동", "복리후생비", "정량", "1인당 연간 복리후생비"),
    KESGItem("S-2-6", "S", "노동", "결사의 자유 보장", "정성", "노동조합 가입률 등"),
    KESGItem("S-3-1", "S", "다양성", "여성 구성원 비율", "정량", "전체 대비 여성 비율"),
    KESGItem("S-3-2", "S", "다양성", "여성 급여 비율", "정량", "남성 대비 여성 급여"),
    KESGItem("S-3-3", "S", "다양성", "장애인 고용률", "정량", "장애인 고용률"),
    KESGItem("S-4-1", "S", "산업안전", "안전보건 추진체계", "정성", "안전보건 조직/정책"),
    KESGItem("S-4-2", "S", "산업안전", "산업재해율", "정량", "재해율 및 사망만인율"),
    KESGItem("S-5-1", "S", "인권", "인권정책 수립", "정성", "인권 정책 및 준수"),
    KESGItem("S-5-2", "S", "인권", "인권 리스크 평가", "정성", "인권 실사 수행"),
    KESGItem("S-6-1", "S", "동반성장", "협력사 ESG 경영", "정성", "협력사 ESG 관리"),
    KESGItem("S-6-2", "S", "동반성장", "협력사 지원 프로그램", "정성", "금융·기술 지원"),
    KESGItem("S-6-3", "S", "동반성장", "협력사 ESG 지원", "정성", "협력사 ESG 역량 강화"),
    KESGItem("S-7-1", "S", "지역사회", "전략적 사회공헌", "정성", "전략적 CSR 방향성"),
    KESGItem("S-7-2", "S", "지역사회", "구성원 봉사참여", "정량", "봉사활동 참여율"),
    KESGItem("S-8-1", "S", "정보보호", "정보보호 시스템 구축", "정성", "정보보호 체계"),
    KESGItem("S-8-2", "S", "정보보호", "개인정보 침해 및 구제", "정량", "개인정보 유출 건수"),
    KESGItem("S-9-1", "S", "사회 법/규제", "사회 법규 위반", "정량", "사회 법규 위반 건수"),
]

# 지배구조 (Governance) — 17개
G_ITEMS = [
    KESGItem("G-1-1", "G", "이사회 구성", "이사회 내 ESG 안건 상정", "정성", "ESG 안건 상정 빈도"),
    KESGItem("G-1-2", "G", "이사회 구성", "사외이사 비율", "정량", "이사회 내 사외이사 비율"),
    KESGItem("G-1-3", "G", "이사회 구성", "대표이사·이사회 의장 분리", "정성", "분리 여부"),
    KESGItem("G-1-4", "G", "이사회 구성", "이사회 성별 다양성", "정량", "여성 이사 비율"),
    KESGItem("G-1-5", "G", "이사회 구성", "사외이사 전문성", "정성", "사외이사 전문 영역"),
    KESGItem("G-2-1", "G", "이사회 활동", "전체 이사 출석률", "정량", "이사회 참석률 (%)"),
    KESGItem("G-2-2", "G", "이사회 활동", "사내이사 출석률", "정량", "사내이사 참석률"),
    KESGItem("G-2-3", "G", "이사회 활동", "이사회 안건 처리", "정성", "안건 심의/의결 내용"),
    KESGItem("G-3-1", "G", "주주권리", "주주총회 소집 공고", "정성", "공고 시점 및 방식"),
    KESGItem("G-3-2", "G", "주주권리", "주주총회 집중일 이외 개최", "정성", "집중일 외 개최 여부"),
    KESGItem("G-3-3", "G", "주주권리", "집중/전자/서면 투표", "정성", "투표 제도 도입"),
    KESGItem("G-3-4", "G", "주주권리", "배당정책 및 이행", "정량", "배당 성향"),
    KESGItem("G-4-1", "G", "윤리경영", "윤리규범 위반사항 공시", "정성", "위반 공시 체계"),
    KESGItem("G-5-1", "G", "감사기구", "내부감사부서 설치", "정성", "내부감사 기구"),
    KESGItem("G-5-2", "G", "감사기구", "감사기구 전문성", "정성", "회계·재무 전문가"),
    KESGItem("G-6-1", "G", "지배구조 법/규제", "지배구조 법규 위반", "정량", "지배구조 법규 위반 건수"),
    KESGItem("G-6-2", "G", "지배구조 법/규제", "내부거래 공시", "정성", "내부거래 공시 준수"),
]

ALL_ITEMS: list[KESGItem] = P_ITEMS + E_ITEMS + S_ITEMS + G_ITEMS

assert len(ALL_ITEMS) == 61, f"K-ESG 항목 개수 오류: {len(ALL_ITEMS)}"

# K-ESG 기본형 28개 — 중소기업 대상 핵심 항목 (공급망 ESG 대응 기준)
BASIC_28_CODES: list[str] = [
    # 정보공시 (1)
    "P-1-1",
    # 환경 (10)
    "E-1-1", "E-1-2",
    "E-2-1",
    "E-3-1", "E-3-3",
    "E-4-1", "E-4-2",
    "E-5-1",
    "E-6-1", "E-6-2",
    # 사회 (10)
    "S-1-1",
    "S-2-1", "S-2-6",
    "S-3-1",
    "S-4-1", "S-4-2",
    "S-5-1",
    "S-6-1",
    "S-7-1",
    "S-8-1",
    # 지배구조 (7)
    "G-1-1", "G-1-2",
    "G-2-1",
    "G-3-1", "G-3-4",
    "G-4-1",
    "G-5-1",
]

BASIC_28_ITEMS: list[KESGItem] = [it for it in ALL_ITEMS if it.code in BASIC_28_CODES]

assert len(BASIC_28_ITEMS) == 28, f"K-ESG 기본형 항목 개수 오류: {len(BASIC_28_ITEMS)}"


# ---- 프로파일 ---------------------------------------------------------------
# K-ESG 61항목 체계 위에서 기업 규모에 맞는 추적 범위를 선택한다.
#   sme  — 중소기업 기본형 28항목 (공급망 ESG 실사 대응 핵심)
#   full — 61항목 전체 (대기업·중견 확장)
# 커버리지 분모는 프로파일 기준 — 중소기업을 61항목으로 평가하면
# 커버리지가 구조적으로 낮게 나와 의미가 없기 때문.

Profile = Literal["sme", "full"]

PROFILES: dict[str, list[KESGItem]] = {
    "sme": BASIC_28_ITEMS,
    "full": ALL_ITEMS,
}

PROFILE_LABELS: dict[str, str] = {
    "sme": "중소기업 기본형 (28항목)",
    "full": "전체 (61항목)",
}


def items_for_profile(profile: Profile) -> list[KESGItem]:
    try:
        return PROFILES[profile]
    except KeyError:
        raise ValueError(f"알 수 없는 프로파일: {profile!r} (sme | full)") from None


def detect_profile(corp_code: str) -> Profile:
    """종목코드 기반 프로파일 자동 판별.

    상장사 종목코드는 6자리 숫자(예: 005930) — full.
    비상장 중소기업은 내부 식별자(예: SME001) — sme.
    """
    return "full" if corp_code.strip().isdigit() else "sme"


def by_area(area: Area) -> list[KESGItem]:
    return [it for it in ALL_ITEMS if it.area == area]


def by_code(code: str) -> KESGItem | None:
    for it in ALL_ITEMS:
        if it.code == code:
            return it
    return None
