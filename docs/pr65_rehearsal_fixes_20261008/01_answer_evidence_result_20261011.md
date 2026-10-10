# PR #65 수정 1 — 답변·근거·범위 검증 결과

- 착수·공통 기준: `6f5dd9488b54570b2115345e4a74d35d2aa2b28c` (`codex/ui-guided-workspace`). GitHub에서 최신 PR #65 SHA가 이 값임을 확인했다.
- 수정 브랜치: `codex/rehearsal-answer-evidence-20261008`. 전용 worktree: `/private/tmp/ESGenie-rehearsal-answer-evidence-20261008`.
- 세트: `hanwool_bm_normal5_20261007_v1`. 최초 17개 PDF 중 회사 답변 1개와 독립 증빙 16개를 구분한다. 보완은 교육 증빙 1개 추가, 총 18개 PDF다.
- GPT-4.1 mini 설정은 변경하지 않았다. 검증은 기존 실제 AI/OCR 추출·원장·같은 실행의 회사 답변을 재생했다. 외부 요청은 0건이며 신규 AI/OCR 처리·A/B 독립 채점으로 표기하지 않는다.
- 실행 산출물: `outputs/rehearsal_fixes_20261008/answer_evidence/20261010/`. 최종 수정 SHA·코드/자료 해시는 이 경로의 `run_info.json`과 PR 설명에 기록한다.

## 원인과 수정

1. **코드 연결을 질문 충족으로 취급**: `mapping._derive_presence()`는 독립 근거가 연결되면 원문이 입증하지 않는 넓은 요건까지 ‘예’로 바꿨다. `requirement_fitness.py`에서 질문의 주제와 필수 요건을 분리한다. 미확인 요건은 `value=None`, `insufficient`, 구체적인 보완 요건으로 남긴다. 명시적인 ‘아니오’는 보존하고 충분한 독립 근거와 충돌하면 경고한다.
2. **단일 조항 코드의 누락**: 16번 22개 조항의 대표 태그는 E-1·B-6·D-6·E-6 등으로 분산됐고 E-7이 없었다. 18번 13개 조항에도 E-11 태그가 없었다. `_merge_rba_clause_evidence()`가 이 대표 코드만 모아 두 문항의 후보를 0건으로 만들었다. 자동 응답의 후보 탐색에서 독립 조항을 문항별로 다시 평가한다. OCR·대표 태그를 고쳐 쓴 것처럼 보고하지 않는다.
3. **우산 검사의 과잉 상속**: B-6은 S-4-1 전체 정책 실패를 물려받았다. 구체적인 RBA 요건 검사가 있는 존재형 문항에는 해당 문항과 관련된 실패만 적용한다. 해당 RBA 코드 실패와 기계 방호장치 등 관련 주제를 명시한 우산 검사 실패는 계속 경고한다. S-4-1 검사 결과 자체는 유지하며 다른 문항·수치형·D1/D6 검사를 삭제하거나 느슨하게 만들지 않는다.
4. **정성 범위 정보의 손실**: TextNode의 조항에는 원문 머리말의 기간·사업장이 빠져 있었다. 그래프에 원문을 파일별로 한 번 보존하고, 공용 Answer의 기존 `boundary_label`, `boundary`, `scope_notes`에 원문 범위를 기록한다. 제작일·파일명·실행 연도·회사 설정을 범위로 사용하지 않는다. 시행일·기준일·명시적인 운영월을 보존하며 여러 날짜의 min/max를 임의의 기간 합계로 만들지 않는다.
5. **절차·요약과 실행 기록의 혼동**: 실제 필수 페이지 제거 검사에서 요약 문장이 운영 기록을 대신하던 7개 실패를 확인했다. 위험평가의 구체적인 위험, 책임·권한, 검토 실시 시각·일자, 외부 대상과 개별 수령 확인, 실제 종결, 원본 복구 대조를 구분해 보완했다. 최종 변형 검사에서는 모두 보류한다.

부정·제한은 문서 전체가 아니라 문장/절 단위로 판정한다. 보복 금지·보복 없는 참여는 보호 근거로 유지한다. ‘교육 출석은 아니다’는 교육 근거가 될 수 없으나 같은 문서의 전달 기록은 E-7 근거로 남는다. 문서 관리 운영 기록은 E-11로 사용하면서 모든 법규 준수·인증 결론으로 확대하지 않는다.

## 공용 메타데이터 계약

추가된 공용 메타데이터는 `EvidenceGraph.source_texts: dict[str, str]` 하나다.

