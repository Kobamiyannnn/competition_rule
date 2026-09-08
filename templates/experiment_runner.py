"""実験スクリプトの雛形。

要点は一つだけ。metrics.json を書くのはこのスクリプトであって、人でも LLM でもない。
`expkit.metrics.write` を呼べば、git hash・環境・seed・実行時間が一緒に記録される。

使い方:
    cp templates/experiment_runner.py src/run_exp0001.py
    # TODO を埋めて
    uv run python src/run_exp0001.py --exp exp0001 --n-rows <学習データの行数>

依存を足すときは `uv add lightgbm` のように uv 経由で。
pip install で入れたものは uv.lock に残らず、その実験は再現できなくなる。
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

import numpy as np

# expkit は uv sync で入るので、パスをいじる必要はない。
from expkit import metrics as expmetrics


def build_config(args: argparse.Namespace) -> dict:
    """この実験の設定。ここに入れたものが metrics.json に丸ごと残る。

    ハイパーパラメータ、特徴量の集合、前処理の切り替え、CV の切り方を全部入れる。
    ここに入れ忘れたものは、あとから再現できない。
    """
    return {
        "seed": args.seed,
        "cv": {"scheme": "TODO", "n_splits": 5},
        "features": {"set": "TODO"},
        "model": {"name": "TODO", "params": {}},
        "preprocessing": {},
    }


def run_fold(fold: int, config: dict) -> tuple[float, np.ndarray, np.ndarray]:
    """1 fold を学習し、(スコア, 検証行のインデックス, その予測) を返す。

    予測を返すのは、out-of-fold 予測を保存して誤り分析に使うため。
    保存していないと、決定が判定できなくなったときに
    「どのケースをなぜ外したか」を見る手段が無くなる。
    """
    raise NotImplementedError("TODO: 学習と評価を書く")


def save_oof(exp_id: str, oof: np.ndarray) -> Path:
    """out-of-fold 予測を保存する。誤り分析（/learn-domain）の入力になる。

    artifacts/ は .gitignore で除外されるので、リポジトリは太らない。
    """
    path = Path(__file__).resolve().parents[1] / "experiments" / exp_id / "artifacts"
    path.mkdir(parents=True, exist_ok=True)
    out = path / "oof.npy"
    np.save(out, oof)
    return out


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--exp", required=True, help="実験 ID（例: exp0001）")
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--folds", type=int, default=5)
    p.add_argument("--n-rows", type=int, default=0,
                   help="学習データの行数。OOF を保存するのに要る")
    args = p.parse_args()

    config = build_config(args)
    started = time.monotonic()

    scores: list[float] = []
    oof = np.full(args.n_rows, np.nan) if args.n_rows else None
    for f in range(args.folds):
        score, valid_idx, preds = run_fold(f, config)
        scores.append(score)
        if oof is not None:
            oof[valid_idx] = preds
    if oof is not None:
        print(f"{save_oof(args.exp, oof)} に OOF を保存した。")

    mean = sum(scores) / len(scores)
    var = sum((s - mean) ** 2 for s in scores) / (len(scores) - 1) if len(scores) > 1 else 0.0

    path = expmetrics.write(
        args.exp,
        metrics={
            "cv": {
                "mean": mean,
                "std": var ** 0.5,
                "folds": scores,
            },
            # LB は `uv run expctl lb <exp> --public <score>` で後から差す。
            # ここで書くと手入力であることが provenance に残らない。
        },
        config=config,
        seed=args.seed,
        runtime_seconds=time.monotonic() - started,
    )

    print(f"{path} を書いた。cv.mean={mean:.5f} std={var ** 0.5:.5f}")
    print(f"次: experiments/{args.exp}/record.yaml を埋めて "
          f"`uv run expctl lint {args.exp}` を通す。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
