"""L0 비정형 OCR — LLM 응답 캐시.

**OCR을 결정적으로 만드는 모듈이 아니다.** LLM 호출은 여전히 비결정적이다.
이 캐시의 목적은 검증 루프에서 **OCR 변동을 상수로 고정**해, 규칙을 바꿨을 때 값이 변한
원인이 규칙인지 추출 흔들림인지 갈릴 수 있게 하는 것이다
(근거: `docs/OCR_비결정성_조사_2026-07-27.md` — 같은 PDF·temperature=0.0인데 hint 공통 비율 48.1%).

키 = **실제 LLM 입력의 해시**다. 코드 버전 문자열을 열거하지 않는다:

    sha256(모델명 \x00 프롬프트전문 \x00 doc_type \x00 LLM입력텍스트)

버전 문자열을 키로 쓰면 `_reconstruct_rows_from_dict`·`_inherit_label_rows` 같은 전처리를
고쳤을 때 입력이 달라졌는데도 같은 키가 나와 낡은 응답을 돌려준다 — 전처리 개선이
"효과 없음"으로 보이는, 매우 찾기 어려운 함정이다. 입력 자체를 해싱하면 전처리를 고치는
순간 키가 저절로 달라진다(pymupdf 추출은 바이트 결정적임을 실측 확인 — 조사 문서 §3).
청크 단위로 키를 잡으므로 청킹 파라미터(`_UNSTRUCTURED_CHUNK_CHARS`)가 바뀌면 전 청크가
자동 무효화된다.

환경변수:
    ESGENIE_OCR_CACHE=0          캐시 완전 우회(읽기·쓰기 안 함) — 기본은 1(켜짐)
    ESGENIE_OCR_CACHE_REFRESH=1  읽기 건너뛰고 호출 후 덮어쓰기(라이브 재측정)
    ESGENIE_OCR_CACHE_DIR=...    캐시 디렉터리 오버라이드(테스트 격리용)
    ESGENIE_FORCE_MOCK=1         → 캐시를 읽지도 쓰지도 않는다(테스트 결정성은 mock이 담당)

**캐시 경계 — LLM 원본 응답 JSON까지만 담는다(2026-07-27 수정).**
`_map_vlm_json`(G6 각주 마커 배제 포함)·조항 보강·`_backfill_kesg_codes`는 히트에서도 **항상
재실행**된다. 처음엔 `_map_vlm_json` 이후 결과를 담았는데, 그러면 G6를 손봤을 때 캐시 히트인
청크가 옛 필터 결과를 그대로 돌려줘 수정이 무효가 된다 — 키 설계가 막으려던 함정이 방향만
바뀌어(전처리→후처리) 되살아난 꼴이었다. **경계는 "비결정적인 것만 담는다"에 둔다.**

깨진 파일·스키마 불일치는 **조용히 무시하고 재호출**한다(예외 전파 금지, 로그만 남긴다).
캐시 미스일 때의 경로는 캐시 도입 이전과 100% 동일하다.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# 엔트리 스키마 버전 — 구조를 바꾸면 올린다(옛 파일은 스키마 불일치로 조용히 무시된다).
#   1 → 2 (2026-07-27): 본문을 '_map_vlm_json 이후 OcrExtraction'에서 'LLM 원본 응답 JSON'으로
#   교체. 옛 엔트리는 스키마 불일치로 자동 폐기된다(실사용 전이라 재생성 비용 없음).
SCHEMA_VERSION = 2

MODE_DISABLED = "disabled"   # 읽기·쓰기 없음 (캐시 OFF 또는 FORCE_MOCK)
MODE_ON = "on"               # 읽고 없으면 쓴다
MODE_REFRESH = "refresh"     # 읽지 않고 호출 → 덮어쓴다


def _force_mock() -> bool:
    """테스트(FORCE_MOCK)에서는 캐시를 완전히 비활성화한다.

    SETTINGS는 import 시점 스냅샷이라 런타임 변경(monkeypatch)도 함께 본다.
    """
    from ..config import SETTINGS
    if SETTINGS.force_mock:
        return True
    return os.getenv("ESGENIE_FORCE_MOCK", "0") == "1"


def cache_mode() -> str:
    """현재 캐시 모드 — MODE_DISABLED / MODE_REFRESH / MODE_ON."""
    if _force_mock():
        return MODE_DISABLED
    if os.getenv("ESGENIE_OCR_CACHE", "1") in ("0", "false", "False"):
        return MODE_DISABLED
    if os.getenv("ESGENIE_OCR_CACHE_REFRESH", "0") == "1":
        return MODE_REFRESH
    return MODE_ON


def cache_dir() -> Path:
    """캐시 디렉터리. 기본 `data/_cache/ocr/` (corp_codes.json과 같은 부모)."""
    override = os.getenv("ESGENIE_OCR_CACHE_DIR")
    if override:
        return Path(override)
    from ..config import DATA_DIR
    return DATA_DIR / "_cache" / "ocr"


def model_name() -> str:
    """캐시 키에 들어가는 모델명 — 모델을 바꾸면 캐시가 갈려야 한다."""
    from ..config import SETTINGS
    return SETTINGS.openai_model


def make_key(*, model: str, prompt: str, doc_type: str, llm_input: str) -> str:
    """캐시 키 — 실제 LLM 입력의 sha256.

    Parameters
    ----------
    model     : 모델명 (예: "gpt-4.1-mini")
    prompt    : 프롬프트 **전문**(system + 템플릿). 버전 문자열이 아니다.
    doc_type  : 라우터가 판정한 문서 유형
    llm_input : LLM에 실제로 들어간 최종 입력 텍스트(청크 포함)

    파일 경로·타임스탬프·회사명은 넣지 않는다 — 같은 내용의 파일은 같은 키여야 한다.
    """
    h = hashlib.sha256()
    for part in (model, prompt, doc_type, llm_input):
        h.update(str(part).encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()


def _path_for(key: str) -> Path:
    return cache_dir() / f"{key}.json"


def load_response(key: str) -> dict[str, Any] | None:
    """캐시 히트면 **LLM 원본 응답 JSON**(dict), 미스/손상이면 None.

    깨진 JSON·스키마 불일치·본문 타입 불일치는 전부 None으로 떨어뜨린다(재호출).
    돌려주는 건 파싱 전 원본이므로, 호출부는 히트든 미스든 `_map_vlm_json`을 똑같이 태운다.
    """
    path = _path_for(key)
    try:
        entry = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("OCR 캐시 파일 손상 — 무시하고 재호출 [%s]: %s", path.name, exc)
        return None

    if not isinstance(entry, dict) or entry.get("schema") != SCHEMA_VERSION:
        logger.warning("OCR 캐시 스키마 불일치 — 무시하고 재호출 [%s]", path.name)
        return None
    payload = entry.get("response")
    if not isinstance(payload, dict):
        logger.warning("OCR 캐시 본문 없음 — 무시하고 재호출 [%s]", path.name)
        return None
    return payload


def store_response(
    key: str,
    response: dict[str, Any],
    *,
    model: str,
    prompt: str,
    doc_type: str,
    source_file: str,
    llm_input: str,
) -> None:
    """LLM 원본 응답 JSON을 `{key}.json`으로 저장한다.

    쓰기 실패는 무시한다(캐시는 보조 장치 — 실패해도 파이프라인은 정상 진행).
    직렬화 불가한 응답도 무시한다(캐시 못 해도 이번 실행 결과는 그대로 쓰인다).
    """
    entry = {
        "schema": SCHEMA_VERSION,
        "meta": {
            "key": key,
            # 언제 채운 캐시인지 — 오래된 캐시를 지울 판단 근거.
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "model": model,
            "doc_type": doc_type,
            # source_file은 **키에 들어가지 않는다**(같은 내용 = 같은 키). 감사용 기록일 뿐이다.
            "source_file": source_file,
            "prompt_sha256": hashlib.sha256(str(prompt).encode("utf-8")).hexdigest(),
            "input_chars": len(llm_input),
        },
        "response": response,
    }
    path = _path_for(key)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(entry, ensure_ascii=False, indent=1), encoding="utf-8")
    except (OSError, TypeError, ValueError) as exc:
        logger.warning("OCR 캐시 저장 실패 — 무시 [%s]: %s", path.name, exc)


def summarize(extractions: list[Any]) -> tuple[int, int, str]:
    """추출물 목록 → (hit 합계, miss 합계, 대표 모드). 로그·스크립트 헤더용."""
    hits = misses = 0
    modes: list[str] = []
    for ext in extractions or []:
        meta = getattr(ext, "router_meta", {}) or {}
        hits += int(meta.get("ocr_cache_hits", 0) or 0)
        misses += int(meta.get("ocr_cache_misses", 0) or 0)
        if meta.get("ocr_cache"):
            modes.append(str(meta["ocr_cache"]))
    mode = modes[0] if len(set(modes)) == 1 and modes else (",".join(sorted(set(modes))) or cache_mode())
    return hits, misses, mode
