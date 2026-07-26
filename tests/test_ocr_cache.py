"""OCR LLM 응답 캐시 회귀 — 전부 mock/로컬. 라이브 호출 없음.

설계 근거: `docs/OCR_비결정성_조사_2026-07-27.md`
결과 문서: `docs/OCR캐시_결과_2026-07-27.md`

이 파일이 고정하는 것:
  · 키가 **실제 LLM 입력**의 함수라는 것(코드 버전 문자열이 아니다) — 전처리를 고치면
    키가 자동 무효화된다. 이게 작업 1의 설계 의도다.
  · 히트 시 LLM을 **다시 부르지 않는다**는 것(호출 카운터로 단언).
  · 깨진 캐시가 예외를 올리지 않고 재호출로 떨어진다는 것.
  · FORCE_MOCK(테스트)에서는 캐시 파일이 **생기지 않는다**는 것.
"""
from __future__ import annotations

import json

import pytest

from esgenie.ssot import ocr_cache
from esgenie.ssot.ocr_router import (
    DocChannel,
    ExtractedClause,
    ExtractedMetric,
    ExtractedTable,
    OcrExtraction,
    TableCell,
    _extract_unstructured_text,
)


# ---- 픽스처 -----------------------------------------------------------------

@pytest.fixture
def cache_on(tmp_path, monkeypatch):
    """캐시를 켜고 tmp 디렉터리로 격리한다.

    conftest가 ESGENIE_FORCE_MOCK=1을 걸어두므로 캐시는 기본 비활성이다
    (그게 정상 동작이다 — 테스트에서 실제 캐시를 오염시키지 않기 위한 설계).
    캐시 자체를 검증하려면 이 테스트에서만 그 스위치를 내려야 한다.
    """
    from esgenie.config import SETTINGS
    monkeypatch.setattr(SETTINGS, "force_mock", False)
    monkeypatch.setenv("ESGENIE_FORCE_MOCK", "0")
    monkeypatch.setenv("ESGENIE_OCR_CACHE", "1")
    monkeypatch.delenv("ESGENIE_OCR_CACHE_REFRESH", raising=False)
    monkeypatch.setenv("ESGENIE_OCR_CACHE_DIR", str(tmp_path / "ocr"))
    return tmp_path / "ocr"


class _CountingClient:
    """LLMClient 대역 — 호출 횟수를 세고 고정 JSON을 돌려준다."""

    calls: list[str] = []

    def __init__(self) -> None:
        pass

    def complete(self, *, system, user, json_mode=False, temperature=0.0, mock_hint=""):
        type(self).calls.append(user)
        from esgenie.llm import LLMResponse
        payload = {
            "metrics": [{"metric_hint": "전력 사용량 합계", "value": 7497.0,
                         "unit": "TJ", "period": "2024", "kesg_code": "E-4-1"}],
            "clauses": [{"section": "환경경영 방침", "text": "환경법규를 준수한다.",
                         "kesg_code": "E-1-1", "page": 3}],
        }
        return LLMResponse(content=json.dumps(payload, ensure_ascii=False),
                           used_mock=True, meta={})


@pytest.fixture
def counting_client(monkeypatch):
    _CountingClient.calls = []
    monkeypatch.setattr("esgenie.llm.LLMClient", _CountingClient)
    return _CountingClient


_KEY_ARGS = dict(
    model="gpt-4.1-mini",
    prompt="[문서 유형] {doc_type}\n이 페이지에서 JSON으로 추출하라",
    doc_type="policy_manual",
    llm_input="전력 사용량 | TJ | 7,497",
)


# ---- 1. 키 안정성 ------------------------------------------------------------

def test_key_is_stable_for_identical_input():
    """같은 입력 → 같은 키. 파일 경로·시각·회사명은 키에 들어가지 않으므로 흔들릴 게 없다."""
    keys = {ocr_cache.make_key(**_KEY_ARGS) for _ in range(5)}
    assert len(keys) == 1
    key = keys.pop()
    assert len(key) == 64 and all(c in "0123456789abcdef" for c in key)


# ---- 2. 키 민감도 ------------------------------------------------------------

