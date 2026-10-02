"""변형 입력 1건씩 별도 프로젝트로 웹 분석 → 프로젝트·Excel·번들 저장. 사용: variant_drive.py <base_url> <pdf> <out_dir>"""
import json
import sys
import time
from pathlib import Path

import requests

base, pdf, out = sys.argv[1], Path(sys.argv[2]), Path(sys.argv[3])
out.mkdir(parents=True, exist_ok=False)
s = requests.Session()
s.headers["x-esgenie-client"] = "workspace"


def call(method, path, **kw):
    r = s.request(method, base + path, timeout=120, **kw)
    r.raise_for_status()
    return r


pid = call("POST", "/api/projects", json={"company_name": "한울정밀공업(주)", "year": 2026, "industry": "자동차 차체부품",
                                          "framework": "rba42"}).json()["id"]
with open(pdf, "rb") as fh:
    up = call("POST", f"/api/projects/{pid}/documents", files={"file": (pdf.name, fh, "application/pdf")}).json()
call("POST", f"/api/projects/{pid}/analysis")
started = time.monotonic()
while (project := call("GET", f"/api/projects/{pid}").json())["job"]["status"] in {"running", "queued"}:
    time.sleep(5)
(out / "session.json").write_text(json.dumps({"project_id": pid, "input": pdf.name, "roles": [d["role"] for d in up["documents"]],
                                               "status": project["job"]["status"], "seconds": round(time.monotonic() - started)},
                                              ensure_ascii=False, indent=1), encoding="utf-8")
(out / "variant_project.json").write_text(json.dumps(project, ensure_ascii=False, indent=1), encoding="utf-8")
for kind, ext in [("xlsx", "xlsx"), ("bundle", "zip")]:
    (out / f"검토용초안.{ext}").write_bytes(call("GET", f"/api/projects/{pid}/download/{kind}").content)
print(json.dumps({"project_id": pid, "status": project["job"]["status"]}, ensure_ascii=False))
