"""번들(zip) 안 실사응답서 PDF 본문을 report_text.txt로 뽑는다(r1~r4와 같은 방식). 사용: pdf_text.py <zip> <out.txt>"""
import sys
import zipfile

import fitz

with zipfile.ZipFile(sys.argv[1]) as z:
    name = next(n for n in z.namelist() if n.split("/")[-1].startswith("실사응답서_rba42_") and n.endswith(".pdf"))
    with fitz.open(stream=z.read(name), filetype="pdf") as doc:
        text = "".join(p.get_text() for p in doc)
open(sys.argv[2], "w", encoding="utf-8").write(text)
print(name, len(text))
