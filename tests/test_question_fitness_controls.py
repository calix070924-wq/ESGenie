"""문항 적합성 대조군 — 2026-09-20 §6 회귀.

근거 문서
---------
  docs/작업지시서_HMC_응답서_검증정합성_개선_2026-09-20.md §6
  docs/reviews/HMC_출력_지적사항_검토_2026-09-20.md

고친 결함(HMC 응답서 실측)
  · E-7(의사소통) 초안이 노사협의·단결권·핫라인·ISMS 문장을 이어 붙여 만들어졌다.
    누가 누구에게 어떤 경로로 전달하는지는 어느 근거에도 없었다.
  · E-8(참여·구제)이 E-7과 사실상 같은 답을 받았다 — 문항 차이가 무시됐다.
  · 원인: 코드 일치(code_match) 경로에 문항 적합성 게이트가 아예 없었고, BM25 폴백의
    관련성 게이트는 청크 묶음을 한 번에 물어 관련 청크 하나가 전체를 통과시켰다.

고정하는 계약
  1. 근거 선별은 코드 일치·BM25 폴백 두 경로 모두에서 일어난다.
  2. 관련 청크 하나 때문에 무관한 청크 전체가 초안 근거로 들어가지 않는다.
  3. grounding(근거에 있는 말인가)과 문항 적합성(질문에 답하는가)은 별개 게이트다.
  4. 규칙은 양식·문항을 가리지 않는다 — E-7 전용 파일명·노드 ID 화이트리스트나
     무관 단어 블랙리스트가 아니라, 양식 내 문항 빈도(df)로 정한 문항 고유 어휘로
     판정한다.
  5. 근거가 부족하면 부족 상태를 유지한다(fail-closed). 담당자·주기·소통 경로를
     지어내지 않는다.

입력 구분
---------
  손으로 만든 **통제 실험**이다. 긍정 대조군은 실제 의사소통 절차가 적힌 합성 문서,
  부정 대조군은 단결권·정보보호 등 인접 주제만 있는 합성 문서다. 모두 synthetic이며
  OCR 재생이나 실제 LLM 호출이 아니다 — LLMClient는 mock이고, 초안 문구의 생성 품질은
  여기서 검증하지 않는다. 검증 대상은 "어떤 근거가 초안 경로에 들어가는가"다.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from esgenie.schemas import GroundingResult
from esgenie.supplychain import get_framework
from esgenie.supplychain.drafter import generate_drafts
from esgenie.supplychain.question_fitness import (
    build_fitness_map,
    select_fit_chunks,
    terms_for,
)
from esgenie.supplychain.schema import Answer, ResponseSheet

# ── 합성 대조군 문서 (synthetic) ─────────────────────────────────────────────
# 긍정: 방침·관행·기대·성과를 누구에게 어떤 경로로 전달하는지가 적혀 있다.
DOC_COMMUNICATION = (
    "제3조(의사소통) 회사는 안전·환경·인권 방침과 관행, 기대사항 및 성과를 "
    "사내 게시판과 정기 조회를 통해 전 근로자에게 전달한다. 공급사에는 구매포털 "
    "공지로, 고객에는 지속가능경영보고서로 전달한다."
)
# 부정: 인접 주제만 — 의사소통 절차가 아니다.
DOC_UNION = (
    "근로자의 단결권과 결사의 자유를 보장하며, 노동조합 가입을 이유로 "
    "불이익을 주지 않는다."
)
DOC_ISMS = "정보보호 관리체계(ISMS) 인증을 취득하여 개인정보를 안전하게 관리한다."
DOC_HOTLINE = "윤리경영 핫라인을 운영하여 익명 신고를 접수한다."
DOC_LABOR_COUNCIL = (
    "제5조 노사협의회는 분기별로 개최하며, 근로자위원과 사용자위원 각 3명으로 "
    "구성한다."
)
# E-8(참여·구제)에는 답하지만 E-7(의사소통)에는 답하지 않는 문서.
DOC_GRIEVANCE = (
    "근로자대표 및 이해관계자와 반기 1회 양방향 간담회를 열고, 고충처리위원회와 "
    "익명 신고채널을 통해 보복 없이 고충을 접수·구제한다."
)


def _node(node_id: str, text: str, code: str | None, source_file: str = "사내규정.pdf"):
    return SimpleNamespace(id=node_id, text=text, kesg_code=code, rba_code=None,
                           source_file=source_file, page=0, origin="ocr_unstructured")


def _graph(nodes):
    table = {n.id: n for n in nodes}

    class FakeGraph:
        text_nodes = table
        nodes: dict = {}

        def text_nodes_by_code(self, code):
            return [n for n in table.values() if n.kesg_code == code]

    return FakeGraph()


def _hmc_sheet(pending_qids: set[str]) -> ResponseSheet:
    """지정한 문항만 작성필요(초안 대상), 나머지는 증빙검증으로 고정."""
    fw = get_framework("hmc")
    answers = [
        Answer(qid=q.qid, section=q.section, question_text=q.text,
               value=None if q.qid in pending_qids else True,
               status="hitl_required" if q.qid in pending_qids else "verified")
        for q in fw.questions
    ]
    return ResponseSheet("hmc", fw.label, "한울정밀공업", answers)


def _accept() -> GroundingResult:
    return GroundingResult(decision="ACCEPT", g1_uncited_sentences=[],
                           g2_orphan_numbers=[], g4_unit_mismatches=[],
                           g5_overclaim=False, hard_fails=[], soft_flags=[],
                           faithfulness=1.0)


def _llm(content: str = "초안 텍스트 [N_OK]") -> MagicMock:
    llm = MagicMock()
    llm.complete.return_value = SimpleNamespace(content=content)
    return llm


def _drafting_calls(llm: MagicMock) -> list[str]:
    """초안 생성 프롬프트만 골라낸다(관련성 판정 호출과 구분)."""
    return [c.kwargs.get("user", "") for c in llm.complete.call_args_list
            if "아래 발췌만 근거로" in c.kwargs.get("user", "")]


def _answer(sheet: ResponseSheet, qid: str) -> Answer:
    return next(a for a in sheet.answers if a.qid == qid)


class TestPositiveControl:
    """실제 의사소통 절차가 있는 문서 → 초안이 만들어진다."""

    @patch("esgenie.supplychain.drafter.evaluate_grounding")
    @patch("esgenie.supplychain.drafter.LLMClient")
    def test_real_procedure_document_produces_a_draft(self, cls, ev):
        cls.return_value = _llm()
        ev.return_value = _accept()

        sheet = generate_drafts(
            _hmc_sheet({"HMC-E-7"}),
            _graph([_node("N_OK", DOC_COMMUNICATION, "E-7", "이해관계자_의사소통_절차서.pdf")]),
        )
        e7 = _answer(sheet, "HMC-E-7")
        assert e7.status == "draft_ready"
        assert [c["node_id"] for c in e7.draft_citations] == ["N_OK"]


class TestNegativeControl:
    """인접 주제(단결권·정보보호·핫라인·노사협의)만 있으면 초안을 만들지 않는다."""

    @patch("esgenie.supplychain.drafter.evaluate_grounding")
    @patch("esgenie.supplychain.drafter.LLMClient")
    def test_adjacent_topics_only_stays_pending(self, cls, ev):
        llm = _llm()
        cls.return_value = llm
        ev.return_value = _accept()

        sheet = generate_drafts(
            _hmc_sheet({"HMC-E-7"}),
            _graph([
                _node("N_UNION", DOC_UNION, "E-7", "단체협약서.pdf"),
                _node("N_ISMS", DOC_ISMS, "E-7", "정보보호방침.pdf"),
                _node("N_HOTLINE", DOC_HOTLINE, "E-7", "윤리규정.pdf"),
                _node("N_COUNCIL", DOC_LABOR_COUNCIL, "E-7", "노사협의회_운영규정.pdf"),
            ]),
        )
        e7 = _answer(sheet, "HMC-E-7")
        assert e7.status == "hitl_required", "인접 주제만으로는 부족 상태를 유지해야 함"
        assert e7.draft_text == ""
        assert _drafting_calls(llm) == [], "초안 생성 호출 자체가 없어야 함"

    @patch("esgenie.supplychain.drafter.evaluate_grounding")
    @patch("esgenie.supplychain.drafter.LLMClient")
    def test_stitching_adjacent_sentences_does_not_complete_the_answer(self, cls, ev):
        """네 문서를 이어 붙여도 의사소통 절차 답변이 되지 않는다."""
        cls.return_value = _llm()
        ev.return_value = _accept()

        sheet = generate_drafts(
            _hmc_sheet({"HMC-E-7"}),
            _graph([_node("N_MIX",
                          f"{DOC_LABOR_COUNCIL} {DOC_UNION} {DOC_HOTLINE} {DOC_ISMS}",
                          "E-7", "사내규정_모음.pdf")]),
        )
        assert _answer(sheet, "HMC-E-7").status == "hitl_required"

    @patch("esgenie.supplychain.drafter.evaluate_grounding")
    @patch("esgenie.supplychain.drafter.LLMClient")
    def test_the_gate_applies_on_the_code_match_path(self, cls, ev):
        """부정 대조군 노드는 모두 E-7 코드가 붙어 있다 — 코드 일치만으로 통과하지 않는다."""
        cls.return_value = _llm()
        ev.return_value = _accept()

        graph = _graph([_node("N_UNION", DOC_UNION, "E-7")])
        assert [n.id for n in graph.text_nodes_by_code("E-7")] == ["N_UNION"]

        sheet = generate_drafts(_hmc_sheet({"HMC-E-7"}), graph)
        assert _answer(sheet, "HMC-E-7").status == "hitl_required"
        ev.assert_not_called()


class TestOneRelevantChunkDoesNotCarryTheRest:
    @patch("esgenie.supplychain.drafter.evaluate_grounding")
    @patch("esgenie.supplychain.drafter.LLMClient")
    def test_irrelevant_chunks_are_dropped_from_the_citation_set(self, cls, ev):
        llm = _llm()
        cls.return_value = llm
        ev.return_value = _accept()

        sheet = generate_drafts(
            _hmc_sheet({"HMC-E-7"}),
            _graph([
                _node("N_OK", DOC_COMMUNICATION, "E-7", "의사소통_절차서.pdf"),
                _node("N_UNION", DOC_UNION, "E-7", "단체협약서.pdf"),
                _node("N_ISMS", DOC_ISMS, "E-7", "정보보호방침.pdf"),
                _node("N_HOTLINE", DOC_HOTLINE, "E-7", "윤리규정.pdf"),
            ]),
        )
        e7 = _answer(sheet, "HMC-E-7")
        assert e7.status == "draft_ready"
        cited = {c["node_id"] for c in e7.draft_citations}
        assert cited == {"N_OK"}, f"무관 청크가 근거로 따라들어옴: {cited}"
        # 초안 프롬프트에도 무관 청크가 실리지 않는다.
        prompt = _drafting_calls(llm)[0]
        assert "단결권" not in prompt and "ISMS" not in prompt


class TestQuestionsAreNotGivenTheSameAnswer:
    @patch("esgenie.supplychain.drafter.evaluate_grounding")
    @patch("esgenie.supplychain.drafter.LLMClient")
    def test_e7_and_e8_get_different_evidence(self, cls, ev):
        """E-7(의사소통)과 E-8(참여·구제)은 같은 근거로 같은 답을 받지 않는다."""
        cls.return_value = _llm()
        ev.return_value = _accept()

        fw = get_framework("hmc")
        e8_code = next(q.primary_code for q in fw.questions if q.qid == "HMC-E-8")
        sheet = generate_drafts(
            _hmc_sheet({"HMC-E-7", "HMC-E-8"}),
            _graph([
                _node("N_COMM", DOC_COMMUNICATION, "E-7", "의사소통_절차서.pdf"),
                _node("N_GRIEV", DOC_GRIEVANCE, e8_code, "고충처리_운영규정.pdf"),
            ]),
        )
        e7, e8 = _answer(sheet, "HMC-E-7"), _answer(sheet, "HMC-E-8")
        assert {c["node_id"] for c in e7.draft_citations} == {"N_COMM"}
        assert {c["node_id"] for c in e8.draft_citations} == {"N_GRIEV"}

    @patch("esgenie.supplychain.drafter.evaluate_grounding")
    @patch("esgenie.supplychain.drafter.LLMClient")
    def test_communication_document_does_not_answer_the_remedy_question(self, cls, ev):
        """의사소통 절차서만 있으면 E-8은 부족 상태로 남는다(같은 근거 재사용 금지)."""
        cls.return_value = _llm()
        ev.return_value = _accept()

        fw = get_framework("hmc")
        e8_code = next(q.primary_code for q in fw.questions if q.qid == "HMC-E-8")
        sheet = generate_drafts(
            _hmc_sheet({"HMC-E-8"}),
            _graph([_node("N_COMM", DOC_COMMUNICATION, e8_code, "의사소통_절차서.pdf")]),
        )
        assert _answer(sheet, "HMC-E-8").status == "hitl_required"


class TestGroundingAndFitnessAreSeparateGates:
    @patch("esgenie.supplychain.drafter.evaluate_grounding")
    @patch("esgenie.supplychain.drafter.LLMClient")
    def test_fitness_blocks_before_grounding_is_ever_consulted(self, cls, ev):
        """적합성에서 걸리면 grounding은 호출되지 않는다 — 두 판정이 다른 축이다."""
        cls.return_value = _llm()
        ev.return_value = _accept()

        sheet = generate_drafts(
            _hmc_sheet({"HMC-E-7"}),
            _graph([_node("N_UNION", DOC_UNION, "E-7")]),
        )
        assert _answer(sheet, "HMC-E-7").status == "hitl_required"
        ev.assert_not_called()

    @patch("esgenie.supplychain.drafter.evaluate_grounding")
    @patch("esgenie.supplychain.drafter.LLMClient")
    def test_fit_evidence_still_faces_grounding(self, cls, ev):
        """적합성을 통과해도 grounding이 막으면 초안은 폐기된다."""
        cls.return_value = _llm()
        ev.return_value = GroundingResult(
            decision="ESCALATE", g1_uncited_sentences=["근거 없는 주장"],
            g2_orphan_numbers=[], g4_unit_mismatches=[], g5_overclaim=False,
            hard_fails=["G1_uncited_claims"], soft_flags=[], faithfulness=0.4)

        sheet = generate_drafts(
            _hmc_sheet({"HMC-E-7"}),
            _graph([_node("N_OK", DOC_COMMUNICATION, "E-7")]),
        )
        assert _answer(sheet, "HMC-E-7").status == "hitl_required"
        ev.assert_called()


class TestTheRuleIsGenericNotQuestionSpecific:
    """E-7 전용 화이트리스트가 아니라 양식 내 문항 빈도(df)로 정해진다."""

    def _fitness(self, framework_key: str):
        from esgenie.knowledge.kesg_evidence_requirements import requirement_for_question
        fw = get_framework(framework_key)
        return build_fitness_map(
            fw.questions,
            lambda q: requirement_for_question(
                q.kesg_codes, quantitative=q.qtype == "numeric"),
        )

    def test_common_words_are_not_distinctive(self):
        """HMC 48문항 중 8문항에 나오는 '근로자'로는 문항을 가릴 수 없다."""
        f = self._fitness("hmc")["HMC-E-7"]
        assert "근로자" in f.terms
        assert "근로자" not in f.distinctive
        assert {"의사소통", "전달", "관행"} <= f.distinctive

    def test_the_same_word_can_be_distinctive_in_another_form(self):
        """고유성은 양식마다 독립 계산된다 — 코드에 박아둔 단어 목록이 아니다."""
        hmc = self._fitness("hmc")["HMC-B-1"]
        kesg = self._fitness("kesg28")["KESG-S-4-1"]
        assert "안전보건" not in hmc.distinctive, "HMC 10문항이 안전보건을 공유한다"
        assert "안전보건" in kesg.distinctive, "K-ESG에서는 한 문항의 말이다"

    def test_other_frameworks_discriminate_too(self):
        """다른 양식의 문항에서도 같은 규칙으로 무관 근거가 걸린다."""
        f = self._fitness("kesg28")["KESG-E-1-1"]
        on_topic = "당사는 2030년까지 온실가스 감축 목표를 수립한 중장기 환경경영 전략을 갖는다."
        off_topic = "산업안전보건위원회를 분기별로 운영한다."
        assert f.score(on_topic) > 0
        assert f.score(off_topic) == 0

    def test_evidence_types_join_the_question_vocabulary(self):
        """문항 텍스트에 없고 증빙요구에만 있는 말도 판정 어휘가 된다."""
        assert "절차서" in terms_for("의사소통", ("이해관계자 의사소통 절차서",))


class TestOneWordOverlapIsNotEnough:
    """고유 어휘 하나만 겹치는 근거는 후보에서 뺀다.

    실제 원문 재생(시연 증빙 PDF 31개 × 6양식 222문항)에서 어휘 하나만 맞은 1,574쌍은
    전수가 오탐이었다 — '근로시간관리규정'이 '자발적 이직률' 문항에 54.5%로, '책임광물
    실사정책'이 '환경 법규 위반 건수' 문항에 50.0%로 걸렸다. 점수만 보면 높다. 점수는
    문항의 고유 어휘 수로 정규화되므로, 어휘가 둘뿐인 문항은 우연한 한 낱말로 절반을
    먹는다. 그래서 점수 하한이 아니라 '몇 개 겹쳤는가'를 기준으로 둔다.
    """

    def _fitness(self, framework_key: str, qid: str):
        from esgenie.knowledge.kesg_evidence_requirements import requirement_for_question
        fw = get_framework(framework_key)
        return build_fitness_map(
            fw.questions,
            lambda q: requirement_for_question(
                q.kesg_codes, quantitative=q.qtype == "numeric"),
        )[qid]

    def test_a_single_incidental_word_is_held(self):
        f = self._fitness("kesg28", "KESG-S-4-1")
        text = "안전보건 관련 게시물을 부착한다."
        assert f.matched_terms(text) == {"안전보건"}
        kept, hold = select_fit_chunks([{"id": "A", "text": text}], f)
        assert kept == [] and "2개" in hold

    def test_two_distinctive_words_pass(self):
        f = self._fitness("kesg28", "KESG-S-4-1")
        text = "제1조 안전보건 경영방침을 수립하고 안전보건 조직을 구성해 운영한다."
        assert len(f.matched_terms(text)) >= 2
        kept, hold = select_fit_chunks([{"id": "A", "text": text}], f)
        assert hold == "" and [c["id"] for c in kept] == ["A"]

    def test_the_bar_never_exceeds_what_the_question_can_supply(self):
        """고유 어휘가 하나뿐인 문항에 2개를 요구하면 그 문항은 영구 보류가 된다."""
        from esgenie.supplychain.question_fitness import QuestionFitness
        one = QuestionFitness("Q1", frozenset({"가"}), frozenset({"가"}), {"가": 1.0})
        assert one.required_hits == 1
        kept, hold = select_fit_chunks([{"id": "A", "text": "가 항목이 있다"}], one)
        assert hold == "" and [c["id"] for c in kept] == ["A"]

    def test_a_two_term_question_needs_both(self):
        """어휘가 둘뿐인 문항(222개 중 4개, 모두 수치)은 둘을 다 요구한다 —
        '자발적 이직률'에 '자발적'만 있는 문서가 걸리던 자리다."""
        f = self._fitness("kesg61", "KESG-S-2-3")
        assert f.required_hits == 2
        kept, hold = select_fit_chunks(
            [{"id": "A", "text": "자발적 퇴직 신청은 인사팀에 제출한다"}], f)
        assert kept == [] and hold

    def test_the_real_communication_procedure_still_passes(self):
        """실제 의사소통 절차서는 E-7 고유 어휘 15개 중 13개를 담는다 — 과차단 방지."""
        f = self._fitness("hmc", "HMC-E-7")
        real = (
            "이해관계자 의사소통 절차서. ESG 방침·관행·기대·성과를 근로자·공급사·고객에 "
            "명확하고 정확하게 전달하는 프로세스를 규정한다. 월간 안전보건 게시판 "
            "업데이트, 분기별 ESG 뉴스레터 배포, 연 1회 공급사 ESG 설명회 개최."
        )
        score, hits = f.assess(real)
        assert hits >= 8, f"실제 절차서가 {hits}개만 맞음"
        assert score > 0.5
        kept, hold = select_fit_chunks([{"id": "A", "text": real}], f)
        assert hold == "" and len(kept) == 1


class TestTermMatching:
    """어휘 일치 규칙 — 낱말 앞머리만 일치로 본다."""

    def _matched(self, terms: set[str], text: str) -> set[str]:
        from esgenie.supplychain.question_fitness import _matched
        return _matched(text, terms)

    def test_a_term_does_not_match_inside_a_longer_word(self):
        """'소통'은 '의사소통'의 일치가 아니다 — 이걸 허용하면 의사소통 절차서가
        참여·구제 문항(E-8)의 근거로 통과한다."""
        assert self._matched({"소통"}, "의사소통 절차서를 운영한다") == set()
        assert self._matched({"소통"}, "노사 간 소통을 강화한다") == {"소통"}

    def test_particles_and_compound_heads_still_match(self):
        """뒤에 붙는 조사·복합어는 일치다 — 한국어에서 이걸 막으면 재현율이 무너진다."""
        assert self._matched({"성과"}, "성과를 공유한다") == {"성과"}
        assert self._matched({"근로자"}, "근로자위원 3명") == {"근로자"}
        assert self._matched({"안전보건"}, "안전보건관리체계를 구축한다") == {"안전보건"}

    def test_matching_survives_punctuation_boundaries(self):
        assert self._matched({"의사소통"}, "제3조(의사소통) 회사는") == {"의사소통"}


class TestFailOpenOnlyWhenJudgementIsImpossible:
    def test_no_fitness_means_no_filtering(self):
        """판정 도구가 없으면 근거를 버리지 않는다 — 조용히 초안을 없애지 않게."""
        chunks = [{"id": "A", "text": "아무 말"}]
        kept, hold = select_fit_chunks(chunks, None)
        assert kept == chunks and hold == ""

    def test_empty_evidence_is_reported_as_such(self):
        kept, hold = select_fit_chunks([], None)
        assert kept == [] and hold == "근거 없음"
