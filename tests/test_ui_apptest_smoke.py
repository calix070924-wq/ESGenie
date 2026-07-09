"""AppTest 기반 UI 스모크 테스트 — "시연 중 3탭 화면이 크래시 없이 렌더됨" 회귀 방지.

목적은 커버리지 수치가 아니라, app.py 스크립트가 세션 상태만으로 3탭(진단/그린워싱/제출)과
전문가 모드 워크스페이스를 예외 없이 렌더하는지, 그리고 데모 방어 가드(분석 예외·다운로드
파일 누락)가 화면을 죽이지 않는지 검증한다.

전제:
- tests/conftest.py 가 ESGENIE_FORCE_MOCK=1 을 설정 → DART·LLM 라이브 호출 없이 mock 으로 동작.
- file_uploader 는 AppTest 로 조작 불가하므로, 업로드 경로/결과는 세션 상태 주입으로 우회한다.
- 파이프라인은 module-scope fixture 로 1회만 실행해 재사용한다(테스트마다 재실행하지 않음).
"""
from __future__ import annotations

import copy
import os
import sys

import pytest

from streamlit.testing.v1 import AppTest

import esgenie.pipeline as pipeline_mod
from esgenie.pipeline import run as run_pipeline

APP_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), "app.py")
# SBERT 로드 + L0~L5 파이프라인 실행이 있어 기본 3초 타임아웃은 부족하다.
RUN_TIMEOUT = 60


@pytest.fixture(autouse=True)
def _unpollute_ui_tabs_module():
    """다른 UI 테스트가 남긴 fake-streamlit 바인딩을 제거한다.

    test_supplychain_tab.py 등은 sys.modules 에 MagicMock streamlit 을 주입한 뒤
    esgenie.ui.tabs 를 재임포트한다. monkeypatch 는 streamlit 은 복원하지만, 그때
    캐시된 esgenie.ui.tabs 모듈 객체는 여전히 top-level `import streamlit as st` 로
    가짜 st 를 붙든 채 남는다 → AppTest 로 실제 app.py 를 돌리면 st.tabs() 가 MagicMock
    을 돌려주어 `missing_tab, present_tab = st.tabs([...])` 언패킹이 깨진다.
    이 모듈을 미리 evict 해 app.py 재실행 시 실제 streamlit 에 다시 바인딩되게 한다.
    """
    sys.modules.pop("esgenie.ui.tabs", None)
    yield


@pytest.fixture(scope="module")
def mock_result():
    """완료된 분석 결과 1개를 module-scope 로 생성해 재사용한다.

    test_pipeline_e2e 와 동일한 mock 파이프라인 경로(005930, mock LLM)를 쓰되,
    export_outputs=True 로 다운로드 산출물(xlsx/audit_json)까지 실제로 만든다.
    demo_greenwash=True 로 초안 vs 최종 비교가 채워지도록 한다.
    """
    return run_pipeline(
        "005930",
        areas=["E"],
        export_outputs=True,
        save_traces=True,
        demo_greenwash=True,
    )


def _fresh_app() -> AppTest:
    return AppTest.from_file(APP_FILE, default_timeout=RUN_TIMEOUT)


def _seed_result(at: AppTest, result) -> AppTest:
    """분석 완료 상태를 세션 상태 주입으로 재현한다(파일 업로드 우회)."""
    at.session_state["result"] = result
    at.session_state["corp_name_manual"] = "삼성전자"
    at.session_state["area_select"] = "E"
    return at


# ---- T1 부팅 ----------------------------------------------------------------

def test_t1_boots_without_result():
    """초기 상태(결과 없음)에서 스크립트가 예외 없이 렌더된다."""
    at = _fresh_app()
    at.run()
    assert not at.exception


# ---- T2 결과 렌더 ------------------------------------------------------------

def test_t2_renders_result_with_downloads_and_scores(mock_result):
    """mock 결과 주입 후 3탭이 예외 없이 렌더되고, 다운로드 버튼·점수 위젯이 존재한다.

    AppTest 는 탭 클릭과 무관하게 모든 탭 본문을 실행하므로 탭 조작은 불필요하다.
    """
    at = _seed_result(_fresh_app(), mock_result)
    at.run()

    assert not at.exception
    # 제출 서류 탭의 다운로드 버튼(.md/.xlsx/.json) — 존재해야 산출물 회귀를 잡는다.
    assert len(at.get("download_button")) > 0
    # 점수 위젯 — ISSB 갭·공급망 응답 등 st.metric 지표가 렌더돼야 한다.
    assert len(at.metric) > 0


# ---- T3 전문가 모드 ----------------------------------------------------------

def test_t3_expert_mode_renders_ssot_and_lab(mock_result):
    """expert_mode=True 주입 시 근거·검증 상세(SSOT)·실험실 워크스페이스가 예외 없이 렌더된다."""
    at = _seed_result(_fresh_app(), mock_result)
    at.session_state["expert_mode"] = True
    at.run()

    assert not at.exception
    # 전문가 모드는 기본 3탭에 "근거·검증 상세"·"실험실" 2탭을 추가한다.
    header_text = " ".join(block.value for block in at.markdown)
    assert "근거·검증 상세" in header_text
    assert "실험실" in header_text


# ---- T4 M1·M3 회귀 (다운로드 가드) ------------------------------------------

@pytest.mark.xfail(reason="다운로드 파일 누락 가드는 feature/ui-download-guards 머지 대기", strict=False)
def test_t4_missing_download_files_warn_not_crash(mock_result):
    """export_paths 키는 있으나 파일이 없는 상태에서도 FileNotFoundError 없이 렌더돼야 한다.

    이 가드(파일 부재 → 경고 처리)는 팀원 작업(feature/ui-download-guards) 결과물이므로,
    머지 전에는 xfail 로 둔다. 머지 후 xpass 로 전환되면 마커를 제거한다.

    module-scope fixture 의 실제 산출물을 삭제하면 뒤따르는 테스트가 깨지므로,
    파일을 지우지 않고 export_paths 를 존재하지 않는 경로로 가리키게 한다(키는 유지).
    """
    result = copy.copy(mock_result)
    result.export_paths = dict(mock_result.export_paths)
    for key in ("xlsx", "audit_json"):
        if key in result.export_paths:
            result.export_paths[key] = os.path.join("outputs", "_missing", f"gone_{key}")

    at = _seed_result(_fresh_app(), result)
    at.run()

    assert not at.exception


# ---- T5 H1 회귀 (분석 예외 가드, 커밋 9bcedaa) ------------------------------

def test_t5_run_error_sets_flag_and_keeps_prior_result(monkeypatch, mock_result):
    """분석 실행부 예외 시 _run_error 가 세팅되고 이전 결과가 유지된다(화면은 살아 있다).

    app.py 는 esgenie.pipeline.run 을 run_pipeline 로 import 하므로, 모듈 속성을 패치하면
    스크립트 재실행 시 바인딩되는 호출이 실패한다.
    """
    def _boom(*args, **kwargs):
        raise RuntimeError("demo boom")

    monkeypatch.setattr(pipeline_mod, "run", _boom)

    at = _seed_result(_fresh_app(), mock_result)
    at.run()
    # 결과가 이미 있으므로 버튼 라벨은 "다시 분석"이다. 이 버튼을 눌러 분석부 예외를 유발한다.
    at.button[0].click().run()

    assert not at.exception
    assert "_run_error" in at.session_state
    assert at.session_state["result"] is not None  # 이전 결과 유지
    assert any("오류가 발생" in (err.value or "") for err in at.error)
