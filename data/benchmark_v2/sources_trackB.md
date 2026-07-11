# Track B 출처 목록 — regulatory_ad greenwash 신규 16건 (TRKB-001~016)

날짜: 2026-07-11
브랜치: feature/holdout-expansion

모든 케이스는 **영국 광고표준위원회(ASA, Advertising Standards Authority)**의 공개 판정문
(1차 출처, upheld 확정 건만)에서 가져왔습니다. 각 판정문 URL에서 원본 raw HTML을 직접
다운로드해 "Ad description" 섹션의 광고 원문을 바이트 단위로 대조·확인했습니다(WebFetch
요약만으로 끝내지 않고 curl로 원문 재검증).

test.json에는 `source`/`source_url` 필드를 케이스별로 직접 추가했습니다(esg_report 트랙과
동일한 방식). 아래 표는 그 1:1 대응을 사람이 훑어보기 쉽게 별도 정리한 것입니다.

| 케이스 ID | 기업 | ASA Ref. | 판정일 | 카테고리 | URL |
|---|---|---|---|---|---|
| TRKB-001 | Deutsche Lufthansa AG (Lufthansa) | A22-1169419 | 2023-03-01 | absolute_unsubstantiated | https://www.asa.org.uk/rulings/deutsche-lufthansa-ag-a22-1169419-deutsche-lufthansa-ag.html |
| TRKB-002 | Oatly UK Ltd | G21-1096286 | 2022-01-26 | condition_omitted | https://www.asa.org.uk/rulings/oatly-uk-ltd-g21-1096286-oatly-uk-ltd.html |
| TRKB-003 | Mazda Motors UK Ltd | A24-1247950 | 2024-09-25 | absolute_unsubstantiated | https://www.asa.org.uk/rulings/mazda-motors-uk-ltd-a24-1247950-mazda-motors-uk-ltd.html |
| TRKB-004 | Nike Retail BV | A25-1309100 | 2025-12-03 | absolute_unsubstantiated | https://www.asa.org.uk/rulings/nike-retail-bv-a25-1309100-nike-retail-bv.html |
| TRKB-005 | Lacoste E-commerce (Lacoste) | A25-1309097 | 2025-12-03 | absolute_unsubstantiated | https://www.asa.org.uk/rulings/lacoste-e-commerce-a25-1309097-lacoste-e-commerce.html |
| TRKB-006 | London Luton Airport Ltd (Luton Rising) | G24-1241707 | 2024-07-10 | condition_omitted | https://www.asa.org.uk/rulings/london-luton-airport-ltd.html |
| TRKB-007 | TIER Operations Ltd | A21-1118832 | 2022-04-06 | absolute_unsubstantiated | https://www.asa.org.uk/rulings/tier-operations-ltd-a21-1118832-tier-operations-ltd.html |
| TRKB-008 | Golden Leaves Ltd | A21-1128535 | 2022-08-03 | absolute_unsubstantiated | https://www.asa.org.uk/rulings/golden-leaves-ltd-a21-1128535-golden-leaves-ltd.html |
| TRKB-009 | Hurtigruten UK Ltd (HX Hurtigruten Expeditions) | A24-1237378 | 2024-07-17 | absolute_unsubstantiated | https://www.asa.org.uk/rulings/hurtigruten-uk-ltd-a24-1237378-hurtigruten-uk-ltd.html |
| TRKB-010 | Floor Design Ltd (Flooring by Nature) | A24-1253521 | 2025-02-19 | condition_omitted | https://www.asa.org.uk/rulings/floor-design-ltd-a24-1253521-floor-design-ltd.html |
| TRKB-011 | www.Cruise.co.uk Ltd (Seascanner) | A25-1284421 | 2025-09-03 | condition_omitted | https://www.asa.org.uk/rulings/www-cruise-co-uk-ltd-a25-1284421-www-cruise-co-uk-ltd.html |
| TRKB-012 | TravelCircle Ltd (Cruise Circle) | A25-1284422 | 2025-09-03 | absolute_unsubstantiated | https://www.asa.org.uk/rulings/travelcircle-ltd-a25-1284422-travelcircle-ltd.html |
| TRKB-013 | Calvin Klein Europe BV (Calvin Klein) | A26-1327724 | 2026-06-24 | partial_truth | https://www.asa.org.uk/rulings/calvin-klein-europe-bv--a26-1327724-calvin-klein-europe-bv.html |
| TRKB-014 | Etihad Airways | A23-1206008 | 2023-12-06 | vague_abstract | https://www.asa.org.uk/rulings/etihad-airways-a23-1206008-etihad-airways.html |
| TRKB-015 | Etihad Airways | A22-1174208 | 2023-04-12 | absolute_unsubstantiated | https://www.asa.org.uk/rulings/etihad-airways-a22-1174208-etihad-airways.html |
| TRKB-016 | Easigrass (Distribution) Ltd | G23-1215528 | 2024-03-27 | absolute_unsubstantiated | https://www.asa.org.uk/rulings/easigrass--distribution--ltd-g23-1215528-easigrass--distribution--ltd.html |

