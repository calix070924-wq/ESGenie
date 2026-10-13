"""수령 대장의 명시 상태 판독. 상태만으로 완료를 입증하지 않는다."""
import re
from dataclasses import dataclass

PENDING_CELL = re.compile(r'\s*(?:(?:상태|결과|진행)\s*[:：]\s*)?'
                          r'(?:대기|예정|요청|미완료|미확인|계획|필요)(?=\W|중|$)')
STATUS_FIELD = re.compile(r'(?:상태|결과|진행)\s*[:：]\s*([^|\n)）\]】;]*)')
STATUS_LINE = re.compile(r'(?:(?:수령(?:\s*확인)?|전달|배포)\s*)?(?:상태|결과|진행)\s*[:：]')
BODY_STATUS_FIELD = re.compile(r'(?:(?:수령(?:\s*확인)?|전달|배포)\s*)?' + STATUS_FIELD.pattern)
STATUS_HEADER = re.compile(r'(?:(?:수령(?:\s*확인)?|전달|배포)\s*)?(?:상태|결과|진행)')
RECIPIENT_HEADER = re.compile(r'(?:대상|전달\s*대상|수신\s*대상|수신자|거래처|공급사|고객|협력사)')


@dataclass(frozen=True)
class ReceiptLine:
    raw: str
    text: str
    header: str = ''


def receipt_table_lines(text: str) -> list[ReceiptLine]:
    """같은 표의 머리글 위치로 상태 셀을 표시한다. 원문은 별도로 보존한다."""
    result = []
    schema = None
    for raw in text.splitlines():
        # 바깥 파이프는 Markdown 표 테두리이며 빈 데이터 열이 아니다.
        inner = raw.strip()
        if inner.startswith('|'):
            inner = inner[1:]
            if inner.endswith('|'):
                inner = inner[:-1]
        cells = [cell.strip() for cell in inner.split('|')]
        status = [i for i, cell in enumerate(cells) if STATUS_HEADER.fullmatch(cell)]
        recipients = [i for i, cell in enumerate(cells) if RECIPIENT_HEADER.fullmatch(cell)]
        if len(cells) > 1 and status and len(recipients) == 1:
            schema = (cells, status, recipients[0], raw)
            result.append(ReceiptLine(raw, ''))  # 머리글은 실행 기록이 아니다.
            continue
        if recipients and any(re.fullmatch(r'(?:(?:전달|발송|수령)\s*)?(?:기록|내용)|본문', cell)
                              for cell in cells):
            schema = None  # 상태 열이 없는 새 표에도 이전 머리글을 넘기지 않는다.
        if schema and len(cells) == len(schema[0]) and all(
                re.fullmatch(r':?-{3,}:?', cell) for cell in cells):
            result.append(ReceiptLine(raw, ''))
            continue
        if schema and '|' in raw:
            headers, status, recipient, header = schema
            if len(cells) == len(headers):
                # 상태 열이 앞/중간/뒤로 이동해도 대상과 본문을 같은 행에서 읽는다.
                for i in status:
                    if not STATUS_LINE.match(cells[i]):
                        cells[i] = headers[i] + ': ' + cells[i]
                ordered = [cells[recipient], *(cell for i, cell in enumerate(cells) if i != recipient)]
                result.append(ReceiptLine(raw, ' | '.join(ordered), header))
                continue
            # 열 수가 달라졌다면 연결을 추측하지 않는다. 이 행은 완료 근거로 쓰지 않는다.
            result.append(ReceiptLine(raw, '', header))
        else:
            result.append(ReceiptLine(raw, raw))
        # 빈 줄, 새 문단, 다른 표는 이전 머리글의 적용 범위 밖이다.
        schema = None
    return result


def status_unresolved(value: str) -> bool:
    """명시적 상태 값의 부정·미완료를 읽는다. 완료 입증에는 쓰지 않는다."""
    states = r'(?:미\s*(?:확인|완료|수령)|대기|예정|요청|계획|필요|불가능|불가|불능|불확실|불명확)'
    # ‘대기 없이’, ‘요청하지 않음’, ‘미확인이 아님’은 그 상태의 부정이다.
    # 이 관계를 먼저 제거해 뒤의 완료를 막는 부정 술어로 뒤집지 않는다.
    cancelled = re.compile(states + r'\s*(?:[이가은는을를]\s*)?'
                           r'(?:상태\s*)?(?:없이|없(?:다|음|습니다)?|아니(?:다|며|라|라는)?|아님|아닌'
                           r'|하지\s*(?:않|못)(?:다|음|았(?:다|음))?)')
    remaining = cancelled.sub('', value)
    return bool(re.search(states + r'(?=\W|중|상태|[이가은는을를]|$)', remaining)
                or re.search(r'지\s*(?:않|못)|(?:확인|수령|완료)\s*없(?:다|음|습니다)?'
                             r'|(?:확인|수령|완료)\s*(?:안\s*(?:됨|됐|되|함|했|하)'
                             r'|못\s*(?:함|했|하)|아님|아니(?:다|었|라|며)|아닌)', remaining))


def row_unresolved(quote: str, pending_state: re.Pattern = PENDING_CELL) -> bool:
    """표의 명시적 상태 값과 독립 상태 셀만 읽고 다른 설명은 섞지 않는다."""
    for cell in re.split(r'[|\n]', quote):
        fields = list(STATUS_FIELD.finditer(cell))
        if fields:
            if any(status_unresolved(field[1]) for field in fields):
                return True
        elif pending_state.match(cell) and status_unresolved(cell):
            return True
    return False


def receipt_rows(text: str) -> list[str]:
    """OCR 다음 줄의 상태는 직전 행에 붙이고 다른 행은 분리한다."""
    rows = []
    for entry in receipt_table_lines(text):
        line = entry.text
        if rows and (STATUS_LINE.match(line.strip()) or PENDING_CELL.match(line)):
            rows[-1] += '\n' + line
        else:
            rows.append(line)
    return rows


def receipt_body(row: str) -> str:
    """상태의 라벨·값·빈 칸을 분리하고 실제 본문 칸 사이의 경계는 유지한다."""
    cells = [BODY_STATUS_FIELD.sub('', cell) for cell in row.split('|')]
    return '|'.join(cell for cell in cells if cell.strip())
