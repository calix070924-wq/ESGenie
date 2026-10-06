"""출력물에 '어느 코드로, 신규 AI 처리인지 캐시 재생인지'를 남긴다.

보고서·시연에 쓴 응답서를 나중에 보면 어느 커밋으로 만들었는지, AI를 실제로 불렀는지
저장된 캐시를 재생한 것인지 알 수 없다. 전략 문서의 "신규 AI 처리와 캐시 재생은
구분한다"를 출력물 자체에서 확인할 수 있게 한다(작업지시서 B §3).

`llm_cache.stats()`는 **프로세스 누적값**이다. 그래서 호출하는 쪽이 실행 시작 시점의
스냅샷을 넘기면 차이로 계산한다. 스냅샷 없이 부르면 누적값을 쓰고 그 사실을
`processing.llm.from_snapshot = False`로 남긴다 — 숫자의 뜻이 달라지는데 조용히
같은 모양으로 내보내지 않는다.

비밀키 값은 어디에도 넣지 않는다. 설정 여부만 '설정됨'/'없음'으로 적는다.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import subprocess
from typing import Any

# Windows에는 tzdata가 없을 수 있어 zoneinfo를 쓰지 않는다.
KST = timezone(timedelta(hours=9))

NEW = "신규 처리"
REPLAY = "캐시 재생"
MIXED = "혼합"

_LLM_KEYS = ("hits", "misses", "live_calls", "successes", "failures")


def _git(args: list[str], cwd: Path) -> str | None:
    """git 명령 한 줄. git이 없거나 저장소가 아니면 None — 예외를 내지 않는다."""
    try:
        done = subprocess.run(["git", *args], cwd=cwd, capture_output=True,
                              text=True, encoding="utf-8", errors="replace", timeout=20)
    except (OSError, subprocess.SubprocessError):
        return None
    if done.returncode != 0:
        return None
    return done.stdout


def code_state(code_path: str | Path | None = None) -> dict[str, Any]:
    """{commit_sha, dirty}. 알 수 없으면 commit_sha='unknown', dirty=None."""
    cwd = Path(code_path) if code_path else Path(__file__).resolve().parent.parent
    sha = _git(["rev-parse", "HEAD"], cwd)
    status = _git(["status", "--porcelain"], cwd)
    return {
        "commit_sha": sha.strip() if sha else "unknown",
        "dirty": None if status is None else bool(status.strip()),
    }


def _diff(end: dict | None, start: dict | None) -> tuple[dict[str, int], bool]:
    """LLM 통계의 구간 값. 스냅샷이 없으면 누적값을 그대로 쓴다."""
    end = end or {}
    if start is None:
        return ({k: int(end.get(k, 0) or 0) for k in _LLM_KEYS}, False)
    return ({k: int(end.get(k, 0) or 0) - int(start.get(k, 0) or 0)
             for k in _LLM_KEYS}, True)


def classify(llm: dict[str, int], ocr: dict[str, int]) -> str:
    """처리 방식 판정 — 작업지시서 §3의 순서를 그대로 따른다.

    1. LLM live_calls == 0 이고 OCR misses == 0 → 캐시 재생
    2. LLM hits == 0 이고 OCR hits == 0        → 신규 처리
    3. 그 밖                                    → 혼합

    순서가 중요하다. 아무것도 부르지 않은 실행(모두 0)은 1번에 걸려 '캐시 재생'이
    된다 — 2번도 만족하지만 지시가 1번을 먼저 보라고 정했다.
    """
    if llm.get("live_calls", 0) == 0 and ocr.get("misses", 0) == 0:
        return REPLAY
    if llm.get("hits", 0) == 0 and ocr.get("hits", 0) == 0:
        return NEW
    return MIXED


def _key_state(value: object) -> str:
    return "설정됨" if value else "없음"


def build_run_info(
    *,
    llm_stats_end: dict | None = None,
    llm_stats_start: dict | None = None,
    ocr_extractions: list | None = None,
    ocr_stats: dict | None = None,
    upstage_replay: bool = False,
    code_path: str | Path | None = None,
    timings: Any | None = None,
    extra: dict | None = None,
) -> dict[str, Any]:
    """출력물에 실을 실행 정보.

    llm_stats_end / llm_stats_start:
        `esgenie.llm_cache.stats()` 결과. start를 주면 차이로 계산한다(누적값이므로).
        end를 주지 않으면 지금 값을 읽는다.
    ocr_extractions:
        `PipelineOutput.ocr_extractions`. 주면 `ocr_cache.summarize`로 hits·misses를 센다.
    ocr_stats:
        이미 센 값이 있으면 `{"hits": …, "misses": …, "mode": …}`로 직접 넘긴다
        (summarize를 두 번 돌리지 않게). ocr_extractions보다 우선한다.
    upstage_replay:
        Upstage 원시 응답을 재생했는가(`live_numeric_rehearsal.py --replay-upstage`).
    timings:
        `PipelineOutput.timings`가 있으면 넘긴다. 없어도 동작한다.
    """
    from .config import SETTINGS

    if llm_stats_end is None:
        from . import llm_cache
        llm_stats_end = llm_cache.stats()
    llm, from_snapshot = _diff(llm_stats_end, llm_stats_start)
    llm_mode = str((llm_stats_end or {}).get("mode") or "")

    if ocr_stats is not None:
        ocr = {"hits": int(ocr_stats.get("hits", 0) or 0),
               "misses": int(ocr_stats.get("misses", 0) or 0)}
        ocr_mode = str(ocr_stats.get("mode") or "")
    elif ocr_extractions is not None:
        from .ssot import ocr_cache
        hits, misses, ocr_mode = ocr_cache.summarize(ocr_extractions)
        ocr = {"hits": int(hits), "misses": int(misses)}
    else:
        ocr, ocr_mode = {"hits": 0, "misses": 0}, ""

    # OCR 모델은 Upstage Document Parse다. ocr_cache.model_name()은 캐시 키용
    # LLM 모델명(= openai_model)이라 그것을 'ocr'에 쓰면 틀린 말이 된다.
    from .ssot.ocr_router import UPSTAGE_DP_MODEL

    info: dict[str, Any] = {
        **code_state(code_path),
        "generated_at": datetime.now(KST).isoformat(timespec="seconds"),
        "models": {
            "llm": SETTINGS.openai_model,
            "llm_provider": "azure_openai" if SETTINGS.azure_openai_endpoint else "openai",
            "ocr": UPSTAGE_DP_MODEL,
            "ocr_provider": "upstage_document_parse",
            # OCR 결과 보정에 쓰는 LLM — 'ocr'와 섞지 않는다.
            "ocr_vlm": SETTINGS.openai_model,
        },
        "keys": {
            "openai": _key_state(SETTINGS.openai_api_key),
            "anthropic": _key_state(SETTINGS.anthropic_api_key),
            "upstage": _key_state(_upstage_key_present()),
        },
        "processing": {
            "label": classify(llm, ocr),
            "llm": {**llm, "mode": llm_mode, "from_snapshot": from_snapshot},
            "ocr": {**ocr, "mode": ocr_mode},
            "upstage_replay": bool(upstage_replay),
        },
    }
    if timings is not None:
        info["timings"] = timings
    if extra:
        info.update(extra)
    return info


def _upstage_key_present() -> bool:
    import os

    return bool(os.getenv("UPSTAGE_API_KEY"))


def short_sha(info: dict | None) -> str:
    sha = str((info or {}).get("commit_sha") or "unknown")
    return sha[:7] if sha != "unknown" else "unknown"


# 바닥글·첫 페이지 한 줄의 고정 접두어.
# PDF 바닥글은 canvas에 직접 그려 추출 텍스트에 섞인다. 응답표를 문항별로 읽는 쪽이
# 이 줄을 표 내용과 가를 수 있어야 하므로 고정 표식으로 시작한다.
STAMP_PREFIX = "실행 정보:"


def footer_line(info: dict | None) -> str:
    """PDF 바닥글 한 줄 — 커밋 앞 7자리 + 처리 방식(+ 미커밋 표시)."""
    if not info:
        return ""
    label = str(info.get("processing", {}).get("label") or "")
    bits = [f"코드 {short_sha(info)}", label]
    if info.get("dirty"):
        bits.append("미커밋 변경 있음")
    return f"{STAMP_PREFIX} " + " · ".join(b for b in bits if b)


def summary_line(info: dict | None) -> str:
    """첫 페이지 하단 한 줄 — 바닥글보다 자세히."""
    if not info:
        return ""
    processing = info.get("processing", {})
    llm = processing.get("llm", {})
    ocr = processing.get("ocr", {})
    models = info.get("models", {})
    bits = [
        f"코드 {short_sha(info)}",
        "미커밋 변경 있음" if info.get("dirty") else
        ("미커밋 변경 없음" if info.get("dirty") is False else "미커밋 여부 확인 못 함"),
        f"생성 {info.get('generated_at', '')}",
        str(processing.get("label") or ""),
        f"LLM 실호출 {llm.get('live_calls', 0)} · 캐시 적중 {llm.get('hits', 0)}",
        f"OCR 미스 {ocr.get('misses', 0)} · 적중 {ocr.get('hits', 0)}",
        f"모델 {models.get('llm', '')} / {models.get('ocr', '')}",
    ]
    if processing.get("upstage_replay"):
        bits.append("Upstage 기록 재생")
    if llm.get("from_snapshot") is False:
        bits.append("LLM 수치는 프로세스 누적값")
    return f"{STAMP_PREFIX} " + " | ".join(b for b in bits if b)


def rows(info: dict | None) -> list[tuple[str, str]]:
    """Excel '실행정보' 시트에 적을 (항목, 값) 목록."""
    if not info:
        return []
    processing = info.get("processing", {})
    llm = processing.get("llm", {})
    ocr = processing.get("ocr", {})
    models = info.get("models", {})
    keys = info.get("keys", {})
    dirty = info.get("dirty")
    out = [
        ("코드 커밋", str(info.get("commit_sha") or "unknown")),
        ("미커밋 변경", "있음" if dirty else ("없음" if dirty is False else "확인 못 함")),
        ("생성 시각(KST)", str(info.get("generated_at") or "")),
        ("처리 방식", str(processing.get("label") or "")),
        ("LLM 실호출", str(llm.get("live_calls", 0))),
        ("LLM 캐시 적중", str(llm.get("hits", 0))),
        ("LLM 캐시 미스", str(llm.get("misses", 0))),
        ("LLM 캐시 모드", str(llm.get("mode") or "")),
        ("LLM 수치 기준", "실행 구간" if llm.get("from_snapshot") else "프로세스 누적"),
        ("OCR 캐시 적중", str(ocr.get("hits", 0))),
        ("OCR 캐시 미스", str(ocr.get("misses", 0))),
        ("OCR 캐시 모드", str(ocr.get("mode") or "")),
        ("Upstage 기록 재생", "예" if processing.get("upstage_replay") else "아니오"),
        ("LLM 모델", f"{models.get('llm', '')} ({models.get('llm_provider', '')})"),
        ("OCR 모델", f"{models.get('ocr', '')} ({models.get('ocr_provider', '')})"),
        ("OCR 보정 LLM", str(models.get("ocr_vlm") or "")),
        ("OpenAI 키", str(keys.get("openai") or "")),
        ("Anthropic 키", str(keys.get("anthropic") or "")),
        ("Upstage 키", str(keys.get("upstage") or "")),
    ]
    if info.get("timings") is not None:
        out.append(("단계 소요", str(info["timings"])))
    return out
