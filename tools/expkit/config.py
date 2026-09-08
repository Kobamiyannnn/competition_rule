"""lint 設定の読み込み。既定にリポジトリ直下の lint.yaml をマージする。"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import yaml

from .paths import repo_root

_DEFAULTS = Path(__file__).parent / "defaults" / "lint.yaml"


def _merge(base: Any, over: Any) -> Any:
    """マッピングは再帰マージ、それ以外は上書き。"""
    if isinstance(base, dict) and isinstance(over, dict):
        out = copy.deepcopy(base)
        for k, v in over.items():
            out[k] = _merge(out.get(k), v) if k in out else copy.deepcopy(v)
        return out
    return copy.deepcopy(over)


def load_lint_config(root: Path | None = None) -> dict:
    cfg = yaml.safe_load(_DEFAULTS.read_text(encoding="utf-8"))

    try:
        override_path = (root or repo_root()) / "lint.yaml"
    except Exception:
        override_path = None

    if override_path and override_path.exists():
        override = yaml.safe_load(override_path.read_text(encoding="utf-8")) or {}
        cfg = _merge(cfg, override)

    # extra を patterns に畳み込む。利用側が extra を意識しなくて済むようにする。
    for rule in cfg.get("vocab", {}).values():
        extra = rule.pop("extra", None) or []
        rule["patterns"] = list(rule.get("patterns", [])) + list(extra)

    return cfg


def load_competition(root: Path | None = None) -> dict:
    from .paths import competition_path

    path = competition_path(root)
    if not path.exists():
        return {}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
