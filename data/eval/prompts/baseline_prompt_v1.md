# 일반 문서 AI 대조군 프롬프트 v1

- 버전: `v1` · 초안 수정 2026-10-07 (KST) · 대상: 작업지시서 B §4.2 대조군 실행기
- **상태: 초안. 아직 커밋하지 않는다.** A 의 공통 답안 형식이 **v1.1 로 확정됐다**
  (A c1bfa67, 2026-10-10 — B 회신 C-1~C-6에 따라 `-draft` 를 뗐다). §6 을 그 확정판에
  맞춰 갱신했다(2026-10-12). **§1·§2 의 모델 지시는 바뀌지 않았다**(해시로 확인).
  검사 통과 후 사람이 "프롬프트 커밋해"라고 지시하면 그때 고정·커밋한다.
  **고정 후에는 실행 후에 고치지 않는다.** 고쳐야 하면 `baseline_prompt_v2.md` 를 새로 만들고 v1·v2 결과를
  모두 보고한다.
- 적용: `scripts/baseline_generic_llm.py` 가 이 파일의 §1·§2 를 **파일에서 읽어** 쓴다.
  프롬프트 문구를 코드에 넣지 않는다. 실행 기록에 이 파일의 sha256 을 남긴다. temperature 0.
- 작성 원칙(작업지시서 §4.2): 평범한 "일반 문서 AI" 지시로 쓴다. 문서를 읽고 각 문항에 답하거나 답할 수
  없으면 보류하고, 근거 파일·쪽·인용을 JSON 으로 받는다.
  **기간·분모·사업장 범위를 확인하라는 지시를 일부러 넣지 않고, 일부러 빼지도 않는다.**

---

## 1. 시스템 프롬프트

```text
당신은 문서를 읽고 질문에 답하는 도우미입니다.

사용자가 PDF에서 추출한 문서 텍스트 묶음과 질문 목록을 줍니다.
문서에 있는 내용으로 각 질문에 답하고, 답할 수 없으면 보류하세요.
질문이 이 회사에 해당하지 않으면 해당 없음으로 표시하세요.
답과 보류 모두 근거가 된 파일 이름, 쪽 번호, 문서의 문장을 그대로 인용해 함께 적으세요.
문서에 없는 내용을 새로 만들지 마세요.

답은 지정한 JSON 형식으로만 출력하세요. JSON 외의 설명·머리말·코드펜스는 붙이지 마세요.
```

## 2. 사용자 프롬프트 템플릿

치환 자리:

| 자리 | 내용 |
|---|---|
| `{{DOCUMENTS}}` | §3 규칙으로 이어 붙인 증빙 문서 텍스트 묶음 |
| `{{COMPANY_ANSWER}}` | §4 규칙으로 만든 회사 자가 답변 구획 |
| `{{QUESTIONS}}` | §5 규칙으로 만든 질문 목록 |
| `{{QUESTION_COUNT}}` | 질문 수(정수) |

```text
아래 [문서]는 한 회사가 제출한 PDF에서 추출한 텍스트입니다.
[회사 자가 답변 초안 — 검증된 증빙 아님]은 회사가 작성한 자가 답변 초안이며 검증된 증빙이 아닙니다.
[질문]은 이 회사에 대해 확인해야 하는 항목 {{QUESTION_COUNT}}개입니다.

[문서]
{{DOCUMENTS}}

[회사 자가 답변 초안 — 검증된 증빙 아님]
{{COMPANY_ANSWER}}

[질문]
{{QUESTIONS}}

[요청]
질문 {{QUESTION_COUNT}}개에 모두 응답하세요. 질문 하나당 객체 하나이고, 질문에 적힌 qid를 그대로 씁니다.
문서 내용으로 답할 수 있으면 "answer", 답할 수 없으면 "hold", 이 회사에 해당하지 않으면
"not_applicable"로 표시하세요.
근거는 위 블록에 표시된 파일 이름과 쪽 번호를 쓰고, 인용은 문서의 문장을 그대로 옮기세요.

[출력 형식]
아래 JSON 스키마 그대로, 최상위 객체 하나만 출력하세요.

{
  "answers": [
    {
      "qid": "<질문의 qid>",
      "decision": "answer" | "hold" | "not_applicable",
      "answer_text": "<사람이 읽을 응답 한두 문장. 보류면 보류 사유>",
      "value": <수치 질문이면 숫자, 예/아니오 질문이면 true 또는 false, 그 밖이면 null>,
      "unit": "<value의 단위. 없으면 빈 문자열>",
      "period": "<응답이 가리키는 기간. 없으면 빈 문자열>",
      "scope": "<응답이 가리키는 대상 범위. 없으면 빈 문자열>",
      "evidence": [
        {
          "file_name": "<블록에 적힌 파일 이름>",
          "page": <블록에 적힌 쪽 번호. 1부터 시작하는 정수. 블록에 '쪽: 미상'이면 null>,
          "quote": "<해당 쪽에 있는 문장을 그대로>"
        }
      ],
      "note": "<추가로 남길 말. 없으면 빈 문자열>"
    }
  ]
}

규칙:
- "answers"의 길이는 {{QUESTION_COUNT}}이고 순서는 [질문]과 같습니다.
- "decision"이 "hold" 또는 "not_applicable"이면 "value"는 null로 둡니다.
  "evidence"는 그 판단의 근거가 있으면 적고, 없으면 빈 배열로 둡니다.
- "page"는 블록에 표시된 쪽 번호를 그대로 쓰며 1부터 시작하는 정수입니다.
  블록이 '쪽: 미상'이면 null로 둡니다. 모르는 쪽을 1로 적지 마세요.
- JSON만 출력합니다.
```

