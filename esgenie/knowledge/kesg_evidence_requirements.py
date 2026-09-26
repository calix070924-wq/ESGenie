"""K-ESG 항목 → 증빙요구 / 도출유형 룩업 테이블 (STEP 2).

> 계획: `docs/다음작업_ESG커버리지_계획.md` STEP 2.

목적
----
각 K-ESG 항목이 **어떤 데이터 타입으로 풀리는지(derive_kind)** 와 **무엇을 올리면
풀리는지(evidence_types)** 를 선언적으로 둔다. NLP·LLM 검출이 아니라 순수 룩업이므로
싸고 결정적이다. STEP 3의 derive 분기와 insufficient/hitl_required 라우팅이 이 표를
*조회만* 해서 동작한다(두 번째 검출엔진 금지).

도출유형 (derive_kind) — 데이터 타입 기준 3갈래
------------------------------------------------
* ``quantitative``  : 정량 수치. 고지서·명세서·산정표로 값 확정 + D1 검증.
                      → 값+증빙 있으면 verified/self_reported, 없으면 insufficient.
* ``disclosure``    : 공시존재형. DART 정기공시 등 '공시했는가'로 풀림(사외이사·주총·감사기구).
                      → 공시 근거 있으면 verified, 없으면 insufficient.
* ``policy``        : 정성 내부정책. 방침서·인증서·규정 등 내부문서가 필요.
                      → ``human_narrative=False`` 면 문서 업로드로 풀림(insufficient),
                        ``human_narrative=True`` 면 증빙이 있어도 사람이 서술해야 함(hitl_required).

이 표는 *판정값을 정하지 않는다*. 어떤 경로로 판정할지와 보완/작성 안내문만 정한다.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from . import kesg_items

DeriveKind = Literal["quantitative", "disclosure", "policy"]


@dataclass(frozen=True)
class EvidenceRequirement:
    """한 K-ESG 항목을 무엇으로/어떻게 푸는가에 대한 선언."""
    code: str
    kind: DeriveKind
    # 올리면 항목이 풀리는 문서 유형(사람이 읽고 준비할 수 있게 구체적으로).
    evidence_types: tuple[str, ...]
    # insufficient/hitl 일 때 출력에 다는 구체적·실행가능한 안내문.
    request: str
    # policy 항목 중 증빙이 있어도 사람이 직접 서술해야 하는가(=hitl_required).
    human_narrative: bool = False

    @property
    def resolvable_by_evidence(self) -> bool:
        """증빙 업로드만으로 자동 해소 가능한가(= insufficient 경로)."""
        return not self.human_narrative


# ── BASIC_28 명시 매핑 ────────────────────────────────────────────────────────
# 자동(quantitative+disclosure) ~17 / 정성 policy ~11 — 계획서 분류와 일치.
_REQUIREMENTS: dict[str, EvidenceRequirement] = {
    # ── 정보공시 P ──
    "P-1-1": EvidenceRequirement(
        "P-1-1", "disclosure",
        ("지속가능경영보고서 또는 ESG 공시 페이지", "DART 정기공시 항목"),
        "ESG 정보를 어디에(보고서/홈페이지/DART) 공시하는지 확인 가능한 자료를 올려주세요.",
    ),
    # ── 환경 E (정량) ──
    "E-2-1": EvidenceRequirement(
        "E-2-1", "quantitative",
        ("원부자재 사용량 집계표", "구매·입고 명세서"),
        "연간 원부자재 총 사용량(톤)을 입증할 구매/입고 명세서 또는 집계표를 올려주세요.",
    ),
    "E-3-1": EvidenceRequirement(
        "E-3-1", "quantitative",
        ("Scope1·2 배출량 산정표", "전력·연료 사용 원천 증빙(고지서·명세서)"),
        "Scope1+2 온실가스 배출량(tCO2eq) 산정표와 전력·연료 사용 고지서를 올려주세요.",
    ),
    "E-4-1": EvidenceRequirement(
        "E-4-1", "quantitative",
        ("전기·가스 사용량 집계표", "에너지 사용 원천 증빙(고지서·계량기록)"),
        "연간 총 에너지 사용량(TJ) 집계표와 전기·가스 고지서를 올려주세요.",
    ),
    "E-4-2": EvidenceRequirement(
        "E-4-2", "quantitative",
        ("재생에너지 사용량 산정표", "REC·PPA·녹색프리미엄 계약/정산 증빙"),
        "재생에너지 사용 비율(%) 산정 근거와 REC/PPA/녹색프리미엄 증빙을 올려주세요.",
    ),
    "E-5-1": EvidenceRequirement(
        "E-5-1", "quantitative",
        ("용수 사용량 집계표", "상수도 요금 고지서·취수 계량기록"),
        "연간 취수량(ton)을 입증할 상수도 고지서 또는 취수 계량기록을 올려주세요.",
    ),
    "E-6-1": EvidenceRequirement(
        "E-6-1", "quantitative",
        ("폐기물 배출량 집계표", "폐기물 위탁처리 명세서(올바로 시스템)"),
        "연간 폐기물 배출량(톤)을 입증할 위탁처리 명세서 또는 집계표를 올려주세요.",
    ),
    "E-6-2": EvidenceRequirement(
        "E-6-2", "quantitative",
        ("폐기물 재활용량·총량 집계표", "재활용 위탁 명세서"),
        "폐기물 재활용 비율(%) 산정을 위해 재활용량과 총 배출량 증빙을 함께 올려주세요.",
    ),
    # ── 환경 E (정성 policy) ──
    "E-1-1": EvidenceRequirement(
        "E-1-1", "policy",
        ("환경경영 방침서", "중장기 환경목표·KPI 문서(이사회/경영진 승인)"),
        "환경경영 목표 수립을 입증할 환경방침서 또는 중장기 감축목표 문서를 올려주세요.",
    ),
    "E-1-2": EvidenceRequirement(
        "E-1-2", "policy",
        ("환경경영 조직도", "환경 전담조직·인력 운영 규정"),
        "환경경영 추진체계를 입증할 조직도 또는 전담조직 운영 규정을 올려주세요.",
    ),
    "E-3-3": EvidenceRequirement(
        "E-3-3", "policy",
        ("온실가스 제3자 검증의견서", "검증기관명·검증범위 명시 페이지"),
        "온실가스 배출량 검증을 입증할 제3자 검증의견서를 올려주세요.",
    ),
    # ── 사회 S (정량) ──
    "S-2-1": EvidenceRequirement(
        "S-2-1", "quantitative",
        ("신규채용·퇴사 인사대장", "4대보험 가입자 명부"),
        "신규 채용 인원·이직률을 입증할 인사대장 또는 4대보험 명부를 올려주세요.",
    ),
    "S-3-1": EvidenceRequirement(
        "S-3-1", "quantitative",
        ("성별 임직원 현황표", "직급별 성비 집계"),
        "전체 대비 여성 구성원 비율(%)을 입증할 성별 임직원 현황표를 올려주세요.",
    ),
    "S-4-2": EvidenceRequirement(
        "S-4-2", "quantitative",
        ("산업재해 발생 기록", "근로복지공단 산재 통계·재해율 산정표"),
        "산업재해율·사망만인율을 입증할 산재 발생 기록 또는 공단 통계를 올려주세요.",
    ),
    # ── 사회 S (정성 policy) ──
    "S-1-1": EvidenceRequirement(
        "S-1-1", "policy",
        ("사회책임·인권경영 방침", "사회공헌 목표 문서"),
        "사회적 책임 목표 수립을 입증할 사회책임 방침 또는 목표 문서를 올려주세요.",
    ),
    "S-2-6": EvidenceRequirement(
        "S-2-6", "policy",
        ("단체협약서", "노사협의회 운영규정·회의록"),
        "결사의 자유 보장을 입증할 단체협약서 또는 노사협의회 규정을 올려주세요.",
    ),
    "S-4-1": EvidenceRequirement(
        "S-4-1", "policy",
        ("안전보건 경영방침", "ISO 45001 인증서·안전보건 조직도"),
        "안전보건 추진체계를 입증할 안전보건 방침 또는 ISO 45001 인증서를 올려주세요.",
    ),
    "S-5-1": EvidenceRequirement(
        "S-5-1", "policy",
        ("인권정책서·인권헌장", "인권경영 선언문"),
        "인권정책 수립을 입증할 인권정책서 또는 인권헌장을 올려주세요.",
    ),
    "S-6-1": EvidenceRequirement(
        "S-6-1", "policy",
        ("협력사 행동규범(Code of Conduct)", "공급망 ESG 관리 정책"),
        "협력사 ESG 경영을 입증할 협력사 행동규범 또는 공급망 ESG 정책을 올려주세요.",
    ),
    "S-8-1": EvidenceRequirement(
        "S-8-1", "policy",
        ("정보보호 정책서", "ISMS-P 인증서·개인정보보호 내규"),
        "정보보호 시스템 구축을 입증할 정보보호 정책서 또는 ISMS-P 인증서를 올려주세요.",
    ),
    "S-7-1": EvidenceRequirement(
        "S-7-1", "policy",
        ("사회공헌 전략 문서", "지역사회 연계 프로그램 자료"),
        "전략적 사회공헌의 방향성·연계성을 담당자가 직접 서술해야 합니다(전략 서술 필요).",
        human_narrative=True,
    ),
    # ── 지배구조 G (정량) ──
    "G-1-2": EvidenceRequirement(
        "G-1-2", "quantitative",
        ("이사회 구성 현황(사외이사 수)", "DART 지배구조 공시"),
        "사외이사 비율(%)을 입증할 이사회 구성 현황 또는 DART 지배구조 공시를 확인하세요.",
    ),
    "G-2-1": EvidenceRequirement(
        "G-2-1", "quantitative",
        ("이사회 출석 현황표", "DART 이사회 활동 공시"),
        "전체 이사 출석률(%)을 입증할 이사회 출석 현황 또는 DART 공시를 확인하세요.",
    ),
    "G-3-4": EvidenceRequirement(
        "G-3-4", "quantitative",
        ("배당 내역", "DART 배당 공시·재무제표 주석"),
        "배당성향을 입증할 배당 내역 또는 DART 배당 공시를 확인하세요.",
    ),
    # ── 지배구조 G (공시존재형 disclosure) ──
    "G-1-1": EvidenceRequirement(
        "G-1-1", "disclosure",
        ("이사회 ESG 안건 상정 내역", "이사회 회의록 또는 보고자료"),
        "이사회 내 ESG 안건 상정을 확인할 이사회 회의록 또는 안건 상정 내역을 올려주세요.",
    ),
    "G-3-1": EvidenceRequirement(
        "G-3-1", "disclosure",
        ("주주총회 소집공고", "DART 주총 소집공고 공시"),
        "주주총회 소집 공고 시점·방식을 확인할 소집공고 또는 DART 공시를 올려주세요.",
    ),
    "G-5-1": EvidenceRequirement(
        "G-5-1", "disclosure",
        ("내부감사 조직 규정", "감사위원회/감사 설치 공시"),
        "내부감사부서 설치를 확인할 감사 조직 규정 또는 설치 공시를 올려주세요.",
    ),
    # ── 지배구조 G (정성 policy, 서술 필요) ──
    "G-4-1": EvidenceRequirement(
        "G-4-1", "policy",
        ("윤리규범·행동강령", "윤리경영 위반 신고·공시 절차 문서"),
        "윤리규범 위반사항 공시 체계와 당해 위반 유무를 담당자가 직접 서술해야 합니다.",
        human_narrative=True,
    ),
}

# ── RBA 고유 10항목 (K-ESG 크로스워크 없음) ──────────────────────────────────
# K-ESG에 대응 항목이 없는 RBA 고유 조항의 증빙 안내. 대부분 정성 내부정책이므로
# kind="policy"이며, 증빙 업로드로 자동 해소된다(human_narrative=False).
_RBA_REQUIREMENTS: dict[str, EvidenceRequirement] = {
    "A-3": EvidenceRequirement(
        "A-3", "policy",
        ("근로시간 관리 규정", "연장근로 동의서 양식", "출퇴근·근태 관리 시스템 캡처"),
        "주 60시간(연장 포함) 근로시간 상한 준수를 입증할 근로시간 관리 규정, "
        "연장근로 자발적 동의서 양식, 주간 근태 집계 자료를 올려주세요.",
    ),
    "B-7": EvidenceRequirement(
        "B-7", "policy",
        ("위생·급식 관리 규정", "기숙사 안전점검 체크리스트", "식품위생 점검 기록"),
        "청결한 화장실·식수·급식 시설과 기숙사(제공 시) 안전·위생 관리를 입증할 "
        "위생관리 규정, 기숙사 점검기록, 식품위생 점검표를 올려주세요.",
    ),
    "C-3": EvidenceRequirement(
        "C-3", "policy",
        ("유해화학물질 관리 대장", "MSDS 비치 현황", "유해폐기물 위탁처리 계약서"),
        "유해물질 식별·표시·안전관리 체계를 입증할 화학물질 관리대장, "
        "MSDS 비치 현황표, 유해폐기물 처리 계약서를 올려주세요.",
    ),
    "C-6": EvidenceRequirement(
        "C-6", "policy",
        ("제품 함유물질 관리 규정", "RoHS/REACH 적합성 시험 성적서", "고객사 물질규제 준수 대응 문서"),
        "제품 내 규제물질(RoHS/REACH 등) 관리 체계를 입증할 함유물질 관리 규정, "
        "적합성 시험성적서, 고객사 물질규제 대응 문서를 올려주세요.",
    ),
    "D-4": EvidenceRequirement(
        "D-4", "policy",
        ("지식재산 보호 규정", "영업비밀 관리 지침", "비밀유지서약서(NDA) 양식"),
        "지식재산·영업비밀 보호 체계를 입증할 IP 보호 규정, "
        "영업비밀 관리 지침, 비밀유지서약서(NDA) 양식을 올려주세요.",
    ),
    "D-7": EvidenceRequirement(
        "D-7", "policy",
        ("책임광물 실사 정책서", "CMRT(분쟁광물 보고서)", "3TG·코발트 공급망 실사 결과"),
        "책임있는 광물 조달(3TG·코발트) 실사 체계를 입증할 분쟁광물 정책서, "
        "CMRT 보고서, 공급망 실사 결과 문서를 올려주세요.",
    ),
    "E-3": EvidenceRequirement(
        "E-3", "policy",
        ("법규 준수 관리 대장", "컴플라이언스 모니터링 절차서", "고객 요구사항 등록·추적 대장"),
        "관련 법규·고객 요구사항(RBA 행동규범 포함) 식별·모니터링 체계를 입증할 "
        "법규 관리 대장, 컴플라이언스 절차서, 고객 요구사항 추적 대장을 올려주세요.",
    ),
    "E-7": EvidenceRequirement(
        "E-7", "policy",
        ("이해관계자 의사소통 절차서", "근로자 공지·게시 기록", "공급사·고객 소통 채널 운영 현황"),
        "방침·관행·기대·성과를 근로자·공급사·고객에 전달하는 프로세스를 입증할 "
        "의사소통 절차서, 공지 기록, 소통 채널 운영 현황을 올려주세요.",
    ),
    "E-10": EvidenceRequirement(
        "E-10", "policy",
        ("시정조치(CAPA) 절차서", "내부감사 부적합 시정 기록", "시정조치 이행 확인서"),
        "내·외부 평가에서 발견된 미흡사항의 시정 프로세스를 입증할 "
        "CAPA 절차서, 부적합 시정 기록, 이행 확인서를 올려주세요.",
    ),
    "E-11": EvidenceRequirement(
        "E-11", "policy",
        ("문서·기록 관리 규정", "문서 보존 기한 목록", "기록 관리 시스템 운영 현황"),
        "법규 준수 입증을 위한 문서·기록 관리 체계를 입증할 "
        "문서관리 규정, 보존기한 목록, 기록관리 시스템 현황을 올려주세요.",
    ),
    # ── 크로스워크가 문항 의미와 어긋나 명시가 필요한 조항(2026-09-20) ──
    # C-1·D-5는 정성 조항인데 크로스워크 코드(E-8-1 환경 법규 위반건수 /
    # G-6-1 공정거래 위반건수)가 정량이라, 폴백이 '고지서·명세서·산정표'를 안내했다.
    # 위반 '건수'를 묻는 K-ESG 코드와 '인허가를 갖췄는가/공정거래 기준을 지키는가'를
    # 묻는 RBA 조항은 요구 증빙이 다르다. 여기서 조항 자신의 요구를 명시해 둔다.
    "C-1": EvidenceRequirement(
        "C-1", "policy",
        ("환경 인허가증·신고 수리증", "배출시설·방지시설 설치 허가/신고 서류",
         "인허가 갱신 이력", "법정 환경 보고(배출량·폐기물 실적) 제출 기록"),
        "필요한 환경 인허가·승인·등록의 취득·유지·갱신을 입증할 인허가증, "
        "배출·방지시설 허가/신고 서류, 갱신 이력, 법정 보고 제출 기록을 올려주세요.",
    ),
    "C-5": EvidenceRequirement(
        "C-5", "policy",
        ("대기오염물질 배출시설 허가/신고 서류", "방지시설 운영·점검 기록",
         "VOC·오존층파괴물질 취급 관리 대장", "대기 자가측정 계획·성적서"),
        "대기배출 특성화·모니터링·통제·처리 체계를 입증할 배출시설 허가 서류, "
        "방지시설 운영·점검 기록, 유해대기물질 관리 대장, 자가측정 성적서를 올려주세요. "
        "배출량 수치 자체는 별도 수치 문항에서 요구합니다.",
    ),
    "D-5": EvidenceRequirement(
        "D-5", "policy",
        ("공정거래 자율준수 방침·프로그램(CP) 규정", "하도급·구매 표준계약 약관",
         "광고·표시 심의 절차서", "임직원 공정거래 교육 기록"),
        "공정거래·광고·경쟁 기준 준수를 입증할 공정거래 자율준수 방침(CP), "
        "하도급·구매 표준계약 약관, 광고·표시 심의 절차서, 공정거래 교육 기록을 올려주세요.",
    ),
    # 아래 조항들도 크로스워크가 정량이라 같은 결함을 공유했다. 조항 차원 문항은
    # '관리체계가 있는가'를 묻는다 — 수치 자체는 지표 수치행(metrics)이나 K-ESG
    # 문항이 따로 요구한다. 유형 기본 폴백('관련 내부 방침서…')으로 두면 문항이
    # 무엇을 요구하는지 사라지므로 조항별로 적는다.
    "A-4": EvidenceRequirement(
        "A-4", "policy",
        ("임금·수당 지급 규정", "임금명세서 양식(항목·공제 내역 표시)",
         "연장·휴일근로 수당 산정 기준", "법정 복리후생 가입 증명(4대보험 등)"),
        "최저임금·연장수당·법정 복리후생 준수와 명확한 임금명세서 제공을 입증할 "
        "임금 규정, 임금명세서 양식, 수당 산정 기준, 사회보험 가입 증명을 올려주세요.",
    ),
    "B-3": EvidenceRequirement(
        "B-3", "policy",
        ("산업재해·질병 관리 절차서", "사고·아차사고 보고·조사 기록",
         "산업재해 발생 대장 및 시정조치 이행 기록", "작업중지권(위험작업 거부) 규정"),
        "산업재해·질병의 예방·기록·조사·시정과 근로자 보고 장려 체계를 입증할 "
        "재해관리 절차서, 사고 조사·시정 기록, 재해 발생 대장, 작업중지권 규정을 올려주세요.",
    ),
    "C-2": EvidenceRequirement(
        "C-2", "policy",
        ("오염예방·자원절감 추진 계획서", "원부자재·용수·에너지 절감 활동 실적",
         "재사용·재활용 운영 기준", "공정 배출·폐기물 원천 저감 개선 기록"),
        "오염물질·폐기물의 원천 저감과 천연자원 절약 체계를 입증할 오염예방·자원절감 "
        "계획서, 절감 활동 실적, 재사용·재활용 운영 기준을 올려주세요.",
    ),
    "C-4": EvidenceRequirement(
        "C-4", "policy",
        ("폐기물 관리 절차서", "폐기물 위탁처리 계약서·인계서(올바로 실적)",
         "폐기물 배출·재활용 데이터 추적 대장", "폐기물 감축 목표·실적"),
        "비유해 고형폐기물의 식별·감축·책임있는 처리 체계를 입증할 폐기물 관리 "
        "절차서, 위탁처리 계약·인계서, 배출·재활용 추적 대장을 올려주세요. "
        "재활용률·배출량 수치는 별도 수치 문항에서 요구합니다.",
    ),
    "C-7": EvidenceRequirement(
        "C-7", "policy",
        ("용수 관리 절차서(수원·사용·배출 문서화)", "폐수 처리시설 운영·수질 측정 기록",
         "절수 개선 활동 기록", "수질 오염경로 점검 기록"),
        "수원·사용·배출의 문서화와 폐수 처리·모니터링 체계를 입증할 용수 관리 "
        "절차서, 폐수 수질 측정 기록, 절수 활동 기록을 올려주세요. "
        "사용기간·사업장·단위가 명시된 취수량·재이용 기록을 함께 첨부해 주세요.",
    ),
    "C-8": EvidenceRequirement(
        "C-8", "policy",
        ("온실가스 감축목표 문서(기준연도·목표연도 포함)", "에너지·배출량 추적 관리 대장",
         "Scope 1·2(주요 Scope 3) 산정 방법·경계 문서", "에너지효율 개선 활동 기록"),
        "절대 온실가스 감축목표 수립·보고와 에너지·배출량 추적 체계를 입증할 "
        "감축목표 문서, 산정 경계·방법 문서, 추적 관리 대장, 효율 개선 기록을 "
        "올려주세요. 배출량·에너지·재생에너지 수치는 별도 수치 문항에서 요구합니다.",
    ),
    "E-6": EvidenceRequirement(
        "E-6", "policy",
        ("연간 교육 계획서(대상·과정·시간)", "관리자·근로자 교육 실시 기록·수료 명부",
         "교육 교재·커리큘럼", "교육 효과 평가 기록"),
        "방침·절차·개선목표 이행과 법규 충족을 위한 관리자·근로자 교육 체계를 "
        "입증할 연간 교육계획서, 교육 실시 기록·수료 명부, 교재를 올려주세요.",
    ),
}
_REQUIREMENTS.update(_RBA_REQUIREMENTS)

# ── BASIC_28 외 K-ESG 코드 중 실제 문항이 쓰는 것만 명시 ──────────────────────
# 수평 카탈로그를 미리 만들지 않는다는 원칙은 유지한다. 여기 있는 것은 양식 문항이
# 실제로 요구하는 코드로, 정량 폴백 문구('고지서·명세서')가 그 수치의 실제 증빙과
# 맞지 않아 명시가 필요했던 경우다.
_EXTRA_KESG_REQUIREMENTS: dict[str, EvidenceRequirement] = {
    "E-7-1": EvidenceRequirement(
        "E-7-1", "quantitative",
        ("대기오염물질 배출량 산정표", "대기 자가측정 성적서", "굴뚝 자동측정(CEMS) 기록"),
        "연간 대기오염물질 배출량(kg)을 입증할 자가측정 성적서 또는 "
        "배출량 산정표(측정 기간·대상 물질 포함)를 올려주세요.",
    ),
}
_REQUIREMENTS.update(_EXTRA_KESG_REQUIREMENTS)

# 정성 항목 기본 안내(미등재 코드의 policy 폴백에 사용).
_DEFAULT_POLICY_REQUEST = "관련 내부 방침서·규정·인증서를 올리면 자동으로 검토됩니다."
_DEFAULT_QUANT_REQUEST = "해당 수치를 입증할 고지서·명세서·산정표를 올려주세요."


def requirement_for(code: str) -> EvidenceRequirement:
    """코드의 증빙요구를 반환. 미등재 코드는 data_type 기반 기본값으로 합성한다.

    수평 카탈로그를 미리 대량 생산하지 않기 위해(계획서 '하지 말 것') BASIC_28만
    명시하고, 나머지 61항목은 정량→quantitative / 그 외→policy 로 안전하게 폴백한다.
    """
    explicit = _REQUIREMENTS.get(code)
    if explicit is not None:
        return explicit

    item = kesg_items.by_code(code)
    if item is not None and item.data_type == "정량":
        return EvidenceRequirement(
            code, "quantitative", ("수치 산정표", "원천 증빙(고지서·명세서)"),
            _DEFAULT_QUANT_REQUEST,
        )
    return EvidenceRequirement(
        code, "policy", ("관련 내부 방침서·규정·인증서",),
        _DEFAULT_POLICY_REQUEST,
    )


def default_requirement(code: str, *, quantitative: bool) -> EvidenceRequirement:
    """유형 기본 증빙요구. 명시 요구도, 유형이 맞는 코드 폴백도 없을 때 쓴다."""
    if quantitative:
        return EvidenceRequirement(
            code, "quantitative", ("수치 산정표", "원천 증빙(고지서·명세서)"),
            _DEFAULT_QUANT_REQUEST,
        )
    return EvidenceRequirement(
        code, "policy", ("관련 내부 방침서·규정·인증서",), _DEFAULT_POLICY_REQUEST,
    )


def requirement_for_question(
    codes: tuple[str, ...] | list[str], *, quantitative: bool,
) -> EvidenceRequirement:
    """문항의 증빙요구 — 코드 하나가 아니라 '문항'을 기준으로 고른다.

    규칙 순서(요구 순서를 한 곳에 고정한다)
      1. 문항이 매칭하는 코드들 중 **명시 등록**되고 문항 유형과 맞는 요구
      2. 그것이 없으면 코드 폴백 중 문항 유형과 맞는 것
      3. 그것도 없으면 문항 유형 기본값

    왜 코드가 아니라 문항인가: 문항의 primary_code는 K-ESG 크로스워크 코드가 먼저
    온다(rba_self._match_codes). 그래서 정성 조항인 D-5(공정거래)·C-1(환경 인허가)이
    위반 건수 코드(G-6-1·E-8-1)의 정량 폴백을 물려받아 "고지서·명세서·산정표를
    올려주세요"를 안내했다(2026-09-20 HMC 응답서 실측). 반대 방향도 막아야 한다 —
    K-ESG 원래 수치 문항은 정량 안내를 그대로 받아야 하므로, 유형이 맞는 정량 요구를
    정성 문구로 덮지 않는다.
    """
    def fits(req: EvidenceRequirement) -> bool:
        return (req.kind == "quantitative") == quantitative

    for code in codes:
        explicit = _REQUIREMENTS.get(code)
        if explicit is not None and fits(explicit):
            return explicit
    for code in codes:
        req = requirement_for(code)
        if fits(req):
            return req
    return default_requirement(codes[0] if codes else "", quantitative=quantitative)


def derive_kind_for(code: str) -> DeriveKind:
    """STEP 3 derive 분기 진입점 — 코드의 도출유형."""
    return requirement_for(code).kind


# ── 무결성 가드 ───────────────────────────────────────────────────────────────
# 명시 매핑은 실재하는 K-ESG 코드 또는 등록된 RBA 고유 코드여야 한다.
from .rba_items import RBA_BY_CODE as _RBA_BY_CODE  # noqa: E402
assert all(
    kesg_items.by_code(c) is not None or c in _RBA_BY_CODE
    for c in _REQUIREMENTS
), (
    "kesg_evidence_requirements에 존재하지 않는 코드가 있습니다."
)
# BASIC_28은 전부 명시 매핑되어 있어야 한다(폴백에 의존하지 않음).
assert set(kesg_items.BASIC_28_CODES) <= set(_REQUIREMENTS), (
    "BASIC_28 중 증빙요구 매핑이 누락된 코드가 있습니다: "
    f"{sorted(set(kesg_items.BASIC_28_CODES) - set(_REQUIREMENTS))}"
)
