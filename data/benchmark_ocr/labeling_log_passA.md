# Pass A Labeling Log

**Labeler**: claude-code-passA  
**Date**: 2026-07-17  
**Method**: pymupdf text extraction only. No LLM outputs or eval_results consulted.

---

## 1. work_hours_2025 (근로시간관리규정_2025.pdf)

**doc_type**: policy_manual  
**Rationale**: Internal regulation document (규정) governing work hours, not meeting minutes or emergency manual.

**metrics_gold**: [] (empty)  
**Rationale**: All numbers (60h, 40h, 12h) are policy limits/rules, not measured ESG performance indicators.

### Facts

| ID | Fact | Source passage |
|----|------|---------------|
| F1 | RBA 행동규범 v8.0 및 근로기준법에 따라 근로시간을 관리한다 | 제1조: "본 규정은 RBA 행동규범 v8.0 및 근로기준법에 따라 주 60시간(연장 포함) 근로시간 상한을 준수하고..." |
| F2 | 주간 총 근로시간은 연장근로 포함 60시간을 초과할 수 없다 | 제2조 1항: "주간 총 근로시간은 연장근로 포함 60시간을 초과할 수 없다." |
| F3 | 법정 기준근로시간은 주 40시간이다 | 제2조 2항: "법정 기준근로시간은 주 40시간으로 한다." |
| F4 | 연장근로는 주 12시간을 초과하지 아니한다 | 제2조 3항: "연장근로는 주 12시간을 초과하지 아니한다." |
| F5 | 7일 중 최소 1일의 휴일을 보장한다 | 제3조 1항: "7일 중 최소 1일의 휴일을 보장한다." |
| F6 | 모든 연장근로는 근로자의 자발적 서면 동의를 얻어야 한다 | 제4조 1항: "모든 연장근로는 근로자의 자발적 서면 동의를 얻어야 한다." |
| F7 | 전자 출퇴근 시스템으로 일 단위 근로시간을 기록한다 | 제5조 1항: "전자 출퇴근 시스템으로 일 단위 근로시간을 기록한다." |
| F8 | 주간 60시간 초과 경고 알림을 시행한다 | 제5조 2항: "주간 60시간 초과 경고 알림을 시행한다." |

**Difference from previous gold**: Added F4 (연장근로 주 12시간 상한) which was present in source but missing from prior gold.

---

## 2. doc_record_mgmt_2025 (문서기록관리규정_2025.pdf)

**doc_type**: policy_manual  
**Rationale**: Internal regulation governing document and record management.

**metrics_gold**: [] (empty)  
**Rationale**: No measured performance values. Retention periods are policy rules.

### Facts

| ID | Fact | Source passage |
|----|------|---------------|
| F1 | 법규 준수·요건 적합성 입증을 위한 문서와 기록을 체계적으로 관리한다 | 제1조: "법규 준수·요건 적합성 입증을 위한 문서와 기록을 체계적으로 생성·유지·보존·폐기한다." |
| F2 | 정책문서(방침서·규정·절차서)는 5년 보존한다 | 제2조 1항: "정책문서: 방침서·규정·절차서(5년 보존)" |
| F3 | 운영기록(점검표·회의록·교육기록)은 3년 보존한다 | 제2조 2항: "운영기록: 점검표·회의록·교육기록(3년 보존)" |
| F4 | 법정기록(인허가·인증서·시험성적서)은 10년 보존한다 | 제2조 3항: "법정기록: 인허가·인증서·시험성적서(10년 보존)" |
| F5 | 전자문서관리시스템(EDMS)으로 통합 관리한다 | 제3조 1항: "전자문서관리시스템(EDMS)으로 통합 관리한다." |
| F6 | 문서 열람 권한은 직무별로 차등 부여한다 | 제4조 1항: "문서 열람 권한은 직무별로 차등 부여한다." |
| F7 | 기밀문서는 암호화 보관·접근이력 로깅한다 | 제4조 2항: "기밀문서는 암호화 보관·접근이력 로깅한다." |
| F8 | 보존기한 만료 문서는 폐기 승인 후 파쇄 처리한다 | 제5조: "보존기한 만료 문서는 폐기 승인 후 파쇄 처리한다." |