def test_key_changes_when_any_field_changes():
    """모델·프롬프트·doc_type·입력 텍스트 중 **하나만** 바뀌어도 키가 달라진다.

    입력 텍스트는 1글자만 고쳐도 갈려야 한다 — 전처리 개선이 캐시에 가려지지 않게 하는
    핵심 성질이다(작업 1).
    """
    base = ocr_cache.make_key(**_KEY_ARGS)

    variants = {
        "model": {**_KEY_ARGS, "model": "gpt-4.1"},
        "prompt": {**_KEY_ARGS, "prompt": _KEY_ARGS["prompt"] + " (지침 1줄 추가)"},
        "doc_type": {**_KEY_ARGS, "doc_type": "safety_minutes"},
        # 1글자 변경 — 7,497 → 7,498
        "llm_input_1char": {**_KEY_ARGS, "llm_input": "전력 사용량 | TJ | 7,498"},
    }
    keys = {name: ocr_cache.make_key(**kw) for name, kw in variants.items()}
    for name, k in keys.items():
        assert k != base, f"{name} 변경이 키를 바꾸지 않았다"
    # 네 변형끼리도 서로 달라야 한다(필드 구분자 \x00이 필드 경계를 지키는지 확인)
    assert len(set(keys.values())) == 4


def test_key_does_not_leak_field_boundaries():
    """필드 경계가 뭉개지면 다른 조합이 같은 키가 된다 — 구분자(\\x00) 회귀 방지."""
    a = ocr_cache.make_key(model="ab", prompt="c", doc_type="d", llm_input="e")
    b = ocr_cache.make_key(model="a", prompt="bc", doc_type="d", llm_input="e")
    assert a != b


# ---- 3. 직렬화 왕복 ----------------------------------------------------------

def test_extraction_round_trip_preserves_nested_structures():
    """to_dict → from_dict가 metrics·clauses·tables(셀·bbox·page)를 그대로 복원한다."""
    ext = OcrExtraction(
        source_file="mobis.pdf",
        channel=DocChannel.UNSTRUCTURED,
        doc_type="policy_manual",
        metrics=[ExtractedMetric(
            metric_hint="용수 사용량(취수량) 합계", value=1992921.0, unit="ton",
            period="2024", kesg_code_guess="E-5-1",
            bbox=[0.1, 0.2, 0.3, 0.44], page=17, confidence=0.75,
        )],
        clauses=[ExtractedClause(
            section="안전보건 체계", text="위험성 평가를 연 1회 실시한다.",
            kesg_code_guess="S-4-1", page=9, rba_code_guess="A1",
        )],
        tables=[ExtractedTable(
            table_id="upstage_table_0", row_count=2, column_count=2,
            cells=[
                TableCell(row_index=0, column_index=0, content="구분", kind="header",
                          bbox=[0.0, 0.0, 0.1, 0.05], page=17, confidence=0.9),
                TableCell(row_index=1, column_index=1, content="1,992,921",
                          row_span=1, column_span=2),
            ],
            source="upstage_dp", page=17, meta={"gate": "tier0"},
        )],
        raw_text="용수 사용량(취수량) | ton | 1,992,921",
        router_meta={"engine": "gpt-4.1-mini-text", "chunks": 3},
    )

    back = OcrExtraction.from_dict(json.loads(json.dumps(ext.to_dict(), ensure_ascii=False)))

    assert back.to_dict() == ext.to_dict()
    assert back.channel is DocChannel.UNSTRUCTURED
    assert back.metrics[0].bbox == [0.1, 0.2, 0.3, 0.44]
    assert back.metrics[0].page == 17
    assert back.clauses[0].rba_code_guess == "A1"
    assert back.tables[0].cells[0].kind == "header"
    assert back.tables[0].cells[1].column_span == 2
    assert back.tables[0].meta == {"gate": "tier0"}


def test_from_dict_ignores_unknown_keys():
    """스키마가 늘어난 캐시 파일도 예외 없이 읽는다(모르는 키는 버린다)."""
    d = OcrExtraction(source_file="a.pdf", channel=DocChannel.UNSTRUCTURED,
                      doc_type="policy_manual").to_dict()
    d["future_field"] = 1
    d["metrics"] = [{"metric_hint": "x", "value": 1.0, "unit": "TJ",
                     "period": "2024", "brand_new": "?"}]
    back = OcrExtraction.from_dict(d)
    assert back.metrics[0].metric_hint == "x"
    assert not hasattr(back.metrics[0], "brand_new")