- 의미: OCR가 이미 읽은 문서 원문을 원본 `source_file` 키로 보관한다. 파일명은 조회 키이며 판정의 정답표가 아니다.
- 생성: `merge_ocr_extraction()`에서 `raw_text`를 보존한다. 외부 호출이나 조항·원장 값 변경은 없다.
- 직렬화: `EvidenceGraph.to_dict()`에 `source_texts`를 추가한다. 기본값은 빈 dict다.
- 하위 호환: 소비자는 `getattr(graph, 'source_texts', {})`로 읽는다. 구버전 그래프는 확인 가능한 조항 범위로만 보존한다. 확인하지 못한 범위를 실행 연도로 채우지 않는다. 새로 그래프를 복원하는 소비자가 있다면 선택적으로 이 dict를 복원할 수 있다.
- 수정 2·A·B: Answer/Question/회사 답변/검토 상태 필드는 추가·변경하지 않았다. 기존 Answer의 범위·근거·참조·보완 사유를 읽으면 된다. `pipeline.py`, `web/review.py`, `web/downloads.py`, 공용 exporters 및 프런트는 수정하지 않았다.

## 검증 결과

- 관련 회귀: **651개 통과**. 문항 적합성, 응답·초안·체크리스트·출력, RBA 태깅, SSOT, 수치 인식·범위·분모, 회사 답변 분리·D1을 포함한다.
- 저장 추출 재생·실제 자료 변형: **70/70 통과**. 최초·보완의 48행, 기준 커밋과 당시 결과의 값·상태 일치, 정상 5건의 필수 페이지 전체, 잘못된 긍정 5건, 인접 문항의 보류, 교육/안내 분리, 수치 의미, 공용 화면 표시를 검사했다.
- 정상 5건: B-6 14번 1·2·3쪽, E-2 15번 1·2쪽, E-7 16번 1·2·3쪽, E-10 17번 1·2쪽, E-11 18번 1·2쪽이 최초·보완 모두 연결되고 원문 범위의 ‘예’다. 검토 완료 상태를 자동 지정하지 않는다.
- 실제 추출 복사본에서 필수 페이지 12개를 각각 제거했을 때 모두 보류한다. 파일명·표현·기간·사업장을 바꾼 복사본에서도 정상 5건의 판정은 유지되고 변경한 원문 범위가 출력된다.
- A-3·C-7·C-8·E-3·E-4의 ‘예’는 최초·보완 모두 보류로 바뀐다. 신규 ‘예’는 필수 페이지를 입증한 E-7·E-11 두 문항뿐이다.
- 교육 근거는 최초 09번, 보완 09·13번이다. 16번 안내 수령과 18번 문서 등록을 교육 증빙으로 쓰지 않는다. 13번의 46+4=중복 제외 50을 보존하며 안내 수령 73을 합치지 않는다.
- 수치행의 값·단위·기간·근거·범위·회사 답변 대조 의미는 기준 커밋과 같다. 외부 폐기물 29.3%와 내부 스크랩 92%는 `not_comparable`과 다른 계산식·분모를 유지한다.

재생 스크립트는 웹 엔진이 pipeline 처리 뒤 붙인 회사 답변도 동일 실행의 `result.json`에서 복원한다. 개발 중 이 주장이 비어 있는 `pipeline.json`만 재생한 중간 결과의 한계를 발견했으며, 최종 비교는 회사 답변까지 복원한 `before_exact/`, `after_exact/`, `checks/`만 사용한다.

## 확인 범위와 남은 통합 검증

이번 수정은 지시된 답변·근거·범위 경로에 한정한다. 요건 규칙은 수정 대상과 인접 문항 13개에 적용하며, 모든 자연어 표현에 대한 정확도나 전수 오탐률을 주장하지 않는다. 명시적으로 같은 범위임을 입증하지 못한 서로 다른 문서의 부분 요건은 보류한다. 알려지지 않은 표현·원문 머리말이 없는 자료는 사람이 확인할 수 있다.

A·B의 독립 라벨·채점과 신규 전체 AI/OCR 처리, 담당자 저장 상태를 포함한 실제 Chrome 다운로드 완주는 네 수정의 통합 버전에서 수행해야 한다. 이번 재생 검사를 최종 라이브 리허설로 대체하지 않는다. 자료·라벨·모델은 제품 결과에 맞춰 변경하지 않았다. 수정 2·3·4와 main 병합은 진행하지 않는다.

## 48행 전후 목록

아래는 최초 재생이다. 보완 단계도 같은 문항에서 같은 값·상태 변화가 있으며 E-6에 13번 교육 기록과 해당 범위가 추가된다. 전후 원문 인용·참조·범위·이유 전체는 `checks/diff_48_rows.json`의 두 단계 각 48행에 남겼다.

