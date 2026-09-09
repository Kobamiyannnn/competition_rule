from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from expkit import metrics as expmetrics  # noqa: E402

COMPETITION = {
    "name": "テスト",
    "metric": {"name": "AUC", "direction": "increase", "verified": True},
    "lb_noise": 0.001,
    "current_phase": "p1_saturate",
    "phases": [
        {"id": "p1_saturate", "goal": "可動域", "tier_quota": {"exploit": 0.7, "explore": 0.3}},
    ],
    "policy": {"backlog_min_open": 2, "tier_window": 10, "cv_trust_min_pairs": 3},
}

AXES = ["metric_fidelity", "validation", "features", "hyperparameters", "ensemble"]


@pytest.fixture()
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("EXPKIT_ROOT", str(tmp_path))
    (tmp_path / "experiments").mkdir()
    (tmp_path / "decisions").mkdir()
    (tmp_path / "knowledge").mkdir()
    (tmp_path / "ideas").mkdir()
    (tmp_path / "competition.yaml").write_text(
        yaml.safe_dump(COMPETITION, allow_unicode=True), encoding="utf-8"
    )
    (tmp_path / "knowledge" / "coverage.yaml").write_text(
        yaml.safe_dump({"axes": [{"id": a, "status": "untouched"} for a in AXES]},
                       allow_unicode=True),
        encoding="utf-8",
    )
    (tmp_path / "ideas" / "backlog.yaml").write_text(
        yaml.safe_dump({"ideas": valid_ideas(3)}, allow_unicode=True), encoding="utf-8")
    (tmp_path / "knowledge" / "landscape.yaml").write_text(
        yaml.safe_dump(valid_landscape(), allow_unicode=True), encoding="utf-8")
    (tmp_path / "knowledge" / "domain.yaml").write_text(
        yaml.safe_dump(valid_domain(), allow_unicode=True), encoding="utf-8")
    return tmp_path


def valid_domain(n_facts: int = 5, n_confirmed: int = 2) -> dict:
    """検証を通るドメイン台帳。confirmed には再確認できる根拠が要る。"""
    return {
        "facts": [
            {
                "id": f"d{k:04d}",
                "statement": "同一患者の複数枚は同じ日に撮影されており、撮影条件がほぼ同じ。",
                "source": "data_observation" if k <= n_confirmed else "domain_literature",
                "evidence": (f"https://example.invalid/doc/{k}"
                             if k <= n_confirmed else "領域の教科書に記載がある。"),
                "confidence": "confirmed" if k <= n_confirmed else "likely",
                "implication": "患者をまたぐ分割にしないと、同条件の画像が"
                               "train と valid に分かれてリークする。",
                "transferred_to": [],
            }
            for k in range(1, n_facts + 1)
        ]
    }


def valid_landscape(n_sources: int = 5, n_transferred: int = 3) -> dict:
    """検証を通る地固め。種類が偏ると landscape.one_kind_only に落ちる。"""
    kinds = ["solution", "paper", "discussion", "benchmark", "dataset_doc"]
    return {
        "task": {
            "statement": "患者の検査画像から、3か月後の再発の有無を予測する。",
            "formulation": "不均衡二値分類。患者単位のグループ構造あり。",
            "why_hard": "陽性が2%。同一患者から複数枚あり、患者をまたぐ分割をしないとリークする。",
        },
        "sources": [
            {
                "id": f"s{k:04d}",
                "kind": kinds[(k - 1) % len(kinds)],
                "title": f"出典 {k}",
                "url": f"https://example.invalid/source/{k}",
                "year": 2024,
                "accessed_at": "2026-09-07",
                "relevance": "同じ患者単位のグループ構造と、同程度の陽性割合を持つ。",
                "takeaway": "患者単位の StratifiedGroupKFold で切り、"
                            "陽性を含む患者を層化に使う。",
                "transferred_to": [f"i{k:04d}"] if k <= n_transferred else [],
            }
            for k in range(1, n_sources + 1)
        ],
    }


# 検証を通る在庫。軸が散っていないと backlog.too_narrow に落ちるので、
# 既定の軸から順に割り当てる。
_IDEA_AXES = ["features", "hyperparameters", "ensemble", "validation", "metric_fidelity"]


def valid_ideas(n: int) -> list[dict]:
    return [
        {
            "id": f"i{k:04d}",
            "created_at": "2026-09-07T12:00:00+09:00",
            "action": f"ユーザ単位の集約特徴を {k} 種類の統計量で作り、"
                      "既存の特徴集合に足して比較する。",
            "axes": [_IDEA_AXES[(k - 1) % len(_IDEA_AXES)]],
            "tier": "explore",
            "expected": {"metric": "cv.mean", "direction": "increase", "magnitude": 0.003},
            "evidence": "priors/tabular.md#t03",
            "cost": 1.0,
            "status": "open",
        }
        for k in range(1, n + 1)
    ]


def make_experiment(
    repo: Path, exp_id: str, *, cv_mean: float, cv_std: float = 0.004,
    folds: list[float] | None = None, record: dict | None = None,
) -> None:
    """metrics を機械経由で書き、record を置く。"""
    expmetrics.write(
        exp_id,
        metrics={"cv": {"mean": cv_mean, "std": cv_std,
                        "folds": folds or [cv_mean] * 5}},
        config={"model": {"name": "lgb"}}, seed=42, runtime_seconds=1.0, root=repo,
    )
    doc = record if record is not None else {}
    (repo / "experiments" / exp_id / "record.yaml").write_text(
        yaml.safe_dump(doc, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )


def base_record(exp_id: str, **over: object) -> dict:
    doc: dict = {
        "id": exp_id,
        "created_at": "2026-09-07T12:00:00+09:00",
        "created_by": "expctl new",
        "tier": "exploit",
        "based_on": None,
        "axes": ["features"],
        "change": {"summary": "集約特徴を12列追加した。"},
        "observation": "cv.mean は ${metrics.cv.mean}。",
        "hypothesis": [],
        "lint_waived": [],
    }
    doc.update(over)
    return doc
