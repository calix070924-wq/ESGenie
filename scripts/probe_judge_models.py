# -*- coding: utf-8 -*-
"""probe_judge_models.py — Azure AI Foundry에 어떤 모델이 배포돼 judge로 쓸 수 있는지 탐색.

추출은 gpt-4.1-mini. 그와 '다른' 모델이 응답하면 독립(cross-model) judge로 쓸 수 있다.
같은 Azure 키/엔드포인트 재사용 — 새 키·비용 없음.

사용:  python scripts/probe_judge_models.py
포털(Azure AI Foundry > Deployments)에 보이는 배포 이름이 있으면 CANDIDATES에 추가하세요.
"""
from __future__ import annotations
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from esgenie.config import SETTINGS

# 시도할 후보 (배포명은 프로젝트마다 다를 수 있음 — 포털에서 본 이름 추가)
CANDIDATES = [
    # 다른 벤더(진짜 독립) — Foundry 모델 카탈로그에 배포돼 있으면 최상
    "Claude-3-7-Sonnet", "Claude-3-5-Sonnet", "claude-3-5-sonnet",
    "Llama-3.3-70B-Instruct", "Mistral-Large-2411", "DeepSeek-V3",
    # 같은 벤더 다른 모델(cross-model, self-judge는 해소)
    "gpt-4o", "gpt-4o-mini", "gpt-4.1", "o3-mini", "o1-mini",
]


def main() -> None:
    ep = (SETTINGS.azure_openai_endpoint or "").rstrip("/")
    if not ep or not SETTINGS.openai_api_key:
        print("Azure 엔드포인트/키 없음 — .env 확인")
        sys.exit(1)
    from openai import OpenAI
    base = f"{ep}/models/" if "services.ai.azure.com" in ep else ep
    client = OpenAI(api_key=SETTINGS.openai_api_key, base_url=base)

    print(f"엔드포인트: …{ep[-30:]}")
    print(f"추출 모델(제외 대상): {SETTINGS.openai_model}\n")
    print("후보 모델 응답 여부:")
    working = []
    for name in CANDIDATES:
        try:
            r = client.chat.completions.create(
                model=name,
                messages=[{"role": "user", "content": "reply with exactly: OK"}],
                max_tokens=5, temperature=0,
            )
            txt = (r.choices[0].message.content or "").strip()
            print(f"  ✅ {name:28} → 응답: {txt!r}")
            working.append(name)
        except Exception as e:
            msg = str(e)
            short = msg.split("\n")[0][:90]
            print(f"  ❌ {name:28} → {short}")
    print("\n" + "=" * 60)
    indep = [w for w in working if w.lower() != SETTINGS.openai_model.lower()]
    if indep:
        print(f"judge로 쓸 수 있는 다른 모델: {indep}")
        print(f"→ 추천: {indep[0]}  (JUDGE_MODEL 로 지정)")
    else:
        print("gpt-4.1-mini 외 사용 가능한 모델 없음 — 포털에서 배포명 확인 후 CANDIDATES에 추가")


if __name__ == "__main__":
    main()