| 문항 | 전 값·상태 | 후 값·상태 | 변경 이유/필드 |
|---|---|---|---|
| A-1 | 보류 / insufficient | 보류 / insufficient | 변경 없음 |
| A-2 | 보류 / insufficient | 보류 / insufficient | 변경 없음 |
| A-3 | 예 / verified | 보류 / insufficient | 근로시간의 휴무·자발성·법정 한도 부족 |
| A-4 | 보류 / insufficient | 보류 / insufficient | 변경 없음 |
| A-5 | 보류 / insufficient | 보류 / insufficient | 변경 없음 |
| A-6 | 보류 / insufficient | 보류 / insufficient | 변경 없음 |
| B-1 | 예 / flagged | 예 / flagged | 원문 기간·사업장 범위 표시 |
| B-2 | 보류 / insufficient | 보류 / insufficient | 변경 없음 |
| B-3 | 보류 / insufficient | 보류 / insufficient | 변경 없음 |
| B-4 | 보류 / insufficient | 보류 / insufficient | 변경 없음 |
| B-5 | 보류 / insufficient | 보류 / insufficient | 변경 없음 |
| B-6 | 예 / flagged | 예 / verified | 해당 기계 요건·전수 점검·보수 및 범위 확인 |
| B-7 | 보류 / insufficient | 보류 / insufficient | 변경 없음 |
| B-8 | 보류 / insufficient | 보류 / insufficient | 교육·소통 일부 연결, 전체 요건 보류 |
| C-1 | 보류 / insufficient | 보류 / insufficient | 변경 없음 |
| C-2 | 예 / verified | 예 / verified | 원문 기간·사업장 범위 표시 |
| C-3 | 보류 / insufficient | 보류 / insufficient | 변경 없음 |
| C-4 | 예 / verified | 예 / verified | 원문 기간·사업장 범위 표시 |
| C-4-E-6-2 | 29.3 / flagged | 29.3 / flagged | 변경 없음 |
| C-4-E-6-1 | 18.4 / self_reported | 18.4 / self_reported | 변경 없음 |
| C-5 | 보류 / insufficient | 보류 / insufficient | 변경 없음 |
| C-5-E-7-1 | 보류 / insufficient | 보류 / insufficient | 변경 없음 |
| C-6 | 보류 / insufficient | 보류 / insufficient | 변경 없음 |
| C-7 | 예 / verified | 보류 / insufficient | 폐수·절수·오염경로 요건 부족 |
| C-8 | 예 / verified | 보류 / insufficient | 감축목표·Scope 추적·공개 부족 |
| C-8-E-3-1 | 68.158 / self_reported | 68.158 / self_reported | 변경 없음 |
| C-8-E-4-1 | 0.513216 / self_reported | 0.513216 / self_reported | 변경 없음 |
| C-8-E-4-2 | 보류 / flagged | 보류 / flagged | 변경 없음 |
| D-1 | 보류 / hitl_required | 보류 / hitl_required | 변경 없음 |
| D-2 | 예 / verified | 예 / verified | 원문 기간·사업장 범위 표시 |
| D-3 | 보류 / insufficient | 보류 / insufficient | 변경 없음 |
| D-4 | 보류 / insufficient | 보류 / insufficient | 변경 없음 |
| D-5 | 보류 / insufficient | 보류 / insufficient | 변경 없음 |
| D-6 | 예 / verified | 예 / verified | 원문 기간·사업장 범위 표시 |
| D-7 | 보류 / insufficient | 보류 / insufficient | 변경 없음 |
| D-8 | 예 / verified | 예 / verified | 원문 기간·사업장 범위 표시 |
| E-1 | 예 / flagged | 예 / flagged | 원문 기간·사업장 범위 표시 |
| E-2 | 예 / verified | 예 / verified | 책임·권한과 정기 검토 실행 연결 |
| E-3 | 예 / verified | 보류 / insufficient | 법규·고객 요건 식별·모니터링 부족 |
| E-4 | 예 / verified | 보류 / insufficient | 폭넓은 위험 식별·중요도·통제 부족 |
| E-5 | 보류 / insufficient | 보류 / insufficient | 변경 없음 |
| E-6 | 예 / verified | 예 / verified | 교육 실시와 안내 수령 분리 |
| E-7 | 보류 / insufficient | 예 / verified | 전달 절차·운영 대장·개별 수령 확인 연결 |
| E-8 | 보류 / insufficient | 보류 / insufficient | 참여·구제 일부 연결, 양방향 참여 요건 보류 |
| E-9 | 보류 / insufficient | 보류 / insufficient | 변경 없음 |
| E-10 | 예 / verified | 예 / verified | 발견·조치와 실제 종결 기록 및 범위 확인 |
| E-11 | 보류 / insufficient | 예 / verified | 문서 관리 절차·보호·복구 실행 연결 |
| E-12 | 보류 / insufficient | 보류 / insufficient | 변경 없음 |