## 3. 증빙 문서 텍스트 조립 규칙 — `{{DOCUMENTS}}`

- 원천: **ESGenie 가 같은 실행에서 쓴 OCR 결과**(`pipeline.json` 의 `ocr_extractions[].raw_text`).
  원본 PDF 를 다시 읽지 않는다. 차이가 OCR 이 아니라 검증 구조에서 나오게 하기 위한 조건이다
  (작업지시서 §4.2).
- 문서 순서는 `ocr_extractions` 의 순서 그대로. 텍스트를 요약·정제·재배열하지 않는다.
- 쪽 구분은 아래 순서로 복원한다(1-기준으로 바꿔 쓴다).
  1. `router_meta.chunk_sources` 의 `page`·`line_start`·`line_end` — `raw_text` 를 줄 단위로 쪼개 쪽에 배분
  2. 쪽이 1쪽임이 `router_meta.dp_response.usage.pages` 또는 `raw_upstage/*.json` 의
     `body.usage.pages`·`body.elements[].page` 로 확인되면 전체를 1쪽으로 본다
  3. 둘 다 안 되면 **복원하지 않는다.** 그 문서는 쪽을 `미상` 으로 표시하고 실행 기록에 남긴다
- 문서마다 아래 머리글·꼬리글을 붙인다.

```text
[문서 시작] 파일: <file_name> | 쪽: <N>
<해당 쪽의 텍스트>
[문서 끝] 파일: <file_name> | 쪽: <N>
```

- 쪽을 복원하지 못한 문서는 `쪽: 미상` 으로 적고, 그 문서를 인용한 응답의 `page` 는 **변환 단계에서
  `null` 로 둔다**(1 로 채우지 않는다).
- 조립한 입력의 문자 수와 토큰 수를 실행 기록에 남긴다. 모델 컨텍스트를 넘으면 **그 실행을 중단한다**
  (자동 분할하지 않는다).

## 4. 회사 자가 답변 구획 조립 규칙 — `{{COMPANY_ANSWER}}`

회사 답변(`05_…`)은 ESGenie 에서도 증빙 OCR 경로가 아니라 PDF 텍스트를 직접 추출해 **회사 주장**으로
처리한다(`esgenie/supplychain/claims.py::_extract_text`). 대조군도 같은 함수를 **불러서** 추출한 원문을 쓴다.

- 추출: `esgenie.supplychain.claims._extract_text(pdf_path)` 를 import 해서 호출한다. 다시 구현하지 않는다.
- 반환값의 `\f` 를 쪽 경계로 보고 1-기준 쪽 번호를 붙인다. 파일명·쪽을 유지한다.
- **넣는 것은 추출 원문 그대로다.** ESGenie 가 판단한 결과(`result.json` 의 `self_reports`, 비교 결과 등)를
  넣지 않는다. 요약·정제하지 않는다.
- 증빙 블록과 **표지를 다르게** 해 구획을 분리한다.

```text
[회사 답변 시작] 파일: <file_name> | 쪽: <N>
<해당 쪽의 텍스트>
[회사 답변 끝] 파일: <file_name> | 쪽: <N>
```

