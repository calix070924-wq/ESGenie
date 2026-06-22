"""RBA 행동규범 v8.0(2024) 기반 42개 조항 정의 — 협력사 실사 기둥의 캐노니컬 substrate.

출처: Responsible Business Alliance Code of Conduct v8.0 (2024년 1월 1일 발효).
      https://www.responsiblebusiness.org/code-of-conduct/
      2026년 현재 최신(5년 주기, 다음 개정 ~2029. v8.0.1(2025)은 마이너 포인트 릴리스).

설계 메모
--------
- 공시 기둥(K-ESG, knowledge/kesg_items.py)과 **병렬**인 실사 기둥의 substrate.
- 현대차·삼성·SK·LG 등 OEM 협력사 폼은 이 RBA 위에 얹는 *어댑터*로 처리한다
  (기존 SAQ가 K-ESG 위에 얹힌 것과 동일 패턴; supplychain/frameworks/ 참조).
- `kesg_codes`: K-ESG 항목과 겹치는 조항은 코드를 크로스워크해 **공유 증빙 풀**과
  기존 derive/evidence 엔진(kesg_evidence_requirements)을 그대로 재사용한다.
  매핑은 단일 dict `_KESG_XWALK` 한 곳에서 관리한다(kesg_items._enrich 패턴 차용).
  · 환경(C)은 K-ESG E와 겹침이 커서 정량 data_point까지 공유된다.
  · 안전보건(B)은 K-ESG가 S-4-1/S-4-2 둘뿐이라 거칠게 매핑된다(K-ESG의 한계).
  · 일부 RBA 고유 조항(C-3 유해물질, C-6 물질규제, D-4 IP, D-7 분쟁광물 등)은
    K-ESG에 대응 항목이 없어 빈 튜플 — RBA 자체 search_terms로만 검색한다.
- `hmc_area`: 현대차 협력사 행동규범 5영역 매핑. RBA 섹션과 1:1 대응.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Literal

# RBA Code of Conduct 5개 섹션
Section = Literal["Labor", "HealthSafety", "Environment", "Ethics", "ManagementSystem"]

# RBA 섹션 → 한국어 / 현대차 협력사 행동규범 영역 (1:1)
SECTION_KO: dict[str, str] = {
    "Labor": "노동",
    "HealthSafety": "안전보건",
    "Environment": "환경",
    "Ethics": "윤리",
    "ManagementSystem": "경영시스템",
}
SECTION_HMC: dict[str, str] = {
    "Labor": "노동·인권",
    "HealthSafety": "안전보건",
    "Environment": "환경",
    "Ethics": "윤리",
    "ManagementSystem": "경영시스템",
}


@dataclass(frozen=True)
class RBAItem:
    """KESGItem과 동일한 베이스 스키마를 공유한다(code/name/data_type/search_terms/unit).

    추가 필드:
      kesg_codes — K-ESG 크로스워크(공유 증빙·엔진 재사용). _KESG_XWALK에서 주입.
      hmc_area   — 현대차 행동규범 영역(섹션에서 도출, 명시 노출용).
    """
    code: str          # 예: "A-1"
    section: Section
    name_ko: str
    name_en: str
    data_type: str     # 정성 / 정량 / 혼합
    description: str
    search_terms: tuple[str, ...] = ()
    unit: str = ""
    kesg_codes: tuple[str, ...] = ()
    # 한 조항이 여러 정량 지표로 분해될 때의 수치 항목들 — (K-ESG코드, 라벨, 단위).
    # 비어있지 않으면 양식 생성 시 '조항 존재형 1문항 + 지표별 수치행 N개'로 펼쳐진다.
    metrics: tuple[tuple[str, str, str], ...] = ()

    @property
    def section_ko(self) -> str:
        return SECTION_KO[self.section]

    @property
    def hmc_area(self) -> str:
        return SECTION_HMC[self.section]


# ── A. 노동 (Labor) — 6개 ────────────────────────────────────────────────
LABOR_ITEMS = [
    RBAItem("A-1", "Labor", "강제노동 금지", "Freely Chosen Employment / Prohibition of Forced Labor",
            "정성",
            "강제·구속·인신매매·담보노동 금지, 이동의 자유, 신분증 압류 금지, 채용수수료 노동자 부담 금지, "
            "모국어 근로계약서 제공.",
            search_terms=("강제노동", "강제근로", "인신매매", "담보노동", "여권 압류", "신분증 보관",
                          "채용수수료", "이주노동자", "근로계약서", "forced labor", "human trafficking",
                          "recruitment fee", "freely chosen employment")),
    RBAItem("A-2", "Labor", "미성년 근로자", "Young Workers",
            "정성",
            "아동노동 금지(만 15세/의무교육연령/최저고용연령 중 높은 기준), 18세 미만 위험·야간작업 금지, "
            "학생근로자 관리, 연령 검증 절차.",
            search_terms=("아동노동", "미성년", "연소근로자", "최저고용연령", "학생근로자", "연령확인",
                          "child labor", "young worker", "age verification")),
    RBAItem("A-3", "Labor", "근로시간", "Working Hours",
            "혼합",
            "주 60시간(연장 포함) 초과 금지, 연장근로 자발성, 7일당 1일 휴무 보장. 법정 한도 준수.",
            search_terms=("근로시간", "노동시간", "초과근무", "연장근로", "주 60시간", "휴일",
                          "working hours", "overtime", "rest day"),
            unit="시간/주"),
    RBAItem("A-4", "Labor", "임금·복리후생", "Wages and Benefits",
            "혼합",
            "최저임금·연장수당·법정복리후생 준수, 동일노동 동일임금, 징계성 임금공제 금지, 명확한 임금명세서.",
            search_terms=("임금", "최저임금", "연장수당", "급여", "복리후생", "임금명세서", "임금공제",
                          "wages", "minimum wage", "benefits", "pay slip")),
    RBAItem("A-5", "Labor", "비차별·비괴롭힘·인도적 대우",
            "Non-Discrimination / Non-Harassment / Humane Treatment",
            "정성",
            "차별(인종·성별·연령·종교·노조 등)·괴롭힘·성희롱·폭력·체벌·정신적 강압 금지. v8.0에서 "
            "인도적 대우가 비차별/비괴롭힘과 통합됨.",
            search_terms=("차별", "괴롭힘", "성희롱", "직장내 괴롭힘", "인권존중", "인도적 대우", "폭력",
                          "체벌", "discrimination", "harassment", "humane treatment", "gender-based violence")),
    RBAItem("A-6", "Labor", "결사의 자유·단체교섭", "Freedom of Association and Collective Bargaining",
            "정성",
            "노동조합 결성·가입·단체교섭·평화적 집회 권리 존중, 보복 없는 노사 소통. 법 제한 시 대체적 "
            "근로자 대표 허용.",
            search_terms=("결사의 자유", "노동조합", "단체교섭", "노사협의회", "근로자대표", "집회",
                          "freedom of association", "collective bargaining", "trade union")),
]

# ── B. 안전보건 (Health & Safety) — 8개 ──────────────────────────────────
HS_ITEMS = [
    RBAItem("B-1", "HealthSafety", "산업안전", "Occupational Health and Safety",
            "정성",
            "유해위험요인(화학·전기·화재·차량·추락 등) 식별·평가, 통제 위계(Hierarchy of Controls) 적용, "
            "PPE 제공, 임산부 보호조치.",
            search_terms=("산업안전", "위험성평가", "유해위험요인", "보호구", "PPE", "통제위계",
                          "occupational safety", "hazard", "personal protective equipment")),
    RBAItem("B-2", "HealthSafety", "비상사태 대비", "Emergency Preparedness",
            "정성",
            "비상상황 식별·평가, 비상대응계획·대피절차·경보·교육·훈련(연 1회 이상), 화재감지·소화설비, "
            "비상구 확보.",
            search_terms=("비상대응", "비상계획", "대피훈련", "소방", "화재감지", "비상구", "대피로",
                          "emergency preparedness", "fire", "evacuation drill")),
    RBAItem("B-3", "HealthSafety", "산업재해·질병", "Occupational Injury and Illness",
            "혼합",
            "산업재해·질병 예방·관리·추적·보고 체계, 근로자 보고 장려, 사례 분류·기록·치료·조사·시정, "
            "위험 작업 거부권.",
            search_terms=("산업재해", "재해율", "업무상 질병", "산재", "재해 기록", "아차사고",
                          "occupational injury", "illness", "incident rate", "near miss"),
            unit="건"),
    RBAItem("B-4", "HealthSafety", "산업위생", "Industrial Hygiene",
            "정성",
            "화학·생물·물리적 인자 노출 식별·평가·통제, PPE 무상 제공, 작업환경 모니터링, 직업건강 관리.",
            search_terms=("산업위생", "작업환경측정", "유해인자", "노출평가", "직업건강", "건강검진",
                          "industrial hygiene", "exposure", "occupational health monitoring")),
    RBAItem("B-5", "HealthSafety", "신체부담 작업", "Physically Demanding Work",
            "정성",
            "중량물 취급·반복작업·장시간 기립 등 신체부담 작업의 위험 식별·평가·통제.",
            search_terms=("근골격계", "중량물", "반복작업", "신체부담", "인간공학",
                          "physically demanding", "ergonomics", "manual handling")),
    RBAItem("B-6", "HealthSafety", "기계 안전장치", "Machine Safeguarding",
            "정성",
            "생산·기타 기계의 안전 위험 평가, 방호장치·인터록·방호벽 설치 및 유지.",
            search_terms=("기계안전", "방호장치", "안전장치", "인터록", "방호벽",
                          "machine safeguarding", "guard", "interlock")),
    RBAItem("B-7", "HealthSafety", "위생·식품·기숙사", "Sanitation, Food, and Housing",
            "정성",
            "청결한 화장실·식수·위생적 식품 시설 제공. 기숙사 제공 시 청결·안전·비상구·온수·환기·개인공간 확보.",
            search_terms=("위생", "화장실", "식수", "급식", "기숙사", "숙소", "샤워시설",
                          "sanitation", "potable water", "dormitory", "housing")),
    RBAItem("B-8", "HealthSafety", "안전보건 의사소통", "Health and Safety Communication",
            "정성",
            "근로자 언어로 안전보건 정보·교육 제공, 시설 내 게시, 작업 전·정기 교육, 보복 없는 의견제기.",
            search_terms=("안전보건교육", "안전정보 게시", "MSDS", "안전보건 의사소통",
                          "health and safety communication", "safety training", "posting")),
]

# ── C. 환경 (Environment) — 8개 ──────────────────────────────────────────
ENV_ITEMS = [
    RBAItem("C-1", "Environment", "환경 인허가·보고", "Environmental Permits and Reporting",
            "정성",
            "필요한 환경 인허가·승인·등록 취득·유지·갱신, 운영·보고 요건 준수.",
            search_terms=("환경인허가", "환경 허가", "배출시설 허가", "환경 등록", "환경 보고",
                          "environmental permit", "discharge permit", "reporting")),
    RBAItem("C-2", "Environment", "오염예방·자원절감", "Pollution Prevention and Resource Conservation",
            "혼합",
            "오염물질 배출·폐기물 발생을 원천 저감, 천연자원(물·화석연료·광물·목재) 절약(재사용·재활용 등).",
            search_terms=("오염예방", "자원절감", "원부자재 저감", "재활용", "재사용", "자원순환",
                          "pollution prevention", "resource conservation", "recycling")),
    RBAItem("C-3", "Environment", "유해물질", "Hazardous Substances",
            "정성",
            "인체·환경 유해 화학물질·폐기물 식별·표시·안전관리(취급·이동·보관·사용·폐기), 유해폐기물 데이터 추적.",
            search_terms=("유해물질", "유해화학물질", "화학물질관리", "유해폐기물", "MSDS",
                          "hazardous substance", "chemical management", "hazardous waste")),
    RBAItem("C-4", "Environment", "고형폐기물", "Solid Waste",
            "정량",
            "비유해 고형폐기물 식별·관리·감축·책임있는 처리/재활용을 위한 체계적 접근, 폐기물 데이터 추적. "
            "OEM 실사에서는 순환이용(재활용)률이 핵심 지표.",
            search_terms=("폐기물 재활용", "재활용률", "순환이용률", "고형폐기물", "폐기물 배출량",
                          "solid waste", "recycling rate", "waste"),
            metrics=(("E-6-2", "폐기물 재활용(순환이용)률", "%"),
                     ("E-6-1", "폐기물 배출량", "톤"))),
    RBAItem("C-5", "Environment", "대기배출", "Air Emissions",
            "정량",
            "VOC·에어로졸·부식성·입자상·오존층파괴물질·연소부산물 특성화·모니터링·통제·처리. 몬트리올 의정서 준수.",
            search_terms=("대기오염물질", "대기배출", "VOC", "오존층파괴물질", "NOx", "SOx", "먼지",
                          "air emissions", "ozone depleting"),
            unit="kg"),
    RBAItem("C-6", "Environment", "물질 규제", "Materials Restrictions",
            "정성",
            "제품·제조 내 특정 물질 금지·제한 관련 법규·고객 요구사항 준수(재활용·폐기 라벨링 포함). 예: RoHS/REACH.",
            search_terms=("물질규제", "함유물질", "RoHS", "REACH", "유해물질 규제", "제품 함유",
                          "materials restriction", "substance restriction")),
    RBAItem("C-7", "Environment", "물 관리", "Water Management",
            "혼합",
            "수원·사용·배출 문서화·특성화·모니터링, 절수 기회 발굴, 오염경로 통제. 폐수 처리·모니터링.",
            search_terms=("용수", "취수량", "물 관리", "폐수", "수자원", "재이용수", "수질",
                          "water management", "water use", "wastewater"),
            unit="ton"),
    RBAItem("C-8", "Environment", "에너지 소비·온실가스",
            "Energy Consumption and Greenhouse Gas Emissions",
            "정량",
            "전사 절대 온실가스 감축목표 수립·보고, 에너지 소비 및 Scope 1·2·주요 Scope 3 배출 추적·문서화·공개, "
            "에너지효율 개선.",
            search_terms=("온실가스", "탄소배출", "Scope 1", "Scope 2", "Scope 3", "에너지 사용량",
                          "재생에너지", "감축목표", "GHG", "carbon", "renewable energy"),
            metrics=(("E-3-1", "온실가스 배출량 (Scope 1+2)", "tCO2eq"),
                     ("E-4-1", "에너지 사용량", "TJ"),
                     ("E-4-2", "재생에너지 비율", "%"))),
]

# ── D. 윤리 (Ethics) — 8개 ───────────────────────────────────────────────
ETHICS_ITEMS = [
    RBAItem("D-1", "Ethics", "사업 청렴성", "Business Integrity",
            "정성",
            "모든 사업 활동에서 최고 수준의 청렴성, 뇌물·부패·갈취·횡령 무관용 정책.",
            search_terms=("청렴", "반부패", "뇌물", "부패방지", "윤리경영", "횡령", "갈취",
                          "business integrity", "anti-corruption", "bribery")),
    RBAItem("D-2", "Ethics", "부당이득 금지", "No Improper Advantage",
            "정성",
            "뇌물·부당이득 제공·수수 금지(직간접·제3자 포함), 반부패 준수를 위한 모니터링·기록·집행 절차.",
            search_terms=("부당이득", "금품수수", "접대", "리베이트", "이해충돌",
                          "improper advantage", "kickback", "facilitation payment")),
    RBAItem("D-3", "Ethics", "정보 공개", "Disclosure of Information",
            "정성",
            "노동·안전보건·환경·사업활동·구조·재무·성과 정보를 법규·업계관행에 따라 투명 공개, 기록 위조·허위표시 금지.",
            search_terms=("정보공개", "공시", "투명성", "기록 위조", "허위표시",
                          "disclosure", "transparency", "falsification")),
    RBAItem("D-4", "Ethics", "지식재산", "Intellectual Property",
            "정성",
            "지식재산권 존중, 기술·노하우 이전 시 IP 보호, 고객·공급사 정보 보호.",
            search_terms=("지식재산", "특허", "영업비밀", "기술보호", "IP",
                          "intellectual property", "trade secret")),
    RBAItem("D-5", "Ethics", "공정거래·광고·경쟁", "Fair Business, Advertising and Competition",
            "정성",
            "공정거래·광고·경쟁 기준 준수.",
            search_terms=("공정거래", "공정경쟁", "부당광고", "담합", "하도급",
                          "fair business", "fair competition", "antitrust")),
    RBAItem("D-6", "Ethics", "신원보호·보복금지", "Protection of Identity and Non-Retaliation",
            "정성",
            "내부고발자(공급사·임직원)의 비밀·익명·보호 프로그램 유지, 보복 두려움 없는 제보 절차.",
            search_terms=("내부고발", "신고제도", "제보자 보호", "윤리신고", "보복금지", "익명신고",
                          "whistleblower", "non-retaliation", "grievance")),
    RBAItem("D-7", "Ethics", "책임있는 광물 조달", "Responsible Sourcing of Minerals",
            "정성",
            "탄탈럼·주석·텅스텐·금(3TG)·코발트의 출처·이력 실사 정책, OECD 분쟁광물 실사지침 등 준수.",
            search_terms=("분쟁광물", "책임광물", "3TG", "코발트", "광물 실사", "CMRT",
                          "conflict minerals", "responsible minerals", "RMI", "cobalt")),
    RBAItem("D-8", "Ethics", "개인정보", "Privacy",
            "정성",
            "공급사·고객·소비자·임직원 개인정보의 합리적 프라이버시 기대 보호, 개인정보·정보보안 법규 준수.",
            search_terms=("개인정보", "프라이버시", "정보보호", "개인정보보호법", "정보보안",
                          "privacy", "personal information", "data protection")),
]

# ── E. 경영시스템 (Management Systems) — 12개 ────────────────────────────
MGMT_ITEMS = [
    RBAItem("E-1", "ManagementSystem", "회사의 의지표명", "Company Commitment",
            "정성",
            "인권·안전보건·환경·윤리 방침 성명(경영진 승인), 대외 공개 및 근로자 소통.",
            search_terms=("ESG 방침", "정책 성명", "경영방침", "인권방침", "지속가능경영 방침",
                          "policy statement", "company commitment")),
    RBAItem("E-2", "ManagementSystem", "경영 책임·권한", "Management Accountability and Responsibility",
            "정성",
            "경영시스템 이행 책임 임원·담당자 지정, 경영진 정기 검토.",
            search_terms=("경영책임", "ESG 거버넌스", "담당임원", "경영진 검토", "추진체계",
                          "management accountability", "responsibility")),
    RBAItem("E-3", "ManagementSystem", "법규·고객 요구사항", "Legal and Customer Requirements",
            "정성",
            "관련 법규·규정·고객 요구사항(본 행동규범 포함) 식별·모니터링·이해 프로세스.",
            search_terms=("법규준수", "컴플라이언스", "고객요구사항", "규제 모니터링",
                          "legal requirements", "compliance", "customer requirements")),
    RBAItem("E-4", "ManagementSystem", "리스크 평가·관리", "Risk Assessment and Risk Management",
            "정성",
            "법규·환경·안전보건·노동·윤리 리스크(중대 인권·환경 영향 포함) 식별·중요도 평가·통제.",
            search_terms=("리스크 평가", "위험관리", "ESG 리스크", "중대성 평가", "실사",
                          "risk assessment", "risk management", "due diligence")),
    RBAItem("E-5", "ManagementSystem", "개선목표", "Improvement Objectives",
            "정성",
            "사회·환경·안전보건 성과 개선을 위한 서면 목표·세부목표·이행계획 및 정기 점검.",
            search_terms=("개선목표", "성과목표", "이행계획", "KPI", "목표 수립",
                          "improvement objectives", "targets")),
    RBAItem("E-6", "ManagementSystem", "교육", "Training",
            "정성",
            "방침·절차·개선목표 이행 및 법규 충족을 위한 관리자·근로자 교육 프로그램.",
            search_terms=("교육 프로그램", "임직원 교육", "ESG 교육", "윤리교육",
                          "training", "education program")),
    RBAItem("E-7", "ManagementSystem", "의사소통", "Communication",
            "정성",
            "방침·관행·기대·성과를 근로자·공급사·고객에 명확·정확히 전달하는 프로세스.",
            search_terms=("의사소통", "이해관계자 소통", "정보전달", "커뮤니케이션",
                          "communication")),
    RBAItem("E-8", "ManagementSystem", "근로자·이해관계자 참여·구제접근",
            "Worker/Stakeholder Engagement and Access to Remedy",
            "정성",
            "근로자·대표·이해관계자와 양방향 소통, 보복 없는 고충·피드백 환경, 구제 접근 보장.",
            search_terms=("고충처리", "이해관계자 참여", "노사소통", "구제절차", "그리번스",
                          "grievance", "stakeholder engagement", "access to remedy")),
    RBAItem("E-9", "ManagementSystem", "감사·평가", "Audits and Assessments",
            "정성",
            "법규·행동규범·고객 계약 요건 적합성 정기 자가평가.",
            search_terms=("내부감사", "자가평가", "ESG 평가", "적합성 점검", "VAP",
                          "audit", "self-assessment", "VAP")),
    RBAItem("E-10", "ManagementSystem", "시정조치 프로세스", "Corrective Action Process",
            "정성",
            "내·외부 평가·점검·조사에서 발견된 미흡사항의 적시 시정 프로세스.",
            search_terms=("시정조치", "부적합 개선", "CAPA", "개선조치",
                          "corrective action", "CAPA")),
    RBAItem("E-11", "ManagementSystem", "문서·기록 관리", "Documentation and Records",
            "정성",
            "법규 준수·요건 적합 입증을 위한 문서·기록 생성·유지(프라이버시 보호 포함).",
            search_terms=("문서관리", "기록관리", "문서화", "이력관리",
                          "documentation", "records")),
    RBAItem("E-12", "ManagementSystem", "협력사 책임", "Supplier Responsibility",
            "정성",
            "공급사에 행동규범 요건 전달·준수 모니터링 프로세스(차상위 공급사 확장).",
            search_terms=("협력사 관리", "공급망 관리", "2차 협력사", "공급사 행동규범", "공급망 실사",
                          "supplier responsibility", "supply chain management", "tier-2")),
]


# ── K-ESG 크로스워크 (단일 출처) ─────────────────────────────────────────
# RBA 코드 → 동일 증빙을 끌어오는 K-ESG 코드. 미수록 코드는 빈 튜플(=RBA 고유 조항).
# 강한 매핑(직접 대응)과 우산 매핑(K-ESG 해상도 한계로 상위 항목에 귀속)이 섞여 있다.
_KESG_XWALK: dict[str, tuple[str, ...]] = {
    # A. 노동 — K-ESG 인권/노동/다양성에 귀속
    "A-1": ("S-5-1", "S-5-2"),          # 강제노동 ← 인권정책·인권 리스크 평가
    "A-2": ("S-5-1",),                  # 미성년 ← 인권정책
    # A-3 근로시간 — K-ESG에 대응 항목 없음
    "A-4": ("S-2-5",),                  # 임금·복리후생 ← 복리후생비
    "A-5": ("S-5-1", "S-3-1", "S-3-3"), # 비차별 ← 인권정책·여성비율·장애인고용
    "A-6": ("S-2-6",),                  # 결사의 자유 ← (직접 대응)
    # B. 안전보건 — K-ESG는 S-4-1/S-4-2 둘뿐 → 우산 매핑
    "B-1": ("S-4-1",),
    "B-2": ("S-4-1",),
    "B-3": ("S-4-2",),                  # 산업재해·질병 ← 산업재해율 (정량 공유)
    "B-4": ("S-4-1",),
    "B-5": ("S-4-1",),
    "B-6": ("S-4-1",),
    # B-7 위생·식품·기숙사 — K-ESG 대응 없음
    "B-8": ("S-4-1",),
    # C. 환경 — K-ESG E와 정량 data_point까지 공유
    "C-1": ("E-8-1",),                  # 인허가·보고 ← 환경 법규 준수
    "C-2": ("E-2-1", "E-2-2"),          # 오염예방·자원절감 ← 원부자재
    # C-3 유해물질 — 대응 없음
    "C-4": ("E-6-2", "E-6-1"),          # 고형폐기물 ← 재활용률(대표)·폐기물량. OEM 핵심+D1 검증 앵커.
    "C-5": ("E-7-1",),                  # 대기배출 ← 대기오염물질
    # C-6 물질규제(RoHS/REACH) — 대응 없음
    "C-7": ("E-5-1", "E-5-2", "E-7-2"), # 물 관리 ← 용수·재사용·수질
    "C-8": ("E-3-1", "E-3-2", "E-3-3", "E-4-1", "E-4-2"),  # GHG·에너지
    # D. 윤리 — K-ESG 윤리경영/공정거래/정보보호에 귀속
    "D-1": ("G-4-1",),                  # 청렴성 ← 윤리경영
    "D-2": ("G-4-1",),                  # 부당이득 ← 윤리경영
    "D-3": ("P-2-1",),                  # 정보공개 ← 공시 내용
    # D-4 지식재산 — 대응 없음
    "D-5": ("G-6-1",),                  # 공정거래 ← 지배구조 법규(공정거래 위반)
    "D-6": ("G-4-1",),                  # 신원보호·보복금지 ← 윤리경영(행동강령/신고)
    # D-7 분쟁광물 — 대응 없음
    "D-8": ("S-8-1", "S-8-2"),          # 개인정보 ← 정보보호 체계·침해 (직접 대응)
    # E. 경영시스템
    "E-1": ("S-5-1", "E-1-1"),          # 의지표명 ← 인권정책·환경경영 목표
    "E-2": ("E-1-2", "G-1-1"),          # 경영 책임 ← 추진체계·이사회 ESG 안건
    # E-3 법규·고객 요구사항 — 대응 없음
    "E-4": ("S-5-2",),                  # 리스크 평가 ← 인권 리스크 평가
    "E-5": ("S-1-1", "E-1-1"),          # 개선목표 ← 사회/환경 목표
    "E-6": ("S-2-4",),                  # 교육 ← 교육훈련비
    # E-7 의사소통 — 대응 없음
    "E-8": ("S-2-6",),                  # 참여·구제 ← 결사의 자유(노사소통)
    "E-9": ("G-5-1",),                  # 감사·평가 ← 내부감사
    # E-10 시정조치 / E-11 문서·기록 — 대응 없음
    "E-12": ("S-6-1", "S-6-3"),         # 협력사 책임 ← 협력사 ESG·지원 (직접 대응)
}


def _enrich(items: list[RBAItem]) -> list[RBAItem]:
    """기본 정의에 K-ESG 크로스워크를 주입(frozen → replace)."""
    return [replace(it, kesg_codes=_KESG_XWALK.get(it.code, ())) for it in items]


# ── 전체 레지스트리 ──────────────────────────────────────────────────────
RBA_ITEMS: list[RBAItem] = _enrich(
    LABOR_ITEMS + HS_ITEMS + ENV_ITEMS + ETHICS_ITEMS + MGMT_ITEMS
)

# code → RBAItem 인덱스
RBA_BY_CODE: dict[str, RBAItem] = {it.code: it for it in RBA_ITEMS}


def items_by_section(section: Section) -> list[RBAItem]:
    return [it for it in RBA_ITEMS if it.section == section]


def get_rba_item(code: str) -> RBAItem:
    try:
        return RBA_BY_CODE[code]
    except KeyError:
        raise KeyError(f"미등록 RBA 코드: '{code}'") from None


# ── 무결성 체크 ──────────────────────────────────────────────────────────
# v8.0 섹션별 조항 수(6/8/8/8/12 = 42)
_EXPECTED = {"Labor": 6, "HealthSafety": 8, "Environment": 8, "Ethics": 8, "ManagementSystem": 12}
assert len(RBA_ITEMS) == 42, f"RBA 항목 수 불일치: {len(RBA_ITEMS)} != 42"
for _sec, _n in _EXPECTED.items():
    _got = len(items_by_section(_sec))  # type: ignore[arg-type]
    assert _got == _n, f"{_sec} 항목 수 불일치: {_got} != {_n}"
# 크로스워크가 가리키는 K-ESG 코드는 실재해야 한다(오타 방지).
from .kesg_items import ALL_ITEMS as _KESG_ALL  # noqa: E402
_KESG_CODES = {it.code for it in _KESG_ALL}
for _rba, _codes in _KESG_XWALK.items():
    assert _rba in RBA_BY_CODE, f"_KESG_XWALK에 미등록 RBA 코드: {_rba}"
    for _c in _codes:
        assert _c in _KESG_CODES, f"존재하지 않는 K-ESG 코드 매핑: {_rba} -> {_c}"


# ============================================================================
# 텍스트 → RBA 코드 해소기 (clause 태깅용 — K-ESG resolver와 동일 하이브리드 패턴)
# ============================================================================
# RBA 고유 조항(근로시간·유해물질·분쟁광물·IP·개인정보 등)은 K-ESG 크로스워크가
# 없어 K-ESG 증빙풀에 안 걸린다. 업로드된 규정/매뉴얼의 조항 텍스트를 RBA
# search_terms로 결정적 매칭해 RBA 코드를 부여한다(없으면 None → insufficient 유지).
# 정규화·퍼지 헬퍼는 kesg_items의 것을 재사용한다(중복 방지).

from .kesg_items import _normalize_label, _jaccard  # noqa: E402


def _build_rba_alias_index() -> dict[str, set[str]]:
    idx: dict[str, set[str]] = {}
    for it in RBA_ITEMS:
        for term in (*it.search_terms, it.name_ko):
            n = _normalize_label(term)
            if len(n) >= 2:
                idx.setdefault(n, set()).add(it.code)
    return idx


_RBA_ALIAS_INDEX: dict[str, set[str]] = _build_rba_alias_index()
_RBA_ALIAS_UNIQUE: dict[str, str] = {
    n: next(iter(codes)) for n, codes in _RBA_ALIAS_INDEX.items() if len(codes) == 1
}


def resolve_rba_code(text: str, *, fuzzy_threshold: float = 0.66) -> tuple[str | None, float, str]:
    """조항 텍스트 → (RBA 코드 | None, 신뢰도, 방법: exact|fuzzy|none).

    clause는 문장이라 라벨보다 길다. 'search_term ⊆ 텍스트'(부분 포함)를 1차로 보고,
    가장 긴(=구체적인) 별칭을 채택한다. 단일 코드로 수렴하지 않으면 None(오부여 방지).
    """
    n = _normalize_label(text)
    if len(n) < 2:
        return (None, 0.0, "none")

    # 1) 텍스트가 알려진 RBA 별칭을 포함 — 가장 긴 별칭 채택
    sub = [(alias, code) for alias, code in _RBA_ALIAS_UNIQUE.items()
           if len(alias) >= 2 and alias in n]
    if sub:
        sub.sort(key=lambda x: len(x[0]), reverse=True)
        top_len = len(sub[0][0])
        winners = {code for alias, code in sub if len(alias) == top_len}
        if len(winners) == 1:
            return (sub[0][1], 0.95, "exact")

    # 2) 짧은 라벨류 입력 폴백 — 퍼지(모호하지 않을 때만)
    if len(n) <= 12:
        best_code, best_score, second = None, 0.0, 0.0
        for alias, code in _RBA_ALIAS_UNIQUE.items():
            sc = _jaccard(n, alias)
            if sc > best_score:
                best_code, best_score, second = code, sc, best_score
            elif sc > second:
                second = sc
        if best_code and best_score >= fuzzy_threshold and (best_score - second) >= 0.08:
            return (best_code, round(best_score, 3), "fuzzy")

    return (None, 0.0, "none")