**Difference from previous gold**: Added F3 (운영기록 3년 보존) and F8 (폐기 절차) which were in source but missing from prior gold.

---

## 3. rohs_reach_2025 (물질규제_RoHS_REACH_2025.pdf)

**doc_type**: policy_manual  
**Rationale**: Substance regulation policy document.

**metrics_gold**: [] (empty)  
**Rationale**: No measured ESG performance values.

### Facts

| ID | Fact | Source passage |
|----|------|---------------|
| F1 | 제품 내 규제물질(RoHS 10종, REACH SVHC)의 함유를 관리한다 | 목적: "제품 내 규제물질(RoHS 10종, REACH SVHC)의 함유를 관리하고..." |
| F2 | EU RoHS 지침(2011/65/EU) 및 REACH 규칙(1907/2006)을 준수한다 | 규제 기준: "EU RoHS 지침 (2011/65/EU, 위임지침 포함)" + "EU REACH 규칙 (1907/2006) SVHC 후보물질" |
| F3 | 원자재 입고 시 성분분석 성적서(ICP-OES)를 확인한다 | 3.1: "원자재 입고 시 성분분석 성적서(ICP-OES) 확인" |
| F4 | 연 1회 완제품 RoHS 적합성 시험을 공인기관에서 실시한다 | 3.2: "연 1회 완제품 RoHS 적합성 시험(공인기관)" |
| F5 | REACH SVHC 후보물질 갱신을 반기 1회 모니터링한다 | 3.3: "REACH SVHC 후보물질 갱신(반기 1회) 모니터링" |
| F6 | 부적합 시 대체물질 전환 계획을 수립한다 | 3.4: "부적합 시 대체물질 전환 계획 수립" |
| F7 | 원자재 공급사는 불검출 보증서를 제출해야 한다 | 4. 공급사 의무: "원자재 공급사는 불검출 보증서를 제출해야 한다." |
| F8 | 시험성적서·불검출 보증서는 출하일로부터 10년 보존한다 | 5. 기록: "시험성적서·불검출 보증서는 출하일로부터 10년 보존." |

**Difference from previous gold**: Added F2 (규제 기준 근거 법령 명시) and F6 (부적합 시 대체물질 전환).

---

## 4. capa_2025 (시정조치_CAPA절차서_2025.pdf)

**doc_type**: policy_manual  
**Rationale**: CAPA procedure document (절차서).

**metrics_gold**: [] (empty)  
**Rationale**: No measured values. Timelines (24h, 30d, 14d) are procedural deadlines.

### Facts

| ID | Fact | Source passage |
|----|------|---------------|
| F1 | 내·외부 감사·평가에서 발견된 부적합 사항을 시정하고 재발을 방지한다 | 1. 목적: "내·외부 감사·평가에서 발견된 부적합 사항을 적시에 시정하고 재발을 방지하기 위한 절차를 규정한다." |
| F2 | 품질·환경·안전보건·윤리 분야 모든 부적합 사항에 적용한다 | 2. 적용 범위: "품질·환경·안전보건·윤리 분야 모든 부적합 사항" |
| F3 | 부적합 접수 후 24시간 이내 CAPA 등록한다 | 3.1: "부적합 접수 → CAPA 등록(24시간 이내)" |
| F4 | 근본원인 분석에 5-Why, Fish-bone 기법을 사용한다 | 3.2: "근본원인 분석(5-Why, Fish-bone)" |
| F5 | 유효성 검증은 30일 후 재발 여부로 확인한다 | 3.5: "유효성 검증(30일 후 재발 여부 확인)" |
| F6 | 시한: 경미 30일 이내, 중대 14일 이내, 긴급 즉시 | 4. 시한: "경미: 30일 이내 / 중대: 14일 이내 / 긴급: 즉시" |
| F7 | CAPA 기록은 최소 5년 보존한다 | 5. 기록 보존: "CAPA 기록은 최소 5년 보존한다." |
| F8 | 이행 완료 건은 경영검토 회의에 보고한다 | 5. 기록 보존: "이행 완료 건은 경영검토 회의에 보고한다." |

