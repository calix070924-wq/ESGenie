**ESGenie 추가 테스트 점검 결과 · 2026-09-11**

새로 작성한 **62개 테스트에서 29개 통과, 33개 실패**가 확인됐다. 실행 준비 오류와 건너뛴 테스트는 없었다. 이전 대화의 30개 테스트도 다시 실행했으며 **12개 통과, 18개 실패**로 당시 결과가 재현됐다. 이전 테스트 수는 새 테스트 62개에 포함하지 않았다.

이번 작업의 범위는 테스트 작성·실행과 결과 보고다. 기존 466개 파일의 내용을 해시로 비교했고, 변경은 없었다. `.env`와 API 키도 그대로다. 점검 당시 기준 커밋은 `d3a06e2`이며, 기존 작업 중 변경사항이 포함된 현재 작업 폴더를 대상으로 했다.

| 점검 영역 | 실행 | 통과 | 실패 |
|---|---:|---:|---:|
| 설문·근거 연결 | 10 | 3 | 7 |
| 수치·단위·경곗값 | 20 | 9 | 11 |
| 생성한 SAQ PDF의 실제 텍스트 파싱 | 10 | 5 | 5 |
| 원장 → 위험도 표 → 응답서 | 9 | 3 | 6 |
| 응답 캐시·실패 처리 | 8 | 7 | 1 |
| D3 근거 부재 처리(제안 요구사항) | 2 | 0 | 2 |
| OCR 입력 통합 경로 | 3 | 2 | 1 |
| **합계** | **62** | **29** | **33** |

**우선 확인할 문제**

1. **‘아니오’ 설문이 ‘예·증빙검증’으로 바뀐다.**

   실제 `_collect_ocr_extractions → build_unified_graph → extract_with_ssot → _apply_survey_answers → build_response_sheet`를 연결했다. `[설문] 아니오`가 `survey_form`의 텍스트 근거 노드로 들어가고, 원장에서는 ‘문서 조항 확인’, SAQ 응답에서는 `value=true`, `status=verified`가 됐다. ‘예’ 입력도 독립 증빙 없이 검증됨으로 표시됐다. 원문에는 파일·설문 구분과 부정 응답이 남아 있으나 응답 도출에 반영되지 않는다.

   확인 위치: [설문 입력](/Users/heojeongmin/Documents/Claude/Projects/ESGenie/esgenie/pipeline.py:85), [정성 조항의 원장 반영](/Users/heojeongmin/Documents/Claude/Projects/ESGenie/esgenie/ssot/ssot_pipeline.py:289), [존재형 응답](/Users/heojeongmin/Documents/Claude/Projects/ESGenie/esgenie/supplychain/mapping.py:204).

2. **원장이 고른 실적값을 응답서가 다시 다른 값으로 고른다.**

   전사 합계 **248.5 TJ**와 국내 사업장 **61.2 TJ**를 포함한 합성 OCR 산출물을 실제 통합 함수에 넣었다. 원장은 248.5를 선택했으나 위험도 표와 응답서는 61.2를 표시했고, 응답 상태는 `verified`였다. OCR 신뢰도는 각각 0.90과 0.99였다. 원장은 총량·부분값 구분을 따르지만 응답서용 값은 별도로 최신 연도·출처·신뢰도를 사용해 재선택한다. 위험도 표는 마지막에 들어온 노드를 사용해 입력 순서에도 영향을 받는다.

   추가로 DART 정규식 값 **999.0**을 원장에서 **248.5**로 교정한 경우, 출력 단계에서 999.0이 다시 선택됐다. 이는 원장의 교정이 출력까지 유지되지 않는 같은 계열의 문제다.

   확인 위치: [응답서용 값 생성](/Users/heojeongmin/Documents/Claude/Projects/ESGenie/esgenie/ssot/audit_trace.py:92), [출처·신뢰도 재선택](/Users/heojeongmin/Documents/Claude/Projects/ESGenie/esgenie/ssot/audit_trace.py:177), [위험도 표 값 선택](/Users/heojeongmin/Documents/Claude/Projects/ESGenie/esgenie/pipeline.py:196).

3. **SAQ PDF의 목표 수치를 실적 주장으로 읽는다.**

   임시 PDF를 실제로 생성하고 텍스트가 원문 그대로 추출되는지 확인한 뒤, 현재 `parse_saq_claims`를 실행했다. `2030년 재활용률 목표 90.2% / 2025년 재활용률 43.8% 달성`은 **90.2**로 읽혔다. 목표만 있는 문구도 실적으로 받아들였다. 음수 `−4%`를 ASCII 마이너스가 들어간 `-4%`로 시험했을 때는 **4.0**으로 읽었고, **101.2%**도 거부하지 않았다. 정상 실적, 0%, 100%, 매립·소각 비율의 보수값 계산은 통과했다.

   확인 위치: [SAQ 주장 추출](/Users/heojeongmin/Documents/Claude/Projects/ESGenie/esgenie/supplychain/claims.py:96).

4. **단위와 잘못된 수치를 검증하는 방어가 부족하다.**

   공급망 수치 주장 입력 경계에서 **2 MWh와 2,000 kWh를 불일치**로, **2 MWh와 2 kWh를 일치**로 판정했다. kWh와 m3처럼 물리량이 다른 단위도 숫자가 같으면 일치했다. 재활용률의 음수·100 초과값·NaN 사례 일부가 검증됨/일치로 남았다. 문서화된 10%p 임계값보다 작은 **9.99%p 차이**도 비교 전 반올림 때문에 불일치로 처리됐다.

   에너지 단위 사례는 단위가 포함된 `SupplierClaim` 직접 입력 테스트다. 현재 SAQ PDF 파서는 재활용 비율만 추출하므로, 에너지 단위 오류를 현재 PDF 파서의 실측 오류라고 해석하면 안 된다.

   확인 위치: [수치 대조와 반올림](/Users/heojeongmin/Documents/Claude/Projects/ESGenie/esgenie/supplychain/mapping.py:187).

