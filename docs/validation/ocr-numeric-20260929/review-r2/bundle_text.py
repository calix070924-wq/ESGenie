"""번들 zip 안의 응답서 PDF(실사응답서_*.pdf) 본문 텍스트를 추출한다. 사용: bundle_text.py <zip> <out.txt>"""
import sys
import zipfile

import fitz

with zipfile.ZipFile(sys.argv[1]) as zf:
    name = next(n for n in zf.namelist() if n.split("/")[-1].startswith("실사응답서_") and n.endswith(".pdf"))
    with fitz.open(stream=zf.read(name), filetype="pdf") as doc:
        text = "".join(page.get_text() for page in doc)
open(sys.argv[2], "w", encoding="utf-8").write(text)
print(name, len(text))