# ---- 4. 히트/미스 ------------------------------------------------------------

def test_second_run_hits_cache_and_does_not_call_llm(cache_on, counting_client):
    """1회차 miss(호출 1) → 2회차 hit(호출 추가 0). 결과도 동일해야 한다."""
    raw = "전력 사용량 | TJ | 7,497"

    first = _extract_unstructured_text("mobis.pdf", doc_type="policy_manual", raw_text=raw)
    assert len(counting_client.calls) == 1
    assert first.router_meta["ocr_cache"] == "miss"
    assert (first.router_meta["ocr_cache_hits"], first.router_meta["ocr_cache_misses"]) == (0, 1)
    assert len(list(cache_on.glob("*.json"))) == 1

    second = _extract_unstructured_text("mobis.pdf", doc_type="policy_manual", raw_text=raw)
    assert len(counting_client.calls) == 1, "히트인데 LLM을 다시 불렀다"
    assert second.router_meta["ocr_cache"] == "hit"
    assert (second.router_meta["ocr_cache_hits"], second.router_meta["ocr_cache_misses"]) == (1, 0)

    # 캐시 리플레이가 같은 추출을 재현하는가 — 이 트랙의 목적 그 자체다.
    assert [(m.metric_hint, m.value, m.unit, m.period) for m in second.metrics] == \
           [(m.metric_hint, m.value, m.unit, m.period) for m in first.metrics]
    assert [(c.section, c.text) for c in second.clauses] == \
           [(c.section, c.text) for c in first.clauses]

    # 메타 필드 — 캐시를 언제·무엇으로 채웠는지 남아 있어야 한다(삭제 판단 근거).
    entry = json.loads(next(cache_on.glob("*.json")).read_text(encoding="utf-8"))
    meta = entry["meta"]
    assert set(meta) >= {"created_at", "model", "doc_type", "source_file",
                         "prompt_sha256", "input_chars"}
    assert meta["doc_type"] == "policy_manual"
    assert meta["source_file"] == "mobis.pdf"


# ---- 5. REFRESH -------------------------------------------------------------

def test_refresh_skips_read_and_overwrites(cache_on, counting_client, monkeypatch):
    """REFRESH=1이면 캐시가 있어도 읽지 않고 재호출한 뒤 덮어쓴다."""
    raw = "전력 사용량 | TJ | 7,497"
    _extract_unstructured_text("mobis.pdf", doc_type="policy_manual", raw_text=raw)
    assert len(counting_client.calls) == 1
    before = next(cache_on.glob("*.json")).read_text(encoding="utf-8")

    monkeypatch.setenv("ESGENIE_OCR_CACHE_REFRESH", "1")
    ext = _extract_unstructured_text("mobis.pdf", doc_type="policy_manual", raw_text=raw)

    assert len(counting_client.calls) == 2, "REFRESH인데 캐시를 읽어버렸다"
    assert ext.router_meta["ocr_cache"] == "refresh"
    assert len(list(cache_on.glob("*.json"))) == 1, "덮어쓰기가 아니라 새 파일을 만들었다"
    after = next(cache_on.glob("*.json")).read_text(encoding="utf-8")
    assert json.loads(after)["meta"]["key"] == json.loads(before)["meta"]["key"]


# ---- 6. 손상 캐시 ------------------------------------------------------------

