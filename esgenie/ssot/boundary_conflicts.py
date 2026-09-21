"""실적과 설비 계획의 관계를 확인할 필요가 있는 원문을 보존한다."""
import re

from .boundary import derive_boundary, detect_measure


def renewable_review_notes(extraction):
    """같은 에너지원의 실적과 뒤늦은 착공이 공존하면 설비 동일성을 확인한다.

    서로 다른 설비가 명시된 경우만 충돌 후보에서 제외한다. 설비 부재/허위 실적을
    단정하는 검출기가 아니므로 원문 위치와 확인할 일을 반환한다.
    """
    notes = []
    seen_conflicts = set()
    plans = []
    text = extraction.raw_text or "\n".join(c.text for c in extraction.clauses)
    for line in text.splitlines():
        if not re.search(r"착공|건설\s*예정|설치\s*예정", line):
            continue
        kind, _ = detect_measure(line)
        # 착공 이후의 '연간 발전 목표'는 착공일이 아니다. 행 전체의 기간어를
        # 합치지 않고 실제 착공 절까지를 사용한다.
        construction = re.split(r"착공|건설\s*예정|설치\s*예정", line, maxsplit=1)[0]
        plans.append((line, kind, derive_boundary(construction, construction)))
    for metric in extraction.metrics:
        actual = derive_boundary(metric.metric_hint, metric.period, unit=metric.unit)
        if actual.basis != "actual" or not actual.measure_kind.startswith("electricity_"):
            continue
        for line, kind, plan in plans:
            if kind != actual.measure_kind or not plan.period_start or not actual.period_end:
                continue
            if plan.period_start <= actual.period_end:
                continue
            # 설비 ID는 같은 형태의 명시적 식별자끼리만 비교한다.
            pattern = r"(?:설비|발전소|호기)\s*[:#-]?\s*([A-Za-z0-9]+)"
            existing, future = re.search(pattern, metric.metric_hint), re.search(pattern, line)
            if existing and future and existing[1] != future[1]:
                continue
            key = (actual.measure_kind, actual.period_start, actual.period_end, line,
                   existing[1] if existing else "")
            if key in seen_conflicts:
                continue
            seen_conflicts.add(key)
            page = f"p.{metric.page+1}" if metric.page is not None else "위치 미확인"
            notes.append(f"실적/계획·설비 동일성 확인 필요 ({extraction.source_file} {page}): "
                         f"{metric.metric_hint} {metric.value}{metric.unit} ({metric.period}) / {line.strip()}. "
                         "기존 설비 또는 동일 설비인지 확인")
    for line in text.splitlines():
        if re.search(r"그린\s*프리미엄", line) and re.search(r"\bPPA\b|전력구매계약", line, re.I):
            notes.append(f"조달수단 확인 필요 ({extraction.source_file}): {line.strip()} — 그린프리미엄/PPA 계약·사용량 증빙 확인")
    return tuple(dict.fromkeys(notes))