**Difference from previous gold**: Added F2 (적용 범위) and F8 (경영검토 보고).

---

## 5. sanitation_housing_2025 (위생_기숙사관리규정_2025.pdf)

**doc_type**: policy_manual  
**Rationale**: Sanitation, food, and housing management regulation.

**metrics_gold**: [] (empty)  
**Rationale**: Numbers (30인당, 4.5㎡) are facility standards, not measured performance.

### Facts

| ID | Fact | Source passage |
|----|------|---------------|
| F1 | RBA B-7(Sanitation, Food, Housing)에 따라 위생 환경을 확보한다 | 제1조: "본 규정은 RBA B-7(Sanitation, Food, Housing)에 따라 사업장 내 청결·위생 환경과 기숙사 안전을 확보함을 목적으로 한다." |
| F2 | 화장실은 남녀 구분, 30인당 1개소 이상 설치한다 | 제2조 1항: "화장실은 남녀 구분, 30인당 1개소 이상 설치한다." |
| F3 | 음용 가능한 깨끗한 식수를 상시 제공한다 | 제2조 2항: "음용 가능한 깨끗한 식수를 상시 제공한다." |
| F4 | 구내식당은 식품위생법 기준을 준수한다 | 제3조 1항: "구내식당 운영 시 식품위생법 기준을 준수한다." |
| F5 | 기숙사는 1인당 최소 4.5㎡ 이상 개인공간을 확보한다 | 제4조 1항: "기숙사 제공 시 1인당 최소 4.5㎡ 이상 개인공간을 확보한다." |
| F6 | 비상구 2개소 이상, 소화기·감지기를 층별 비치한다 | 제4조 2항: "비상구 2개소 이상, 소화기·감지기를 층별 비치한다." |
| F7 | 입·퇴실 자유를 보장하며 신분증 보관을 금지한다 | 제4조 4항: "입·퇴실 자유를 보장하며, 신분증 보관을 금지한다." |
| F8 | 식중독 예방 교육을 연 2회 실시한다 | 제3조 3항: "식중독 예방 교육을 연 2회 실시한다." |

**Difference from previous gold**: Added F3 (식수 제공). Source explicitly states this requirement.

---

## 6. hazmat_2025 (유해물질관리규정_2025.pdf)

**doc_type**: policy_manual  
**Rationale**: Hazardous substance management regulation.

**metrics_gold**: [] (empty)  
**Rationale**: No measured ESG performance indicators.

### Facts

| ID | Fact | Source passage |
|----|------|---------------|
| F1 | 유해화학물질의 식별·표시·보관·취급·폐기 전 과정을 안전하게 관리한다 | 제1조: "유해화학물질의 식별·표시·보관·취급·폐기 전 과정을 안전하게 관리하여..." |
| F2 | 모든 화학물질은 MSDS를 비치한다 | 제3조 1항: "모든 화학물질은 MSDS를 비치한다." |
| F3 | 신규 물질 도입 시 EHS팀 사전 승인을 받는다 | 제3조 2항: "신규 물질 도입 시 EHS팀 사전 승인을 받는다." |
| F4 | 연 1회 MSDS 최신본 갱신 여부를 점검한다 | 제3조 3항: "연 1회 MSDS 최신본 갱신 여부를 점검한다." |
| F5 | 유해물질 보관구역은 잠금·환기·누출방지턱을 구비한다 | 제4조 1항: "유해물질 보관구역은 잠금·환기·누출방지턱을 구비한다." |
| F6 | 취급 시 지정 보호구(내화학 장갑·고글·방독면)를 착용한다 | 제4조 2항: "취급 시 지정 보호구(내화학 장갑·고글·방독면)를 착용한다." |
| F7 | 유해폐기물은 허가 업체를 통해 법정 절차에 따라 위탁 처리한다 | 제5조 2항: "허가 업체를 통해 법정 절차에 따라 위탁 처리한다." |
| F8 | 처리 이력을 5년간 보관한다 | 제5조 3항: "처리 이력을 5년간 보관한다." |

