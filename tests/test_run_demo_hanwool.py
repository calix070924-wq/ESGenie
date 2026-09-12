"""한울정밀 리허설 러너의 입력 세트·역할 분리 회귀 테스트."""
import pytest

from scripts import run_demo_hanwool
from scripts.run_demo_hanwool import collect_inputs


@pytest.fixture(autouse=True)
def numbered_input_directory(tmp_path, monkeypatch):
    """파일 선택·역할 분리는 저장소에 없는 개인 시연 PDF에 의존하지 않는다."""
    for number in range(1, 32):
        (tmp_path / f"{number:02d}_example.pdf").touch()
    (tmp_path / "test.pdf").touch()
    (tmp_path / "01_reference.md").touch()
    monkeypatch.setattr(run_demo_hanwool, "EVIDENCE_DIR", tmp_path)


def test_collect_full_numbered_set_and_split_supplier_claims() -> None:
    evidence, claims, selected = collect_inputs(core_only=False)
    assert len(selected) == 20
    assert len(evidence) == 17
    assert len(claims) == 3
    assert all(name[:3] in {f"{i:02d}_" for i in range(1, 21)} for name in selected)
    assert "test.pdf" not in selected
    assert not any(name.endswith(".md") for name in selected)
    assert all(name.split("/")[-1].startswith(("05_", "06_", "07_")) for name in claims)


def test_collect_core_readme_set() -> None:
    evidence, claims, selected = collect_inputs(core_only=True)
    assert len(selected) == 7
    assert len(evidence) == 4
    assert len(claims) == 3
    assert [name[:3] for name in selected] == [f"{i:02d}_" for i in range(1, 8)]
