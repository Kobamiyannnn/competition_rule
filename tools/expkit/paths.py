"""リポジトリ内のパス解決。"""

from __future__ import annotations

import os
from pathlib import Path

_MARKERS = ("competition.yaml", ".git")


class RepoNotFound(RuntimeError):
    pass


def repo_root(start: Path | None = None) -> Path:
    """competition.yaml か .git を持つ最も近い祖先を返す。

    EXPKIT_ROOT が設定されていればそれを優先する（テストと hook 用）。
    """
    env = os.environ.get("EXPKIT_ROOT")
    if env:
        return Path(env).resolve()

    cur = (start or Path.cwd()).resolve()
    for candidate in (cur, *cur.parents):
        if any((candidate / m).exists() for m in _MARKERS):
            return candidate
    raise RepoNotFound(
        "competition.yaml も .git も見つからない。"
        "コンペのリポジトリ内で実行するか EXPKIT_ROOT を設定する。"
    )


def experiments_dir(root: Path | None = None) -> Path:
    return (root or repo_root()) / "experiments"


def decisions_dir(root: Path | None = None) -> Path:
    return (root or repo_root()) / "decisions"


def knowledge_dir(root: Path | None = None) -> Path:
    return (root or repo_root()) / "knowledge"


def reports_dir(root: Path | None = None) -> Path:
    return (root or repo_root()) / "reports"


def record_path(exp_id: str, root: Path | None = None) -> Path:
    return experiments_dir(root) / exp_id / "record.yaml"


def metrics_path(exp_id: str, root: Path | None = None) -> Path:
    return experiments_dir(root) / exp_id / "metrics.json"


def decision_path(dec_id: str, root: Path | None = None) -> Path:
    return decisions_dir(root) / f"{dec_id}.yaml"


def competition_path(root: Path | None = None) -> Path:
    return (root or repo_root()) / "competition.yaml"