**Difference from previous gold**: Added F4 (MSDS 갱신 점검) and F6 (보호구 착용 의무).

---

## 7. ip_protection_2025 (지식재산보호규정_2025.pdf)

**doc_type**: policy_manual  
**Rationale**: Intellectual property protection regulation.

**metrics_gold**: [] (empty)  
**Rationale**: No measured ESG performance indicators.

### Facts

| ID | Fact | Source passage |
|----|------|---------------|
| F1 | 회사 및 거래처의 지식재산권, 영업비밀, 기술 노하우를 보호한다 | 제1조: "본 규정은 회사 및 거래처의 지식재산권, 영업비밀, 기술 노하우를 보호하는 것을 목적으로 한다." |
| F2 | 전 임직원은 입사 시 비밀유지서약서(NDA)에 서명한다 | 제3조 1항: "전 임직원은 입사 시 비밀유지서약서(NDA)에 서명한다." |
| F3 | 퇴직 후 2년간 비밀유지 의무를 부담한다 | 제3조 2항: "퇴직 후 2년간 비밀유지 의무를 부담한다." |
| F4 | 외부 방문자는 방문 전 기밀유지 동의서를 작성한다 | 제3조 3항: "외부 방문자는 방문 전 기밀유지 동의서를 작성한다." |
| F5 | 거래처 기술정보의 무단 복제·유출을 금지한다 | 제4조 1항: "거래처 기술정보의 무단 복제·유출을 금지한다." |
| F6 | 기술 이전 시 쌍방 서면 계약을 체결한다 | 제4조 2항: "기술 이전 시 쌍방 서면 계약을 체결한다." |
| F7 | 지식재산 침해 시 즉시 징계 및 손해배상 청구가 가능하다 | 제5조 1-2항: "지식재산 침해 시 즉시 징계 조치한다." + "손해배상 청구 및 형사 고발할 수 있다." |

**Difference from previous gold**: Added F6 (기술 이전 서면 계약).

---

## 8. responsible_minerals_2025 (책임광물실사정책_2025.pdf)

**doc_type**: policy_manual  
**Rationale**: Responsible minerals due diligence policy document.

**metrics_gold**: [] (empty)  
**Rationale**: No measured ESG performance indicators.

### Facts

| ID | Fact | Source passage |
|----|------|---------------|
| F1 | 3TG(탄탈럼·주석·텅스텐·금) 및 코발트의 공급망을 투명하게 관리한다 | 1. 목적: "탄탈럼(Ta)·주석(Sn)·텅스텐(W)·금(Au)(3TG) 및 코발트의 공급망을 투명하게 관리하고..." |
| F2 | OECD 분쟁광물 실사 지침을 적용한다 | 2. 적용 기준: "OECD 분쟁광물 실사 지침" |
| F3 | 공급사에 연 1회 CMRT 작성·제출을 요청한다 | 3.1: "공급사에 연 1회 CMRT 작성·제출 요청" |
| F4 | 제련소 RMAP 인증 여부를 확인한다 | 3.2: "제련소 확인(RMAP 인증 여부)" |
| F5 | 고위험 공급원 식별 시 시정 요구 또는 거래 재검토한다 | 3.3: "고위험 공급원 식별 시 시정 요구 또는 거래 재검토" |
| F6 | 연 1회 분쟁광물 실사보고서를 공개한다 | 4. 보고: "연 1회 분쟁광물 실사보고서를 공개하고..." |
| F7 | 고객사 요청 시 30일 이내 CMRT를 제출한다 | 4. 보고: "고객사 요청 시 30일 이내 CMRT를 제출한다." |