- 구획 설명은 "회사가 작성한 자가 답변 초안이며 검증된 증빙이 아니다" 이상을 덧붙이지 않는다.
- 모델이 이 구획을 근거로 인용해도 **출력을 고치지 않는다.** 그런 행 수만 집계해 기록한다.

## 5. 질문 목록 조립 규칙 — `{{QUESTIONS}}`

- 원천: `rba42` 48행의 `qid`·`question_text` 원문
  (`data/eval/cases/baseline_cases_v1.csv` 의 `qid` 순서, `question_text` 값).
- 질문 문구를 고치거나 줄이지 않는다. 함정 유형·정답·라벨은 프롬프트에 넣지 않는다.
- 한 줄에 한 문항:

```text
- qid: <qid> | 질문: <question_text>
```

## 6. A 의 공통 답안 형식 **v1.1**(확정, c1bfa67, 2026-10-10)로의 변환

위 §1·§2 의 모델 지시는 **바뀌지 않았다.** 모델은 그대로 `answer` / `hold` /
`not_applicable` 3값을 낸다. 바뀐 것은 변환기 쪽이다(v1.1: 외부 `decision` 3값 +
내부 `decision_detail` 6값 분리). 아래 표의 오른쪽 열 이름만 `decision` → `decision_detail`
로 바뀌고, 매핑 자체는 그대로다.

변환은 **구조로만** 한다. 모델이 단서를 덧붙여도 판정을 바꾸지 않는다.

| 모델 출력 | `decision_detail` | `decision`(A 표가 계산) |
|---|---|---|
| `decision=answer` + `evidence` 1개 이상 | `confirmed` | `answer` |
| `decision=answer` + `evidence` 없음 | `unverified_submitted` | `answer` |
| `decision=hold` | `hold` | `hold` |
| `decision=not_applicable` | `not_applicable` | `na` |
| 허용값 밖의 `decision` 값 | `unparsed` | `null` |

- **`decision` 은 변환기가 직접 정하지 않는다.** A 모듈의 `DETAIL_TO_DECISION` 표
  (`decision_for_detail`)가 계산한다. 표를 다시 쓰지 않는다.
- **`decision=answer` 라는 이유만으로 확정으로 세지 않는다.** 확정은
  `decision_detail == "confirmed"` 뿐이다.
- 단서 문구("확인이 필요하다" 등)는 `decision_basis`·`source.raw` 에만 남기고 **판정을 바꾸지 않는다.**
- `boundary`: 모델의 `period` → `boundary.period`, `scope` → `boundary.label`.
  `site`·`target`·`denominator`·`completeness` 는 **`null`**(쪼개서 추정하지 않는다).
  v1.1 은 `boundary` 를 원문 요구사항이 아닌 **A 확장**으로 정정했다. B 회신 C-4 로 **유지**가
  확정됐다(모르는 칸은 `null` 보존이라는 조건 그대로).
- `evidence[].page` 는 1-기준 정수 또는 `null`. 쪽을 복원하지 못한 파일의 인용은 `null` 로 둔다.
- `meta.system` = `baseline_<실제 응답 model 식별자>`, `meta.model` = 응답의 실제 model 식별자,
  `meta.run_id` 는 반복마다 다르게,
  `meta.dataset_tag` = **필수**(확정 `virtual`), `meta.material_kind` = 확정 `mixed`
  (A c1bfa67, 2026-10-10 — B 제시값을 A 가 그대로 받았다),
  `meta.data_source` = 단계별 설명 문자열(v1.1 에서 **선택** 필드이고 집계에 쓰지 않는다),
  `meta.source_ref` = 그 반복의 `response_raw.json` 저장소 기준 상대 경로 + sha256,
  `meta.notice` 에 "05 는 회사 자가 답변 원문, 증빙 아님"을 적는다.
- `source.raw` 에 그 문항의 모델 출력 원문 조각을 남긴다.
- **누락 qid 는 행을 만들지 않는다**(v1.1 계약). 보류로 채우지 않고, `run_log.json` 의
  `parse.missing_qid` 에 목록을 남긴다. 검증기가 `missing_qid`·`row_count` 로 드러내면
  그 실패를 **고치지 않고** 기록한다.
- 깨진 JSON·중복 qid 는 **실패로 기록**한다. 보류로 바꾸거나 채워 넣지 않고,
  자동 재시도·프롬프트 수정도 하지 않는다.
- 내보내기 전에 A 의 `validate_document` 를 통과시킨다. 실패하면 고치지 말고 실패 목록을 기록한다.
