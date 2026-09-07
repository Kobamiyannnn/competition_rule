"""実験スクリプトの雛形。

要点は一つだけ。metrics.json を書くのはこのスクリプトであって、人でも LLM でもない。
`expkit.metrics.write` を呼べば、git hash・環境・seed・実行時間が一緒に記録される。

使い方:
    cp templates/experiment_runner.py src/run_exp0001.py
    # TODO を埋めて
    python3 src/run_exp0001.py --exp exp0001
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from expkit import metrics as expmetrics  # noqa: E402


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


def run_fold(fold: int, config: dict) -> float:
    """1 fold を学習して、その fold のスコアを返す。"""
    raise NotImplementedError("TODO: 学習と評価を書く")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--exp", required=True, help="実験 ID（例: exp0001）")
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--folds", type=int, default=5)
    args = p.parse_args()

    config = build_config(args)
    started = time.monotonic()

    scores = [run_fold(f, config) for f in range(args.folds)]

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
            # LB は `tools/expctl lb <exp> --public <score>` で後から差す。
            # ここで書くと手入力であることが provenance に残らない。
        },
        config=config,
        seed=args.seed,
        runtime_seconds=time.monotonic() - started,
    )

    print(f"{path} を書いた。cv.mean={mean:.5f} std={var ** 0.5:.5f}")
    print(f"次: experiments/{args.exp}/record.yaml を埋めて "
          f"`tools/expctl lint {args.exp}` を通す。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