**Difference from previous gold**: Added F7 (고객사 CMRT 제출 30일 기한).

---

## 9. safety_minutes_2025 (safety_policy_2025.pdf)

**doc_type**: safety_minutes  
**Rationale**: Document header says "산업안전보건위원회 회의록" (Industrial Safety and Health Committee meeting minutes).

**metrics_gold**:
- S-4-2: 0.3% (산업재해율 목표) - Source: "산업재해율 목표: 0.3% 이하"
- S-2-6: 62.0% (노동조합 가입률) - Source: "노동조합 가입률 현황 공유 (가입률 62%)"

**Rationale**: These are actual measured/target performance values reported in the meeting, not policy thresholds.

### Facts

| ID | Fact | Source passage |
|----|------|---------------|
| F1 | 산업재해율 목표 0.3% 이하(전년 0.41% 대비 감축) | 안건 1: "산업재해율 목표: 0.3% 이하 (전년 0.41% 대비 감축)" |
| F2 | 위험성 평가 연 2회 이상 실시한다 | 안건 1: "위험성 평가 연 2회 이상 실시" |
| F3 | 신규 입사자 안전교육 8시간 의무화 | 안건 1: "신규 입사자 안전교육 8시간 의무화" |
| F4 | 유해화학물질 보관구역 CCTV 설치 의결 | 안건 2: "유해화학물질 보관구역 CCTV 설치 의결" |
| F5 | 개인보호구(PPE) 착용 의무 전 공정 확대 | 안건 2: "개인보호구(PPE) 착용 의무 전 공정 확대" |
| F6 | 야간작업 조명 개선 요청이 2분기 내 조치 완료로 결의됨 | 안건 3: "야간작업 조명 개선 요청 → 2분기 내 조치 완료 결의" |
| F7 | 노동조합 가입률 62% | 안건 3: "결사의 자유 보장: 노동조합 가입률 현황 공유 (가입률 62%)" |
| F8 | 전원 찬성으로 의결됨 | 결의사항: "전원 찬성으로 의결" |

**Difference from previous gold**: F7 simplified to just the metric fact ("노동조합 가입률 62%") without "결사의 자유 보장" preamble, as the metric itself is the core verifiable content.

---

## 10. emergency_manual_2025 (emergency_manual_2025.pdf)

**doc_type**: emergency_manual  
**Rationale**: Document title is "비상대응 매뉴얼".

**metrics_gold**: [] (empty)  
**Rationale**: No measured ESG performance indicators.

### Facts

| ID | Fact | Source passage |
|----|------|---------------|
| F1 | 화재·폭발·화학물질 누출·자연재해 등 비상상황 대피 절차를 규정한다 | 1. 목적: "화재・폭발・화학물질 누출・자연재해 등 비상상황 발생 시 신속한 대피와 인명 피해 최소화를 위한 절차를 규정한다." |
| F2 | 1등급(긴급): 화재·폭발·대규모 누출 시 즉시 전원 대피한다 | 2. 비상 유형 분류: "1등급(긴급): 화재・폭발・대규모 누출 → 즉시 전원 대피" |
| F3 | 각 층 2개 이상 대피 경로를 확보한다 | 3. 대피 경로: "각 층 2개 이상 대피 경로 확보" |
| F4 | 집결지는 본관 앞 주차장 A구역이다 | 3. 대피 경로: "집결지: 본관 앞 주차장 A구역" |
| F5 | 비상 연락 체계: 현장 관리자→EHS팀장(5분 이내)→대표이사·119(10분 이내) | 4. 비상 연락 체계: "1차: 현장 관리자 → EHS팀장 (5분 이내)" + "2차: EHS팀장 → 대표이사・119 (10분 이내)" |
| F6 | 전사 합동 소방훈련 연 2회 실시한다 | 5. 훈련: "전사 합동 소방훈련 연 2회 실시" |
| F7 | 화학물질 누출 대응훈련 반기 1회 실시한다 | 5. 훈련: "화학물질 누출 대응훈련 반기 1회" |
| F8 | 훈련 결과 기록을 3년 보존한다 | 5. 훈련: "훈련 결과 기록 보존(3년)" |