5. **근거 ID나 일부 근거만 있어도 전체 답변이 검증됨이 될 수 있다.**

   그래프에 없는 근거 ID를 주면 임시 링크가 생성되고 검증됨으로 표시됐다. 다른 주제의 조항을 가리키는 ID도 통과했다. 선택형 문항은 ‘에너지 효율’만 근거가 있고 ‘재사용·재활용’에는 근거가 없어도 두 영역을 선택한 전체 응답이 검증됨이었다. 이 사례들은 검증 배지의 신뢰 요건을 점검하는 합성 입력이다.

   확인 위치: [선택형 응답](/Users/heojeongmin/Documents/Claude/Projects/ESGenie/esgenie/supplychain/mapping.py:223), [근거 링크 생성](/Users/heojeongmin/Documents/Claude/Projects/ESGenie/esgenie/supplychain/mapping.py:251).

6. **엔드포인트만 변경하면 이전 응답 캐시가 재사용된다.**

   외부 호출을 대신하는 시험용 제공자 A/B를 사용했다. 모델명과 프롬프트를 유지한 채 주소를 A에서 B로 바꾸고 클라이언트를 새로 생성해도, B를 호출하지 않고 A의 응답을 반환했다. 캐시 키에 엔드포인트가 없기 때문이다. 같은 입력 재사용, 프롬프트·모델·JSON 모드·온도 변경, 캐시 비활성화, strict 오류 처리 대조군은 통과했다. 앞서 확인한 실제 Azure 연결 성공과는 별개로, 향후 주소를 바꿀 때의 재현성 문제다.

   확인 위치: [캐시 사용](/Users/heojeongmin/Documents/Claude/Projects/ESGenie/esgenie/llm.py:99), [캐시 키 구성](/Users/heojeongmin/Documents/Claude/Projects/ESGenie/esgenie/llm_cache.py:43).

7. **D3 근거 부재는 현재 ‘평가불가’로 표현되지 않는다.**

   청크가 없으면 D3=0.5, 다른 축이 0일 때 종합=0.125였다. 빈 검색 인덱스를 주면 D3=1.0, 종합=0.25였다. 둘 다 기권 표시는 없었다. 이 두 실패는 이전 대화에서 제안했던 ‘근거 없으면 명시적으로 기권’ 요구사항과 현재 동작의 차이다. 기존에 보장된 기능의 회귀라고 판정한 것은 아니다.

   확인 위치: [D3 판정](/Users/heojeongmin/Documents/Claude/Projects/ESGenie/esgenie/layer3_detect.py:626).

**결과를 해석할 범위**

실패 33건은 매개변수가 다른 경계 사례를 포함한 테스트 수이며, 독립 버그 33개나 실제 기업 문서 오류율을 뜻하지 않는다. 테스트에는 정상 대조군과 제품 신뢰 요건을 확인하는 사례를 함께 넣었다.

임의 그래프에 목표·미래값을 직접 넣으면 출력값이 오염되는 사례도 있었다. 다만 정상 OCR 결과 통합 경로의 목표 분리·미래 연도 분리는 대조 테스트에서 통과했다. 따라서 이 두 그래프 직접 주입 사례를 모든 업로드에서 발생하는 문제로 확대하지 않는다. 전사 합계와 부분값 문제는 OCR 결과 통합 경로를 거친 뒤에도 재현됐다.

이번 새 테스트의 외부 API 호출은 **0회**다. 설문 처리·실제 SAQ 양식·원장과 응답서 연결은 기존 함수를 사용했고, SAQ PDF는 임시로 생성한 텍스트 PDF다. OCR/VLM의 실제 인식 품질, Azure 모델 응답 품질, 전체 라이브 시연 성능은 이번 결과에 포함되지 않는다. 외부 네트워크는 테스트에서 차단했고 예기치 않은 접속 시도도 없었다.

**재현 자료**

- [새 테스트](/Users/heojeongmin/Documents/Claude/Projects/ESGenie/outputs/audit_20260911_ry_stjqv/tests/test_fresh_contracts.py)
- [OCR 산출물 통합 경로 추가 테스트](/Users/heojeongmin/Documents/Claude/Projects/ESGenie/outputs/audit_20260911_ry_stjqv/tests/test_ingress_replay.py)
- [개별 입력·출력과 실패 내용](/Users/heojeongmin/Documents/Claude/Projects/ESGenie/outputs/audit_20260911_ry_stjqv/results.json)
- [기존 파일 무변경 확인](/Users/heojeongmin/Documents/Claude/Projects/ESGenie/outputs/audit_20260911_ry_stjqv/integrity.json)
- [새 테스트 실행 로그](/Users/heojeongmin/Documents/Claude/Projects/ESGenie/outputs/audit_20260911_ry_stjqv/fresh.log), [추가 경로 로그](/Users/heojeongmin/Documents/Claude/Projects/ESGenie/outputs/audit_20260911_ry_stjqv/ingress.log), [이전 30개 재실행 로그](/Users/heojeongmin/Documents/Claude/Projects/ESGenie/outputs/audit_20260911_ry_stjqv/previous.log)

재실행은 프로젝트의 Python 환경에서 아래 파일을 실행하면 된다. 결과와 PDF 입력·캐시는 새로운 임시 폴더에 생성된다. 구현을 고치지 않은 현재 상태에서는 실패를 나타내는 종료 코드 1이 정상적으로 재현될 것으로 예상된다.

```text
venv/bin/python outputs/audit_20260911_ry_stjqv/run_tests.py
```
