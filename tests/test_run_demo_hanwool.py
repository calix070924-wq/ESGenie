"""한울정밀 리허설 러너의 입력 세트·역할 분리 회귀 테스트."""
from scripts.run_demo_hanwool import collect_inputs


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