**Difference from previous gold**: Added F8 (훈련 결과 기록 3년 보존).

---

## 11. hr_policy_2025 (hr_policy_2025.pdf)

**doc_type**: hr_policy  
**Rationale**: Document title is "인권・노사관계 정책서".

**metrics_gold**: [] (empty)  
**Rationale**: No measured ESG performance indicators.

### Facts

| ID | Fact | Source passage |
|----|------|---------------|
| F1 | UN 기업과 인권 이행원칙 및 RBA v8.0에 따라 인권을 보호한다 | 제1조: "UN 기업과 인권 이행원칙 및 RBA 행동규범 v8.0에 따라 모든 이해관계자의 인권을 존중하고 보호한다." |
| F2 | 모든 고용은 자발적이며 여권·신분증 보관을 금지한다 | 제2조 1-2항: "모든 고용은 자발적이며..." + "여권・신분증 보관을 금지한다." |
| F3 | 채용수수료는 회사가 부담하며 근로자에게 전가하지 않는다 | 제2조 3항: "채용수수료는 회사가 부담하며, 근로자에게 전가하지 아니한다." |
| F4 | 만 18세 미만의 근로자를 고용하지 않는다(아동노동 금지) | 제3조: "만 18세 미만의 근로자를 고용하지 아니한다." |
| F5 | 성별·나이·국적·종교·장애·성적 지향에 따른 차별을 금지한다 | 제4조: "성별・나이・국적・종교・장애・성적 지향에 따른 차별을 일체 금지한다." |
| F6 | 동일 업무에 동일 임금을 보장한다 | 제4조: "동일 업무에 동일 임금을 보장한다." |
| F7 | 근로자의 노동조합 가입 및 단체교섭권을 보장한다 | 제5조 1항: "근로자의 노동조합 가입 및 단체교섭권을 보장한다." |
| F8 | 노사협의회를 분기 1회 정기 개최한다 | 제5조 3항: "노사협의회를 분기 1회 정기 개최한다." |
| F9 | 익명 제보 핫라인을 운영하고 제보자 보복을 금지한다 | 제6조 1-2항: "익명 제보 핫라인(전화・이메일・앱)을 운영한다." + "제보자에 대한 보복을 금지하며..." |
| F10 | 연 1회 인권영향평가(HRIA)를 실시한다 | 제7조: "연 1회 인권영향평가(HRIA)를 실시하고 결과를 경영검토에 보고한다." |

**Difference from previous gold**: Added F6 (동일 임금 보장).

---

## 12-14. Scan Variants

The scan variants (work_hours_2025_scan, hazmat_2025_scan, responsible_minerals_2025_scan, safety_minutes_2025_scan) contain the same document content as their digital originals. Gold labels are identical to the respective digital original documents listed above.

---

## Summary of Changes from Previous Gold

| Document | Facts added | Facts modified |
|----------|------------|----------------|
| work_hours_2025 | +F4 (연장근로 12시간 상한) | - |
| doc_record_mgmt_2025 | +F3 (운영기록 3년), +F8 (폐기 절차) | - |
| rohs_reach_2025 | +F2 (법령 근거), +F6 (대체물질 전환) | - |
| capa_2025 | +F2 (적용범위), +F8 (경영검토 보고) | - |
| sanitation_housing_2025 | +F3 (식수 제공) | - |
| hazmat_2025 | +F4 (MSDS 갱신), +F6 (보호구 착용) | - |
| ip_protection_2025 | +F6 (서면 계약) | - |
| responsible_minerals_2025 | +F7 (CMRT 30일 제출) | - |
| safety_minutes_2025 | - | F7 simplified |
| emergency_manual_2025 | +F8 (훈련기록 3년 보존) | - |
| hr_policy_2025 | +F6 (동일 임금) | - |
