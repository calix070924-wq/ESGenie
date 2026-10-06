# 공통 답안 형식 v1 — 합성 예시 (발췌)

이 디렉터리의 JSON은 **형식을 보여 주기 위한 합성 발췌**다.

- 값·근거·페이지는 **임의로 만든 자리값**이다. ESGenie의 실제 출력도, 정답 라벨도 아니다.
  실제 정답 라벨은 사람이 원본 증빙을 보고 작성하며 `data/eval/labels/`에만 둔다.
- 행이 3개뿐이다. **정상 실행 한 건은 48행**(rba42 문항 수)이다. 48행 문서가 검증을
  통과하는 것은 `tests/test_eval_answer_format.py::test_rba42_document_of_48_rows_validates`로
  고정해 두었다(문항 ID는 양식에서 읽는다).
- 형식 정의는 `docs/공통답안형식_v1초안_2026-10-06.md`다. **v1 초안 / 합의 대기** 상태다.

| 파일 | 무엇을 보여 주는가 |
|---|---|
| `common_v1_esgenie_excerpt.json` | ESGenie `result.json`을 어댑터로 변환한 모습. `source`에 `status`·`comparison`이 보존되고 페이지가 1-기준으로 바뀌어 있다 |
| `common_v1_control_excerpt.json` | 일반 AI 대조군이 직접 채우는 모습. ESGenie 전용 `status`가 없어도 된다. 페이지·인용문을 모르면 `null`로 두고, 출력을 읽을 수 없었던 문항은 `unparsed`로 남긴다 |

검증 방법:

```bash
python -c "
from esgenie.eval import answer_format as af
from esgenie.supplychain.frameworks import get_framework
qids = tuple(q.qid for q in get_framework('rba42').questions)
for p in ('data/eval/examples/common_v1_esgenie_excerpt.json',
          'data/eval/examples/common_v1_control_excerpt.json'):
    doc = af.load_document(p)
    # 발췌라 행 수가 48이 아니다 → 문항 목록은 넘기지 않고 형식만 본다.
    print(p, af.validate_document(doc) or 'OK')
"
```
