# 번들 폰트 고지

`NotoSansKR-Regular.ttf`, `NotoSansKR-Bold.ttf` 는 Google **Noto Sans CJK KR**
(SIL Open Font License 1.1)을 한글 음절·자모·ASCII·일반기호로 **서브셋**하고,
reportlab 임베드를 위해 CFF 아웃라인을 **TrueType(glyf)** 로 변환한 파생본입니다.

- 원본: Noto Sans CJK (https://github.com/notofonts/noto-cjk)
- 라이선스: SIL OFL 1.1 (https://scripts.sil.org/OFL)
- 용도: 공급망 실사 응답서 PDF(`esgenie/supplychain/exporters/pdf.py`)의 한글 렌더
- 변환: Hangul Syllables(U+AC00–D7A3)·Jamo·ASCII·구두점/기호 서브셋 후 cu2qu glyf 변환

OFL상 파생본 재배포가 허용되며, 'Noto'를 Reserved Font Name으로 단독 사용하지 않습니다.
