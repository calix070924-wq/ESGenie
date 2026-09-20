"""문항 적합성 — 근거가 '이 질문에 답하는 내용인가'를 선별한다(2026-09-20 §6).

grounding과 다른 축이다.
  · grounding(rag_gates.grounding_gate) : 생성된 초안의 문장이 근거에 있는 말인가.
  · 문항 적합성(여기)                    : 그 근거가 이 질문에 답하는 내용인가.

둘을 한 게이트로 묶으면 "근거에 있는 말이지만 질문과 무관한 답"이 통과한다. E-7
의사소통 문항에 노사협의·단결권·핫라인·ISMS 문장을 이어 붙인 초안이 승인 대기까지
올라간 자리가 여기였다(2026-09-20 HMC 응답서 실측). 코드 태깅(code_match)은 "이
문장이 코드 X 주제"만 말해주고 "이 문항에 답한다"는 말해주지 않는다 — HMC 48문항 중
10문항이 S-4-1 하나를 공유하므로, 코드만 믿으면 열 문항이 같은 근거를 받는다.

판정 기준: **문항 고유 어휘(distinctive term)**
-----------------------------------------------
양식 안에서 그 문항을 다른 문항과 구별해주는 말만 판정에 쓴다. 양식 전체의 문항
빈도(df)로 계산한다 — HMC에서 '근로자'는 48문항 중 8문항에, '의사소통'은 2문항에
나온다. 앞은 어느 문항에나 있어 적합성을 가르지 못하고, 뒤는 이 문항의 말이다.

  근거가 고유 어휘를 하나도 담지 않으면 그 근거는 이 문항의 근거가 아니다.

이 규칙은 모든 문항·모든 양식에 같다. 특정 문항의 파일명·노드 ID 화이트리스트나
무관 단어 블랙리스트를 두지 않는다. LLM 호출도 하지 않는다 — 결정적이라 대조군
실험으로 검증할 수 있다.
"""
from __future__ import annotations

import math
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

# 양식 문항 중 이 비율 이상에 나오는 말은 '흔한 말'로 보고 판정에서 뺀다.
# df==1(한 문항에만 나오는 말)은 양식 크기와 무관하게 항상 고유 어휘다 — 문항 수가
# 적은 양식(saq5_env 등)에서 고유 어휘가 0이 되지 않게.
DISTINCTIVE_DF_RATIO = 0.15
# 최고 청크 대비 이 비율 미만인 청크는 버린다 — 관련 청크 하나가 무관한 청크 전체를
# 끌고 들어오지 않게(§6).
RELATIVE_FLOOR = 0.34

_TOKEN = re.compile(r"[0-9A-Za-z가-힣]+")
# 조사·어미 — 문항의 '성과를'과 근거의 '성과는'을 같은 말로 본다.
_SUFFIXES = (
    "으로써", "로부터", "에서의", "에게는", "에게도", "에게", "으로", "까지", "부터",
    "에서", "에는", "에도", "이나", "하는", "한다", "하며", "하고", "되는", "된다",
    "들의", "들을", "들이", "와의", "과의", "로서", "로써",
    "를", "을", "이", "가", "은", "는", "의", "에", "도", "만", "와", "과", "로", "나",
)
# 문항 종류를 가리지 않고 쓰이는 말 — df 계산 전에 뺀다. 이 말들은 어느 사내문서에나
# 있어서, 남겨두면 무관한 문서가 '운영'·'기록' 하나로 적합 판정을 받는다.
_STOPWORDS = frozenset({
    "관련", "대한", "대해", "위한", "위해", "있는", "없는", "여부", "또는", "그리고",
    "해당", "각각", "모든", "등을", "등의", "경우", "내용", "사항", "확인", "제출",
    "올려주세요", "올리면", "자동", "검토", "필요", "문항", "따라", "통해", "포함",
    "운영", "관리", "수립", "체계", "기록", "현황", "자료", "문서", "기준", "실시",
    "이행", "적용", "구성", "명확", "정확히", "지속", "대상", "방법", "결과",
})
_MIN_TERM_LEN = 2


def _strip_suffix(token: str) -> str:
    """조사·어미를 한 겹 벗긴다. 짧은 토큰은 건드리지 않는다."""
    if len(token) < 3:
        return token
    for suf in _SUFFIXES:
        if len(token) - len(suf) >= 2 and token.endswith(suf):
            return token[: -len(suf)]
    return token


def _tokens(text: str) -> list[str]:
    return [_strip_suffix(t) for t in _TOKEN.findall(text or "")]


def terms_for(question_text: str, evidence_types: Sequence[str] = ()) -> set[str]:
    """한 문항의 어휘. 문항 텍스트 + 증빙요구 문서 유형에서 뽑는다.

    문항 코드 접두사([E-7] 등)는 어휘가 아니라 라벨이므로 뺀다 — 코드 문자열이 본문에
    있다는 이유로 무관한 근거가 통과하지 않게.
    """
    body = re.sub(r"^\s*\[[^\]]+\]\s*", "", question_text or "")
    raw = _tokens(body) + [t for et in evidence_types for t in _tokens(et)]
    return {t for t in raw
            if len(t) >= _MIN_TERM_LEN and t not in _STOPWORDS and not t.isdigit()}


