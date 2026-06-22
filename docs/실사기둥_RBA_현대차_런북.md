# 실사 기둥 (RBA / 현대차) — 런북

ESGenie 2기둥 구조 중 **실사 기둥**(대기업 협력사 ESG 실사 응답서)의 구성·실행 안내.
공시 기둥은 K-ESG, 실사 기둥은 **RBA 행동규범 v8.0(2024)** 을 substrate로 둔다.

## 구성

| 파일 | 역할 |
|------|------|
| `esgenie/knowledge/rba_items.py` | RBA v8.0 42조항 정의 + K-ESG 크로스워크(`_KESG_XWALK`) + 정량지표 분해(`metrics`) |
| `esgenie/supplychain/frameworks/rba_self.py` → `rba42` | 실사 substrate. RBA 항목 → 문항 자동생성(일부는 수치행 분해) |
| `esgenie/supplychain/frameworks/hmc.py` → `hmc` | 현대차 어댑터. RBA-42를 현대차 5영역 순서로 재배치 |
| `scripts/demo_hmc_sheet.py` | **샌드박스 데모** — 라이브 OCR 없이 한울정밀 수치로 응답서 Excel/PDF 생성 |
| `scripts/demo_d6.py` | **로컬 라이브 검증** — 실제 PDF→OCR→추출→응답서 + 그린워싱 🚩 |

### 양식 ↔ K-ESG 재사용
RBA/현대차 문항은 K-ESG 크로스워크(`kesg_codes`)를 실어 기존 responder/derive 엔진을
그대로 쓴다(추가 검출엔진 없음). 42개 중 32개가 K-ESG에 매핑, 10개는 RBA 고유
(근로시간·유해물질·물질규제·IP·분쟁광물·법규·의사소통·시정조치·문서기록·위생식품).

### 정량지표 분해(metrics)
한 조항이 여러 수치를 가지면 '조항 존재형 1문항 + 지표별 수치행 N개'로 펼친다.
- **C-4 고형폐기물** → 관리체계 존재 + 재활용률(%) + 배출량(톤)
- **C-8 에너지·온실가스** → 방침 존재 + Scope1+2(tCO2eq) + 에너지(TJ) + 재생에너지(%)

자가주장 D1 검증이 각 지표 코드(E-6-2 등)에 정확히 걸리도록 수치행 `kesg_codes`는 단일.

## 실행

### 1) 샌드박스 데모 (키 불필요)
```bash
python -m scripts.demo_hmc_sheet
# → outputs/_supplychain_demo/실사응답서_hmc_한울정밀공업㈜.{xlsx,pdf}
```
한울정밀 대표 증빙(가상)으로 현대차 양식 응답서를 생성. 폐기물 재활용률 행에서
SAQ 자가주장 92% vs 증빙 29.3% → **62.7%p 괴리 🚩** 가 뜨는지 확인.

### 2) 로컬 라이브 검증 (Azure/OpenAI 키 필요 — 샌드박스 불가)
```bash
# 현대차 양식으로 실제 OCR 파이프라인 검증 + 엑셀 출력
python -m scripts.demo_d6 --framework hmc --export --strict
# RBA substrate로 보려면
python -m scripts.demo_d6 --framework rba42 --export
```
합격 기준:
- OCR engine = azure_docintel(01~03) / gpt-4.1-mini-text(04) — mock/None 아님
- 폐기물 재활용률 행(`HMC-C-4-E-6-2`) status = flagged 🚩
- 응답서 flagged_count ≥ 1

## 남은 작업(TODO)
- 현대차 **실제 자가진단 질문지(SAQ)** 문구·번호·subset 확보 시 `hmc.py` 교체
  (현재는 행동규범 5영역 완전본).
- 노동·안전보건·윤리·경영 영역 **실제 증빙 PDF** 를 한울정밀 세트에 추가 생성
  (현재 `demo_hmc_sheet.py`는 보유 가정한 대표 문서로 커버리지를 채움).
- RBA 고유 10개 항목용 증빙요구(`kesg_evidence_requirements`) RBA 전용 안내문 보강.
