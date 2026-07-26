#!/usr/bin/env python3
"""임베딩 모델별 한국어 ESG 의미 판별력 비교 — D3 축 진단용.

배경
----
D3(의미 일관성)는 주장 문장과 RAG 청크의 코사인 유사도를 쓴다. 그런데 실제
audit_trace를 보면 현행 MiniLM에서 cos-sim이 0.11~0.24로 임계치(0.35)보다
일관되게 낮게 나온다. 이것이

  (a) 모델이 한국어에서 유사도를 못 만들어내는 문제인지
  (b) 정말로 주장과 증빙이 괴리된 것인지

를 가르려면, **정답이 있는 쌍**에서 유사도가 얼마나 나오는지 봐야 한다.

측정 방식
--------
K-ESG 가이드라인 항목(질의) ↔ 우수보고서 발췌(문서)를 주제로 1:1 대응시킨다.
이 라벨은 사람이 붙인 것이므로 시스템 출력을 정답으로 쓰는 순환논리가 아니다.

  - matched   : 정답 쌍의 cos-sim (높아야 함)
  - unmatched : 오답 쌍의 cos-sim (낮아야 함)
  - separation: matched - unmatched  ← 핵심 지표. 임계치와 무관하게 판별력을 본다.
  - Recall@1  : 질의에 대해 정답 문서가 1위로 오는 비율
  - spread    : 벤치 50문장 전체의 평균 쌍별 유사도 (등방성 진단)
                이 값이 matched와 비슷하면 모델이 의미를 구분하지 못하는 것

한계
----
정답 쌍이 5개뿐이다. 이 스크립트는 "임베딩 교체가 값어치 있는가"를 가르는
싼 1차 진단이며, 성능 주장에 쓸 수치가 아니다. 본격 측정은 한울정밀 증빙
20개에 K-ESG 항목 정답을 붙인 평가셋이 선행돼야 한다.

실행
----
    python scripts/embed_model_eval.py                    # 전체 모델
    python scripts/embed_model_eval.py --models 0 2       # 인덱스로 일부만
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]

# 채택 후보. D3는 실행 시점에 생성 문장을 인코딩하므로 사전 계산이 불가능하고,
# 따라서 모델은 시연 머신(맥, GPU 없음)에서 돌아가야 한다. 실질 상한은 600M급.
MODELS: list[str] = [
    "paraphrase-multilingual-MiniLM-L12-v2",          # 현행 (118M)
    "snunlp/KR-SBERT-V40K-klueNLI-augSTS",            # 한국어 전용 SBERT (110M)
    "intfloat/multilingual-e5-large",                 # 다국어 검색 특화 (560M)
    "BAAI/bge-m3",                                    # 다국어 검색 (568M)
    "nlpai-lab/KURE-v1",                              # bge-m3 한국어 추가학습 (568M)
    "Qwen/Qwen3-Embedding-0.6B",                      # 최신 계열 중 맥에서 돌아가는 유일한 크기
]

# 채택 불가(맥에서 못 돌린다). "우리가 포기한 상한"을 알기 위한 참고 측정 전용.
# --add 로 명시해야 실행된다. transformers>=4.51 + 최신 torch 필요 → venv-eval에서만.
REFERENCE_ONLY: list[str] = [
    "Qwen/Qwen3-Embedding-4B",
    "Qwen/Qwen3-Embedding-8B",
]

# 질의/문서 접두어. 안 붙이면 해당 모델이 부당하게 낮게 측정된다.
_QWEN_Q = "Instruct: Given a web search query, retrieve relevant passages\nQuery: "
PREFIX: dict[str, tuple[str, str]] = {
    "intfloat/multilingual-e5-large": ("query: ", "passage: "),
    "Qwen/Qwen3-Embedding-0.6B": (_QWEN_Q, ""),
    "Qwen/Qwen3-Embedding-4B": (_QWEN_Q, ""),
    "Qwen/Qwen3-Embedding-8B": (_QWEN_Q, ""),
}

# K-ESG 항목 코드 → 발췌 주제. 사람이 지정한 정답 대응.
# G-1-4(이사회 성별 다양성)는 '이사회 구성'과 '다양성' 양쪽에 걸쳐 모호하므로
# 정답 쌍에서 제외하고 방해 문서로만 남긴다.
MATCH: dict[str, str] = {
    "E-3-1": "온실가스 감축",
    "E-4-2": "재생에너지",
    "S-3-1": "다양성",
    "S-4-2": "산업안전",
    "G-1-2": "이사회 구성",
}


def load_data() -> tuple[list[dict], list[dict], list[str]]:
    guidelines = json.loads(
        (ROOT / "data/kesg/guidelines.json").read_text(encoding="utf-8")
    )["guidelines"]
    excerpts = json.loads(
        (ROOT / "data/best_reports/excerpts.json").read_text(encoding="utf-8")
    )["excerpts"]
    bench = json.loads(
        (ROOT / "data/benchmark/greenwash_bench.json").read_text(encoding="utf-8")
    )["cases"]
    sentences = [c["sentence"] for c in bench]
    return guidelines, excerpts, sentences


def cos_matrix(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    a = a / (np.linalg.norm(a, axis=1, keepdims=True) + 1e-12)
    b = b / (np.linalg.norm(b, axis=1, keepdims=True) + 1e-12)
    return a @ b.T


def evaluate(model_name: str, guidelines, excerpts, sentences, fp16: bool = False) -> dict:
    from sentence_transformers import SentenceTransformer

    t0 = time.time()
    kwargs: dict = {"device": "cuda"}
    if fp16:  # 4B/8B는 fp32로 올리면 VRAM을 불필요하게 먹는다
        kwargs["model_kwargs"] = {"torch_dtype": "float16"}
    model = SentenceTransformer(model_name, **kwargs)
    load_s = time.time() - t0

    q_pre, d_pre = PREFIX.get(model_name, ("", ""))

    # 질의: 항목 제목 + 판단기준. 파이프라인이 항목 단위로 검색하는 방식과 맞춘다.
    queries, gold_topics = [], []
    for g in guidelines:
        if g["code"] not in MATCH:
            continue
        queries.append(q_pre + g["title"] + " " + g.get("criteria", ""))
        gold_topics.append(MATCH[g["code"]])

    docs = [d_pre + e["text"] for e in excerpts]
    topics = [e["topic"] for e in excerpts]

    t0 = time.time()
    qv = model.encode(queries, batch_size=32, show_progress_bar=False)
    dv = model.encode(docs, batch_size=32, show_progress_bar=False)
    sv = model.encode(
        [d_pre + s for s in sentences], batch_size=32, show_progress_bar=False
    )
    encode_s = time.time() - t0

    sim = cos_matrix(np.asarray(qv), np.asarray(dv))

    matched, unmatched, hits, rr = [], [], 0, []
    for i, gold in enumerate(gold_topics):
        gi = topics.index(gold)
        matched.append(float(sim[i, gi]))
        unmatched.extend(float(sim[i, j]) for j in range(len(docs)) if j != gi)
        order = np.argsort(-sim[i])
        rank = int(np.where(order == gi)[0][0]) + 1
        hits += int(rank == 1)
        rr.append(1.0 / rank)

    # 등방성 진단: 서로 무관한 벤치 문장들끼리의 평균 유사도.
    ss = cos_matrix(np.asarray(sv), np.asarray(sv))
    iu = np.triu_indices(len(sentences), k=1)
    spread = float(ss[iu].mean())

    m, u = float(np.mean(matched)), float(np.mean(unmatched))
    return {
        "model": model_name,
        "dim": int(np.asarray(qv).shape[1]),
        "matched": m,
        "unmatched": u,
        "separation": m - u,
        "recall@1": hits / len(queries),
        "mrr": float(np.mean(rr)),
        "spread": spread,
        "load_s": load_s,
        "encode_s": encode_s,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", nargs="*", type=int, help="MODELS 인덱스 선택")
    ap.add_argument("--add", nargs="*", default=[], help="임의 모델명 추가 (참고 측정용)")
    ap.add_argument("--fp16", action="store_true", help="半정밀도 로딩 (대형 모델용)")
    ap.add_argument("--out", default="outputs/embed_eval")
    args = ap.parse_args()

    targets = [MODELS[i] for i in args.models] if args.models else list(MODELS)
    targets += list(args.add)
    guidelines, excerpts, sentences = load_data()
    print(f"정답 쌍 {len(MATCH)}개 | 문서 {len(excerpts)}개 | 벤치 문장 {len(sentences)}개\n")

    rows = []
    for name in targets:
        print(f"[{name}] 로딩·인코딩 중...", flush=True)
        try:
            rows.append(evaluate(name, guidelines, excerpts, sentences, fp16=args.fp16))
        except Exception as exc:  # 모델 하나가 실패해도 나머지는 계속
            print(f"  실패: {type(exc).__name__}: {exc}\n")
            continue
        r = rows[-1]
        print(
            f"  matched={r['matched']:.3f} unmatched={r['unmatched']:.3f} "
            f"sep={r['separation']:+.3f} R@1={r['recall@1']:.2f} spread={r['spread']:.3f}\n",
            flush=True,
        )

    if not rows:
        print("측정된 모델이 없습니다.")
        return

    hdr = (
        "| 모델 | dim | matched↑ | unmatched↓ | separation↑ | R@1↑ | MRR↑ | spread |\n"
        "|---|---|---|---|---|---|---|---|"
    )
    lines = [
        "# 임베딩 모델 비교 — 한국어 ESG 의미 판별력 (D3 진단)",
        "",
        f"- 정답 쌍 {len(MATCH)}개(N이 작음 — 1차 진단용, 성능 주장 금지)",
        "- separation = matched - unmatched. 임계치와 무관한 판별력 지표.",
        "- spread = 무관한 벤치 50문장의 평균 쌍별 유사도. matched와 비슷하면 판별력 없음.",
        "",
        hdr,
    ]
    for r in sorted(rows, key=lambda x: -x["separation"]):
        lines.append(
            f"| {r['model']} | {r['dim']} | {r['matched']:.3f} | {r['unmatched']:.3f} | "
            f"{r['separation']:+.3f} | {r['recall@1']:.2f} | {r['mrr']:.3f} | {r['spread']:.3f} |"
        )

    out_dir = ROOT / args.out
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    md, js = out_dir / f"embed_eval_{stamp}.md", out_dir / f"embed_eval_{stamp}.json"
    md.write_text("\n".join(lines) + "\n", encoding="utf-8")
    js.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n".join(lines[6:]))
    print(f"\n저장: {md}\n      {js}")


if __name__ == "__main__":
    main()