전건 **Upheld**(위반 인정) 판정. Not upheld(기각) 건은 채택하지 않음(예: Aramco
A24-1250237, 2025-07-09 not upheld — 검토했으나 미채택).

## 접근 실패/미채택 소스 (그대로 보고)

- **한국 공정거래위원회 의결서**: 포스코/포스코홀딩스 그린워싱 시정명령(2025-04-17 보도)을
  확인했으나, 개별 의결서 원문(의결번호)을 이 세션의 웹 접근으로 찾지 못함. 언론 보도(뉴시스,
  경향신문 등) 2차 출처만 확인되어 "1차 출처 우선, 2차 기사만 있으면 제외" 원칙에 따라
  **미채택**. 문구도 기존 test.json GOLD-20("이노빌트·e오토포스·그린어블")과 사실상 동일 사안이라
  중복 우려도 있었음.
- **한국환경산업기술원 녹색제품정보시스템(greenproduct.go.kr)**: 이 세션 환경에서 DNS 조회 실패
  (`ENOTFOUND`)로 접근 불가 — kind.krx.co.kr/company.emart.com과 동일한 네트워크 제약으로 추정.
  실제 사례 데이터베이스로 유망했으나 사용하지 못함.
- **금융위원회/금융감독원**: ESG펀드 그린워싱에 대한 구체적 제재 사례(사건번호 포함)를 이
  세션에서 찾지 못함 — 2024년 국정감사에서 "그린워싱 규제 미비"가 지적된 정황만 확인, 구체적
  의결 사례 없음. **미채택**.
- **EU 그린워싱 지침(Green Claims Directive) 적발 사례**: 지침 자체(정책)만 확인, 개별 기업
  제재 사례·사건번호는 이 세션에서 찾지 못함. **미채택**.

## 제외 판단(uncertain 아님, 완전 제외)

- **Kinetique Ltd t/a Ethica Diamonds (A21-1102851, 2021-10-06)**: ASA가 위반으로 인정했으나,
  판정 근거가 "carbon neutral diamonds" 등 환경 주장보다는 "다이아몬드"라는 재질 명칭이
  실제로는 랩그로운 모조석(모이사나이트)임을 소비자가 오인한다는 **소재/품질 오인** 문제가
  핵심이었음. 지시사항의 "단순 가격·품질 과장 등 비환경 위반은 제외" 원칙에 따라 완전 제외
  (uncertain 처리도 하지 않음 — 애초에 그린워싱 판정의 핵심 사유가 아니었으므로 후보 목록에서
  아예 빠짐).
- **Innocent Ltd (G21-1111827, 2022-02-23)**: 확인은 했으나 광고 형식이 애니메이션 노래
  가사("We're messing up the planet... Let's get fixing up the planet")로, 하나의 명확한
  "광고 문구"로 축약하면 원문의 뉘앙스가 왜곡될 위험이 있어 verbatim 원칙상 제외. (다른 후보로
  충분히 목표 건수를 채울 수 있어 무리하게 포함하지 않음.)

uncertain 처리 건은 없음 — 16건 전부 ASA가 명시적으로 "Upheld"(위반 인정) 판정했고,
5개 카테고리 중 하나로 명확히 분류 가능했음.