def test_corrupt_cache_is_ignored_without_raising(cache_on, counting_client):
    """깨진 JSON·스키마 불일치는 조용히 미스로 떨어진다(예외 전파 금지)."""
    raw = "전력 사용량 | TJ | 7,497"
    _extract_unstructured_text("mobis.pdf", doc_type="policy_manual", raw_text=raw)
    path = next(cache_on.glob("*.json"))

    # (a) JSON 자체가 깨진 경우
    path.write_text("{ 이건 JSON이 아니다", encoding="utf-8")
    ext = _extract_unstructured_text("mobis.pdf", doc_type="policy_manual", raw_text=raw)
    assert len(counting_client.calls) == 2
    assert ext.router_meta["ocr_cache"] == "miss"
    assert ext.metrics, "재호출 결과가 비었다 — 폴백 경로가 깨졌다"

    # (b) 스키마 버전이 다른 경우
    entry = json.loads(path.read_text(encoding="utf-8"))
    entry["schema"] = ocr_cache.SCHEMA_VERSION + 99
    path.write_text(json.dumps(entry, ensure_ascii=False), encoding="utf-8")
    ext = _extract_unstructured_text("mobis.pdf", doc_type="policy_manual", raw_text=raw)
    assert len(counting_client.calls) == 3
    assert ext.router_meta["ocr_cache"] == "miss"

    # (c) 본문 없는 경우 — load()가 None을 돌려주기만 하면 된다
    path.write_text(json.dumps({"schema": ocr_cache.SCHEMA_VERSION}), encoding="utf-8")
    assert ocr_cache.load(path.stem) is None


# ---- 7. FORCE_MOCK 격리 ------------------------------------------------------

def test_force_mock_neither_reads_nor_writes_cache(tmp_path, monkeypatch, counting_client):
    """테스트(FORCE_MOCK=1)에서는 캐시 파일이 생기지 않는다 — 격리 회귀."""
    cdir = tmp_path / "ocr"
    monkeypatch.setenv("ESGENIE_OCR_CACHE_DIR", str(cdir))
    monkeypatch.setenv("ESGENIE_FORCE_MOCK", "1")   # conftest 기본값과 동일
    assert ocr_cache.cache_mode() == ocr_cache.MODE_DISABLED

    raw = "전력 사용량 | TJ | 7,497"
    for _ in range(2):
        ext = _extract_unstructured_text("mobis.pdf", doc_type="policy_manual", raw_text=raw)
        assert ext.router_meta["ocr_cache"] == "disabled"

    assert not cdir.exists() or not list(cdir.glob("*.json"))
    assert len(counting_client.calls) == 2, "캐시가 꺼졌는데 호출이 줄었다"


# ---- 8. 전처리 변경 → 자동 무효화 --------------------------------------------

def test_preprocessing_change_invalidates_cache(cache_on, counting_client):
    """전처리가 LLM 입력 텍스트를 바꾸면 캐시가 자동 무효화된다.

    **작업 1의 설계 의도를 고정하는 테스트다.** 키를 코드 버전 문자열로 잡았다면
    `_reconstruct_rows_from_dict`·`_inherit_label_rows`·`_attach_column_headers`를
    고쳐도 같은 키가 나와 낡은 응답이 돌아오고, 전처리 개선이 '효과 없음'으로 보인다.
    """
    before = "Scope 1 배출량 | tCO2eq | 396,152"
    # 전처리 개선을 흉내낸다 — 컬럼 헤더가 붙어 라벨이 달라진 같은 표.
    after = "Scope 1 배출량 합계 2024년 | tCO2eq | 396,152"

    _extract_unstructured_text("mobis.pdf", doc_type="policy_manual", raw_text=before)
    assert len(counting_client.calls) == 1

    # 같은 전처리 → 히트(호출 없음)
    _extract_unstructured_text("mobis.pdf", doc_type="policy_manual", raw_text=before)
    assert len(counting_client.calls) == 1

    # 전처리가 바뀌면 → 미스(재호출) + 새 엔트리
    ext = _extract_unstructured_text("mobis.pdf", doc_type="policy_manual", raw_text=after)
    assert len(counting_client.calls) == 2, "입력이 바뀌었는데 캐시가 낡은 응답을 돌려줬다"
    assert ext.router_meta["ocr_cache"] == "miss"
    assert len(list(cache_on.glob("*.json"))) == 2

    # 청킹 파라미터가 바뀌어도 같은 이유로 무효화된다(청크 단위 키).
    k1 = ocr_cache.make_key(**{**_KEY_ARGS, "llm_input": "A\nB"})
    k2 = ocr_cache.make_key(**{**_KEY_ARGS, "llm_input": "A\nB\nC"})
    assert k1 != k2