@lru_cache(maxsize=4096)
def _term_pattern(term: str) -> re.Pattern[str]:
    """어휘가 낱말 앞머리에 오는 경우만 일치로 본다.

    뒤쪽은 열어둔다 — 한국어는 조사·복합어가 뒤에 붙으므로 '성과를', '근로자위원',
    '안전보건관리체계'는 각각 성과·근로자·안전보건의 일치로 봐야 한다.
    앞쪽은 막는다 — 막지 않으면 '의사소통'이 '소통'의 일치로 잡혀, 의사소통 절차서가
    참여·구제 문항(E-8)의 근거로 통과한다(2026-09-20 실측 4.7%).
    대신 '산업안전보건위원회'처럼 어휘가 복합어 가운데 박힌 경우는 놓친다 — 재현율을
    조금 잃는 대신, 무관한 문서가 겹치는 음절 하나로 통과하는 쪽을 막는다.
    """
    return re.compile(r"(?<![0-9A-Za-z가-힣])" + re.escape(term))


def _matched(text: str, terms: Iterable[str]) -> set[str]:
    """근거 본문에 실제로 나타난 어휘."""
    flat = text or ""
    if not flat:
        return set()
    return {t for t in terms if _term_pattern(t).search(flat)}


@dataclass(frozen=True)
class QuestionFitness:
    """한 문항의 적합성 판정 도구 — 어휘와 그중 고유 어휘, 가중치."""
    qid: str
    terms: frozenset[str]
    distinctive: frozenset[str]
    weights: dict[str, float]

    @property
    def usable(self) -> bool:
        """판정 가능 여부. 고유 어휘를 못 뽑은 문항은 선별하지 않는다."""
        return bool(self.distinctive)

    def score(self, text: str) -> float:
        """근거의 적합도(0~1) — 고유 어휘를 df 역가중으로 얼마나 덮었는가."""
        if not self.distinctive:
            return 0.0
        total = sum(self.weights.get(t, 1.0) for t in self.distinctive)
        if total <= 0:
            return 0.0
        hit = _matched(text, self.distinctive)
        return sum(self.weights.get(t, 1.0) for t in hit) / total

    def matched_terms(self, text: str) -> set[str]:
        """근거가 실제로 담은 고유 어휘 — 보류 사유·감사 로그용."""
        return _matched(text, self.distinctive)


def build_fitness_map(questions: Sequence[Any], requirement_of: Any) -> dict[str, QuestionFitness]:
    """양식의 문항들로 qid→QuestionFitness 맵을 만든다.

    requirement_of(question) -> EvidenceRequirement. 증빙요구 문서 유형도 문항 어휘에
    넣기 위해 호출자가 넘긴다(순환 import 회피).

    고유 어휘는 **이 양식 안에서** 정해진다 — 같은 말이 K-ESG 28문항에서는 고유하고
    HMC 48문항에서는 흔할 수 있다. 양식마다 독립 계산한다.
    """
    per_q: dict[str, set[str]] = {}
    for q in questions:
        try:
            req = requirement_of(q)
            ev = tuple(getattr(req, "evidence_types", ()) or ())
        except Exception:  # noqa: BLE001 — 증빙요구 룩업 실패가 초안 경로를 막지 않게
            ev = ()
        per_q[q.qid] = terms_for(getattr(q, "text", ""), ev)

    n = len(per_q) or 1
    df: dict[str, int] = {}
    for terms in per_q.values():
        for t in terms:
            df[t] = df.get(t, 0) + 1

    cutoff = DISTINCTIVE_DF_RATIO * n
    weights = {t: math.log(1.0 + n / d) for t, d in df.items()}

    def is_distinctive(term: str) -> bool:
        d = df.get(term, 1)
        # df==1은 양식 크기와 무관하게 고유 어휘다 — 문항 수가 적은 양식(saq5_env 등)에서
        # 고유 어휘가 0이 되어 판정 자체가 불가능해지지 않게.
        return d == 1 or d < cutoff

    out: dict[str, QuestionFitness] = {}
    for qid, terms in per_q.items():
        distinctive = {t for t in terms if is_distinctive(t)}
        out[qid] = QuestionFitness(qid, frozenset(terms), frozenset(distinctive), weights)
    return out


def select_fit_chunks(
    chunks: Sequence[dict[str, Any]],
    fitness: QuestionFitness | None,
) -> tuple[list[dict[str, Any]], str]:
    """(선별된 근거, 보류 사유). 사유가 비어있지 않으면 초안을 만들지 않는다.

    fitness가 없거나 고유 어휘를 못 뽑은 문항은 선별하지 않고 그대로 돌려준다 —
    판정 근거가 없는데 근거를 버리지 않는다.
    """
    if not chunks:
        return [], "근거 없음"
    if fitness is None or not fitness.usable:
        return list(chunks), ""

    scored = [(c, fitness.score(c.get("text", ""))) for c in chunks]
    best = max(s for _, s in scored)
    if best <= 0.0:
        return [], "문항 고유 어휘와 겹치는 근거 없음"
    kept = [c for c, s in scored if s > 0.0 and s >= best * RELATIVE_FLOOR]
    return kept, ""
