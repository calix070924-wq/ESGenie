"""수치 비교 단위 테스트용 명시적 합성 범위. 생산 코드 기본값으로 쓰지 않는다."""
from esgenie.ssot.boundary import derive_boundary


def confirmed_boundary(code="E-4-1", year=2025):
    hint = {"E-4-1": "총 에너지 사용량", "E-4-2": "총 에너지 대비 전체 재생에너지 비율",
            "E-3-1": "온실가스 총 배출량"}.get(code, "전체 합계")
    return derive_boundary(hint, f"{year}년 연간", doc_context="전사", unit="%" if code == "E-4-2" else "TJ")
