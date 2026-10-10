"""RBA 문항 요건별 근거 판정. 코드 태깅은 후보이고 충족 판정은 별개다.

원문 조항을 여러 문항의 후보로 읽는다. 파일명·회사명·페이지 번호는 판정에
사용하지 않는다. 각 요건은 주제와 실행/규정 관계를 함께 요구하며, 부재·한계
문장은 원문 참조로 보존한다. Answer의 기존 필드만 사용한다.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, replace
from collections import defaultdict

from ..ssot.audit_trace import evidence_link
from ..ssot.boundary import Boundary, detect_site
from ..survey import is_survey


@dataclass(frozen=True)
class Requirement:
    label: str
    patterns: tuple[str, ...]

    def matches(self, text: str) -> bool:
        return all(re.search(p, text, re.I | re.S) for p in self.patterns)


def R(label, *patterns):
    return Requirement(label, patterns)


# 실행 기록의 날짜·시간을 원문 표기 그대로 읽는다. 한글·하이픈·슬래시·
# 마침표 표기를 지원하며 문서 번호나 숫자 열을 날짜로 간주하지 않는다.
_RECORD_TIME = (r'(?<!\d)(?:20\d{2}\s*[년./-]\s*)?'
                r'(?:1[0-2]|0?[1-9])\s*[월./-]\s*(?:3[01]|[12]\d|0?[1-9])\s*일?(?!\d)'
                r'|(?<!\d)(?:2[0-3]|1?\d)\s*[:시]\s*[0-5]\d\s*분?(?!\d)')


# RBA 정의의 요건을 분해한다. 이 표는 가상 세트의 정답/출처 목록이 아니다.
REQUIREMENTS = {
    'A-3': (
        R('주간 근로시간 한도 준수', r'근로시간|노동시간|working hours', r'상한|한도|초과\s*금지|준수|최대'),
        R('연장근로 자발성', r'연장|초과근무|overtime', r'자발|동의|voluntary'),
        R('7일당 1일 휴무 보장', r'휴무|휴일|rest day', r'7일|매주|주\s*1|일주일'),
        R('법정 한도 준수', r'법정|법적|법률|법규', r'근로|노동|working', r'한도|준수'),
    ),
    'B-6': (
        R('기계 안전 위험 평가', r'기계|설비|방호|machine', r'위험|risk', r'평가|assessment', r'끼임|말림|접촉|낙하|위험등급|severity|hazard'),
        R('방호장치 설치·기능', r'방호|인터록|interlock|guard', r'설치|고정|차단|기능|install'),
        R('방호장치 전수 점검', r'방호|인터록|interlock|guard', r'점검|시험|확인|inspect|test', r'전수|누락.*없|모든.*(?:기계|설비)|all.*machines'),
        R('방호장치 유지·보수 실행', r'방호|덮개|인터록|guard', r'예방정비|재체결|정비.*완료|유지보수.*기록|maintenance.*completed'),
    ),
    'B-8': (
        R('근로자 언어로 안전보건 정보·교육 제공', r'근로자|근무자|직원|임직원|작업자', r'안전|보건', r'언어|한국어|모국어|번역'),
        R('안전보건 정보 게시', r'안전|보건', r'게시'),
        R('작업 전·정기 안전교육', r'안전|보건', r'교육', r'작업\s*전|정기'),
        R('보복 없는 의견제기', r'보복', r'금지|없이|없는|하지\s*않', r'의견|제기|신고'),
    ),
    'C-7': (
        R('수원·용수 사용·배출 모니터링', r'수원|용수|물\s*사용|취수', r'배출|폐수', r'모니터링|측정|특성|추적'),
        R('절수 기회 발굴', r'절수|물\s*절약|용수\s*절감', r'기회|계획|발굴|개선|실시'),
        R('오염경로 통제', r'오염', r'경로|통제|차단'),
        R('폐수 처리·설비 점검', r'폐수', r'처리', r'설비|점검|성능'),
    ),
    'C-8': (
        R('온실가스 감축목표', r'온실가스|탄소|GHG', r'감축', r'목표'),
        R('Scope 1 추적', r'scope\s*1|직접배출', r'추적|산정|집계|측정|관리'),
        R('Scope 2 추적', r'scope\s*2|간접배출', r'추적|산정|집계|측정|관리'),
        R('Scope 3 추적', r'scope\s*3|가치사슬', r'추적|산정|집계|측정|관리'),
        R('온실가스 공개', r'온실가스|탄소|GHG', r'공개|공시|보고서'),
    ),
    'E-2': (
        R('책임·권한 지정', r'경영|대표이사|ESG|management', r'책임|담당|responsib', r'권한|자원\s*배정|승인권|authority'),
        R('경영진 정기 검토 절차', r'경영진|대표이사|management', r'정기|월\s*1회|분기|연\s*1회', r'검토|회의|review'),
        R('경영진 검토 실시 기록', r'검토|회의|review', r'실시|완료|확정|held|completed', _RECORD_TIME),
    ),
    'E-3': (
        R('법규·규정 식별', r'법규|법률|규제|legal', r'식별|목록|등록|identify'),
        R('고객 요구사항 식별', r'고객', r'요구사항|행동규범|요건', r'식별|목록|등록'),
        R('요구사항 모니터링·이해', r'법규|규제|요구사항|요건', r'모니터링|개정\s*확인|변경\s*검토|이해|monitor'),
    ),
    'E-4': (
        R('법규·환경·노동·윤리 리스크 식별', r'법규', r'환경', r'노동|인권', r'윤리', r'리스크|위험', r'식별|평가'),
        R('중요도 평가', r'리스크|위험|영향', r'중요도|중대성|우선순위'),
        R('리스크 통제', r'리스크|위험|영향', r'통제|완화|관리\s*조치'),
    ),
    'E-6': (
        R('관리자·근로자 교육 프로그램', r'교육|training', r'프로그램|계획|실시|출석|이수', r'관리자|담당자|근로자|임직원|근무자'),
        R('방침·절차 이행 교육', r'교육|training', r'방침|절차|규정|안전|윤리|법규', r'실시|출석|이수|프로그램'),
    ),
    'E-7': (
        R('방침·관행·기대·성과 전달 절차', r'방침', r'관행', r'기대', r'성과', r'전달|공유|배포'),
        R('근로자 전달·수령', r'근로자|근무자|직원|임직원|종업원', r'전달|게시|열람|수령|배포'),
        R('공급사 전달·수령', r'공급사|공급자|협력사', r'전달|발송|수령|제공|배포'),
        R('고객 전달·수령', r'고객', r'전달|발송|수령|제공|배포'),
        R('개별 전달 확인 기록', r'근무자별|인원별|수신자별|전자 열람', r'수령|열람|이해', r'확인|완료|기록'),
        R('공급사·고객 전달 운영', r'공급사|공급자|협력사', r'고객',
          r'수령[^\n.!?。;]{0,30}확인|(?:전달|배포)[^\n.!?。;]{0,30}완료',
          r'\d+\s*곳|(?:공급사|공급자|협력사)[^\n.!?。;]{0,60}고객[^\n.!?。;]{0,60}수령[^\n.!?。;]{0,30}확인(?:했다|하였다|했습니다)'),
        R('문의·정정 회신', r'문의|정정', r'회신|답변|전달'),
    ),
    'E-8': (
        R('양방향 참여', r'근로자|근무자|이해관계자|대표', r'양방향|간담회|의견.*(?:수렴|반영)|협의'),
        R('고충·구제 접근', r'고충|구제|익명\s*접수', r'접근|접수|처리|신고'),
        R('보복 없는 참여', r'보복', r'금지|없는|없이|하지\s*않'),
    ),
    'E-10': (
        R('발견·접수 절차', r'점검|평가|조사', r'발견|접수|등록', r'미흡|시정|조치'),
        R('담당·기한·조치', r'담당|배정', r'기한', r'조치|시정'),
        R('효과 확인·종결 실행', r'효과|검증', r'확인', r'기한\s*내\s*종결|종결(?:했다|하였|하였다|\s*완료)|효과.{0,20}확인.{0,15}(?:마쳤|완료)|closed|completed', r'20\d{2}|\d{2}-\d{2}|completed'),
    ),
    'E-11': (
        R('문서·기록 생성·승인', r'문서|기록|원본', r'생성|작성|등록', r'승인|확인'),
        R('버전·원본 유지', r'원본|문서', r'버전|개정|정정', r'보관|유지|등록'),
        R('접근·개인정보 보호', r'원본|문서|기록|개인정보', r'접근|권한|실명', r'제한|보호|분리|차단|가린'),
        R('보존·폐기 절차', r'기록|문서|보존', r'보존기간|보존\s*기간', r'폐기|승인'),
        R('백업·복구 절차', r'백업', r'복구', r'확인|대조'),
        R('보호·복구 운영 점검', r'복구', r'원본.{0,80}(?:문자|페이지|내용).{0,30}일치|원본.{0,60}복구.{0,40}대조.{0,15}(?:했다|하였다)|복구.{0,40}성공', r'20\d{2}|\d{2}-\d{2}|completed'),
    ),
}

# 부정어 자체가 아니라 '입증하지 않는 사실/요건'을 제외한다. 보복·우회 금지 등은
# 보호/통제 요건의 긍정 근거이므로 여기서 제거하지 않는다.
_UNSUPPORTED = re.compile(
    r'(?:아니[다며]|아님|없(?:다|음|으며|어)|미보유|미실시|미확인|대신하지|대체할\s*수\s*없|'
    r'지\s*(?:않|못)|입증하지\s*못|확정하지|의미하지|계획이며|향후\s*일정|예정이다)', re.I)
_PROTECTIVE = re.compile(r'보복\s*(?:없|없는|없이)|이상.{0,12}없|누락.{0,12}없|고장.{0,12}없|'
                         r'구서식.{0,12}없|접근권한.{0,12}없|보복(?:을)?\s*하지\s*않\S*')


def supported_parts(text):
    parts = re.split(r'(?<=[.!?。;])\s+|(?<=이며)\s*|(?<=지만)\s*|'
                     r'(?<=않으며)\s*|(?<=않았으며)\s*|\n(?=[^\n]*[|:])', text or '')
    return '\n'.join(p for p in parts if not _UNSUPPORTED.search(_PROTECTIVE.sub('', p)))


def requirement_code(q):
    if q.qtype not in ('yes_no', 'yes_no_evidence'):
        return ''
    return next((c for c in q.kesg_codes if c in REQUIREMENTS), '')


def scoped_policy_failures(q, failures):
    """우산 검사의 실패 중 해당 RBA 요건에 관련된 실패만 선택한다."""
    code = requirement_code(q)
    if not code:
        return failures
    selected = {}
    for audit_code in q.kesg_codes:
        row = failures.get(audit_code)
        if not row:
            continue
        if audit_code == code:
            selected[audit_code] = row
            continue
        findings = []
        for finding in row.get('findings', []):
            if finding.get('status') == 'met':
                continue
            text = ' '.join(str(finding.get(k) or '') for k in ('description', 'clause_id'))
            if any(re.search(r.patterns[0], text, re.I) for r in REQUIREMENTS[code]):
                findings.append(finding)
        if findings:
            selected[audit_code] = dict(row, findings=findings)
    return selected


def assess_requirements(answer, q, graph):
    """원문 요건 충족과 부분 자료를 분리한다. 명시적인 아니오는 보존한다."""
    code = requirement_code(q)
    if graph is None:
        return answer
    if not code:
        if q.qtype in ('yes_no', 'yes_no_evidence', 'text') and answer.evidence_links:
            _scope_from_sources(answer, getattr(graph, 'source_texts', {}))
        return answer
    requirements = REQUIREMENTS[code]
    related, proofs, contexts = {}, defaultdict(list), defaultdict(list)
    node_scopes = {}
    section_parts = defaultdict(list)
    source_texts = getattr(graph, 'source_texts', {})
    for proof_id, node in _candidate_nodes(graph):
        if is_survey(node):
            continue
        contexts[node.source_file].append(f'{node.section}\n{node.text}')
        supported = supported_parts(node.text)
        # 교육 프로그램의 대상·주제는 같은 페이지의 원문 머리말과 함께 읽는다.
        # 문서 등록표가 교육 실적을 열거한 것은 교육 실시 기록으로 재해석하지 않는다.
        header = '\n'.join(line for line in source_texts.get(node.source_file, '').splitlines()[:12]
                           if not re.search(r'가상|FICTIONAL|제작|개편|시연용', line, re.I))
        if code in ('E-6', 'B-8'):
            education_document = bool(re.search(r'^(?!.*(?:문서|등록|보관)).{0,16}교육.*(?:실시|프로그램|계획)',
                                               header, re.M))
            direct_education = bool(re.search(r'교육|훈련|training', node.section, re.I)
                                    and not re.search(r'등록|보관|전달|수령|명부|문서', node.section)
                                    and re.search(r'프로그램|실시|출석|이수|진행했|안내했', supported))
            if not education_document and not direct_education and code == 'E-6':
                supported = ''
            elif re.search(r'해야|예정|향후|필요', supported) and not re.search(r'실시하였|진행했|안내했|출석하였다', supported):
                supported = ''
            elif education_document and node.page == 0 and supported.strip():
                supported = supported_parts(header) + '\n' + supported
        # 제목은 후보 주제일 뿐 실행/규정의 근거가 아니다. 제거된 부정문의
        # 주어를 제목에서 다시 가져와 다른 문장의 실행 동사와 결합하지 않는다.
        body = supported
        hits = [r for r in requirements if body and r.matches(body)]
        boundary = _node_scope(node, source_texts.get(node.source_file, ''))
        if body.strip():
            # 표의 대상/수량 설명과 실제 수령 행은 별도 OCR 조항일 수 있다.
            # 동일 문서·절·적용 범위의 본문만 연결하며 제목은 입증에 사용하지 않는다.
            section_parts[(node.source_file, node.section,
                           _scope_key(boundary, node.source_file))].append(
                               (proof_id, node, body, boundary))
        # 태그만 일치한 부분 자료는 원문 참조로 남긴다.
        link = evidence_link(node)
        if hits:
            related[proof_id] = replace(link, quote=supported)
            node_scopes[proof_id] = boundary
            for req in hits:
                proofs[req.label].append(proof_id)
        elif code in link.kesg_codes:
            related[proof_id] = link
    for parts in section_parts.values():
        body = '\n'.join(part[2] for part in parts)
        for req in requirements:
            if not req.matches(body):
                continue
            for proof_id, node, supported, boundary in parts:
                if not any(re.search(p, supported, re.I | re.S) for p in req.patterns):
                    continue
                related[proof_id] = replace(evidence_link(node), quote=supported)
                node_scopes[proof_id] = boundary
                if proof_id not in proofs[req.label]:
                    proofs[req.label].append(proof_id)
    # 한 문서가 전체 요건을 입증하면 그 문서의 운영 기록을 우선한다. 부수 문서의
    # 요약·안내 한 줄이 필수 운영 기록을 대체하거나 범위를 넓히지 않게 한다.
    # 파일이 같아도 조항의 적용 기간·사업장이 다를 수 있다. 요건을 동일한
    # 범위별로 모으며, 한 범위에 전체 요건이 있어야 '예'를 만들 수 있다.
    coverage, source_coverage = defaultdict(set), defaultdict(set)
    for label, ids in proofs.items():
        for nid in ids:
            source = related[nid].file_name
            key = _scope_key(node_scopes[nid], source)
            coverage[key].add(label)
            source_coverage[(source, key)].add(label)
    complete_source_scopes = {key for key, labels in source_coverage.items()
                             if len(labels) == len(requirements)}
    complete_scopes = {key for key, labels in coverage.items() if len(labels) == len(requirements)}
    if complete_scopes:
        def in_complete_scope(nid):
            source = related[nid].file_name
            key = _scope_key(node_scopes[nid], source)
            return ((source, key) in complete_source_scopes if complete_source_scopes
                    else key in complete_scopes)
        proofs = {label: [nid for nid in ids if in_complete_scope(nid)] for label, ids in proofs.items()}
    missing = [r.label for r in requirements if not proofs.get(r.label)]
    if not missing and not complete_scopes:
        missing.append('동일 기간·사업장에 대한 요건 연결')
    proof_ids = {nid for ids in proofs.values() for nid in ids}
    complete_sources = {source for source, key in complete_source_scopes}
    answer.evidence_links = [related[nid] for nid in sorted(proof_ids)]
    answer.reference_links = [link for nid, link in related.items() if nid not in proof_ids
                              and (not complete_sources or link.file_name in complete_sources)]
    # 명시적 부정 응답과 충돌 상태를 덮어쓰지 않는다.
    if answer.value is not False:
        answer.value = None if missing else True
        answer.status = 'insufficient' if missing else 'verified'
        answer.rationale = ('미확인 요건: ' + ' / '.join(missing) if missing else
                            '문항 요건 확인: ' + ' / '.join(r.label for r in requirements))
        if missing:
            answer.evidence_needed = missing
    if answer.value is False and not missing:
        answer.status = 'flagged'
        answer.flags.append('부정 응답과 해당 문항의 독립 근거가 충돌함 — 원문 검토 필요')
    answer.scope_notes.append('관련 자료 연결과 문항 전체 요건 충족은 별도로 판정함')
    contexts = {source: source_texts.get(source) or '\n'.join(parts)
                for source, parts in contexts.items()}
    link_scopes = {(link.file_name, link.node_id, link.quote): node_scopes[nid]
                   for nid, link in related.items() if nid in node_scopes}
    _scope_from_sources(answer, contexts, link_scopes)
    return answer


def _source_scope(text):
    # 사업장·기간을 담은 문서 머리말만 읽는다. 본문의 다른 지표/다른 사업장
    # 참조와 제작일을 적용 범위로 재해석하지 않는다.
    lines = (text or '').splitlines()
    header = []
    for line in lines[:12]:
        if re.search(r'가상|FICTIONAL|제작|개편|시연용', line, re.I):
            continue
        if re.search(r'20\d{2}|제\d+공장|두\s*공장|양\s*공장', line) or _sites(line)[1] != 'unknown':
            if re.search(r'아니|확정하지|없|다른', line):
                continue
            header.append(line)
    # 월간 운영대장은 첫 페이지의 시행일과 다른 축이다. 명시적인 월 표기를
    # 별도로 보존하며 일자 두 개의 min/max를 기간 합계로 만들어내지 않는다.
    months = re.findall(r'20\d{2}\s*년\s*\d{1,2}\s*월\s*(?:기록|대장|실적)', text or '')
    header.extend(months)
    context = '\n'.join(header)
    date_parts = []
    for line in header:
        for part in re.split(r'[|]|(?<!\d)/|/(?!\d)', line):
            if re.search(r'20\d{2}\s*[-./년]', part):
                date_parts.append(part.strip())
    sites, site_scope = _sites(context)
    for line in lines:
        if re.search(r'가상|FICTIONAL|제작|개편|아니|확정하지|다른', line, re.I):
            continue
        match = re.match(r'(?:20\d{2}\s*년\s*)?\d{1,2}월(?:\s*중|[^.]{0,30}(?:기록|접수|검토|대장))', line.strip())
        if match:
            date_parts.append(match[0])
    sites = [site for site in sites if not any(site != other and site in other for other in sites)]
    period = '; '.join(dict.fromkeys(date_parts))
    return Boundary(period_text=period, site=', '.join(sites),
                    site_scope=site_scope,
                    completeness='partial' if period or sites else 'unknown',
                    source_quote=context)


def _sites(text):
    # 공용 파서의 사업장/전사 판정을 사용하고, 원문의 복수 공장은 모두 보존한다.
    if detect_site(text)[1] == 'entity':
        return ['전사'], 'entity'
    sites = re.findall(r'(?:[가-힣]+\s*)?제\s*\d+\s*공장|두\s*공장(?:\s*전체)?|양\s*공장', text)
    # 정성 문장에서 '노동', '이동'을 건물의 '동'으로 읽지 않는다.
    for match in re.finditer(r'[가-힣A-Za-z0-9]{1,12}(?:공장|사업장)', text):
        site, scope = detect_site(match[0])
        if scope == 'site' and not re.search(r'제\s*\d+\s*공장', site):
            sites.append(site)
    sites = list(dict.fromkeys(sites))
    sites = [site for site in sites if not any(site != other and site in other for other in sites)]
    return sites, 'site' if sites else 'unknown'


_LOCAL_PERIOD = re.compile(r'20\d{2}\s*년\s*\d{1,2}'
                           r'(?:\s*[~–-]\s*\d{1,2})?\s*월(?:\s*\d{1,2}\s*일)?'
                           r'|20\d{2}[./-]\d{1,2}(?:[./-]\d{1,2})?')
_SCOPE_FIELD = re.compile(r'(?:대상|적용|보고|집계|운영)\s*(?:기간|범위)|사업장\s*[:：]')


def _scope_prefix(line):
    prefix = re.split(r'[:：]', line.strip(), maxsplit=1)[0]
    period = _LOCAL_PERIOD.search(prefix)
    sites, scope = _sites(prefix)
    if not period or scope == 'unknown':
        return False
    remainder = prefix[:period.start()] + prefix[period.end():]
    for site in sorted(sites, key=len, reverse=True):
        remainder = remainder.replace(site, '')
    # 날짜·사업장을 언급한 실행 문장을 적용 범위 선언으로 읽지 않는다.
    remainder = re.sub(r'기준|대상|적용|기간|범위|전체|모든|사업장|전사|[\s/|·,()\[\]-]', '', remainder)
    return not remainder


def _scope_declaration(line):
    return _scope_prefix(line) or bool(_SCOPE_FIELD.search(line))


def _candidate_nodes(graph):
    # OCR가 여러 적용 범위를 한 조항으로 묶어도 각 범위의 요건을 따로 읽는다.
    # 내부 후보 키만 나누며 공용 링크의 실제 node_id·페이지는 변경하지 않는다.
    for node in graph.text_nodes.values():
        chunks, lines, has_body = [], [], False
        for line in re.split(r'\n|(?<=[.!?。;])\s+', node.text):
            declaration = _scope_declaration(line)
            # 연속된 기간/사업장 메타데이터는 하나의 범위다. 본문 뒤의 새
            # 선언부터 별도 후보로 읽어 앞선 요건에 마지막 범위가 덮이지 않게 한다.
            if has_body and declaration:
                chunks.append('\n'.join(lines))
                lines = []
                has_body = False
            lines.append(line)
            if line.strip() and not declaration:
                has_body = True
            elif _scope_prefix(line) and re.search(r'[:：]\s*\S', line):
                has_body = True
        chunks.append('\n'.join(lines))
        for index, text in enumerate(chunks):
            yield (node.id, index), replace(node, text=text)


def _node_scope(node, source_text):
    """조항에 명시된 적용 범위가 문서 머리말보다 우선한다. 실행일은 별도다."""
    default = _source_scope(source_text)
    period, sites, scope, quotes = '', [], 'unknown', []
    for line in f'{node.section}\n{node.text}'.splitlines():
        if re.search(r'가상|FICTIONAL|제작|개편|아니|확정하지|다른', line, re.I):
            continue
        # 월/기간·사업장을 함께 선언한 접두부 또는 명시적인 메타데이터만
        # 적용 범위로 읽는다. 회의 실시일·정비일은 문서 적용 기간을 덮지 않는다.
        prefix = re.split(r'[:：]', line.strip(), maxsplit=1)[0]
        local_sites, local_scope = _sites(prefix)
        if not _scope_declaration(line):
            continue
        match = _LOCAL_PERIOD.search(line)
        if match:
            period = match[0]
        if local_scope != 'unknown':
            sites, scope = local_sites, local_scope
        elif _SCOPE_FIELD.search(line):
            local_sites, local_scope = _sites(line)
            if local_scope != 'unknown':
                sites, scope = local_sites, local_scope
        quotes.append(line)
    return replace(default, period_text=period or default.period_text,
                   site=', '.join(sites) if sites else default.site,
                   site_scope=scope if sites else default.site_scope,
                   source_quote='\n'.join(quotes) if quotes else default.source_quote)


def _scope_key(boundary, source):
    period = re.sub(r'\s+|기록|대장|실적', '', boundary.period_text)
    period = re.sub(r'(?:대상|적용|보고|집계|운영)기간[:：]?', '', period)
    period = re.sub(r'(20\d{2})년(\d{1,2})월(\d{1,2})일',
                    lambda m: f'{m[1]}-{int(m[2]):02d}-{int(m[3]):02d}', period)
    period = re.sub(r'(20\d{2})년(\d{1,2})월',
                    lambda m: f'{m[1]}-{int(m[2]):02d}', period)
    site = ','.join(sorted(re.sub(r'\s+', '', part) for part in boundary.site.split(',')))
    # 미확인 경계를 서로 다른 문서의 동일 범위라고 간주하지 않는다.
    return (period, boundary.site_scope, site,
            source if not period or boundary.site_scope == 'unknown' else '')


def _scope_from_sources(answer, contexts, link_scopes=None):
    boundaries = []
    links = answer.evidence_links or answer.reference_links
    for link in links:
        boundary = ((link_scopes or {}).get((link.file_name, link.node_id, link.quote))
                    or _source_scope(contexts.get(link.file_name, '')))
        if boundary.is_known and boundary.label() not in [b.label() for b in boundaries]:
            boundaries.append(boundary)
    labels = [b.label() for b in boundaries]
    answer.boundary_label = ' / '.join(labels)
    answer.completeness = 'partial' if labels else 'unknown'
    answer.boundary = boundaries[0].to_dict() if len(boundaries) == 1 else {}
    if len(boundaries) > 1:
        answer.scope_notes.append('근거별 기간·사업장 범위가 다름: ' + ' / '.join(labels))
    if not labels:
        answer.scope_notes.append('원문에서 기간·사업장 범위 미확인')
