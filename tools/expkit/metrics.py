"""metrics.json の書き込みと改竄検知。

この基盤で唯一「機械しか書かない」ファイル。人と LLM は読むだけで、
記述欄からは ${metrics.cv.mean} のような参照でしか触れない。
書き込みのたびに integrity ハッシュを付け直し、lint が照合する。
手で編集すればハッシュがずれ、lint が落ちる。
"""

from __future__ import annotations

import hashlib
import json
import platform
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .paths import metrics_path


class MetricsTampered(RuntimeError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _canonical(doc: dict) -> bytes:
    body = {k: v for k, v in doc.items() if k != "integrity"}
    return json.dumps(body, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()


def digest(doc: dict) -> str:
    return hashlib.sha256(_canonical(doc)).hexdigest()


def _git_info(root: Path) -> dict:
    def run(*args: str) -> str | None:
        try:
            out = subprocess.run(
                ["git", *args], cwd=root, capture_output=True, text=True, timeout=10
            )
        except (OSError, subprocess.SubprocessError):
            return None
        return out.stdout.strip() if out.returncode == 0 else None

    commit = run("rev-parse", "HEAD")
    status = run("status", "--porcelain")
    return {
        "commit": commit,
        "branch": run("rev-parse", "--abbrev-ref", "HEAD"),
        # dirty な状態での実験は再現できない。lint が警告する材料にする。
        "dirty": bool(status) if status is not None else None,
    }


def _lock_info(root: Path) -> dict | None:
    """uv.lock の指紋。どの依存で回した実験かを後から照合できるようにする。

    packages の一覧だけだと推移的依存の版が分からない。lock の指紋があれば
    「この実験と同じ依存で回っているか」を1つの値で確かめられる。
    """
    lock = root / "uv.lock"
    if not lock.exists():
        return None
    return {
        "file": "uv.lock",
        "sha256": hashlib.sha256(lock.read_bytes()).hexdigest(),
    }


def fingerprint_submission(path: Path) -> dict:
    """提出ファイルの指紋。

    「LB 0.8734 を出したのはどのファイルか」を後から照合できるようにする。
    重みも提出物も git に入れないので、これが無いと実験を回し直すまで確かめられない。
    行数も見るのは、途中で切れたファイルを出す事故がよくあるため。
    """
    data = path.read_bytes()
    info: dict = {
        "file": path.name,
        "sha256": hashlib.sha256(data).hexdigest(),
        "bytes": len(data),
    }
    if path.suffix.lower() in (".csv", ".tsv", ".txt"):
        # ヘッダを除いた行数。末尾の改行は数えない。
        info["rows"] = max(0, data.count(b"\n") - (0 if data.endswith(b"\n") else -1) - 1)
    return info


def _env_info() -> dict:
    packages: dict[str, str] = {}
    try:
        from importlib.metadata import distributions

        interesting = {
            "numpy", "pandas", "scikit-learn", "scipy", "lightgbm", "xgboost",
            "catboost", "torch", "transformers", "polars", "optuna",
        }
        for dist in distributions():
            name = (dist.metadata["Name"] or "").lower()
            if name in interesting:
                packages[name] = dist.version
    except Exception:
        pass
    return {
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "packages": dict(sorted(packages.items())),
    }


def write(
    exp_id: str,
    *,
    metrics: dict[str, Any],
    config: dict[str, Any] | None = None,
    seed: int | None = None,
    runtime_seconds: float | None = None,
    root: Path | None = None,
    extra: dict[str, Any] | None = None,
) -> Path:
    """実験スクリプトから呼ぶ唯一の書き込み口。"""
    from .paths import repo_root

    r = root or repo_root()
    path = metrics_path(exp_id, r)
    path.parent.mkdir(parents=True, exist_ok=True)

    doc: dict[str, Any] = {
        "experiment_id": exp_id,
        "written_at": _now(),
        "written_by": "expkit.metrics.write",
        "git": _git_info(r),
        "env": {**_env_info(), "lock": _lock_info(r)},
        "seed": seed,
        "runtime_seconds": runtime_seconds,
        "config": config or {},
        "metrics": metrics,
        "provenance": [
            {"at": _now(), "by": "expkit.metrics.write", "fields": ["config", "metrics", "env", "git"]}
        ],
    }
    if extra:
        doc.update(extra)
    doc["integrity"] = {"algo": "sha256", "value": digest(doc)}

    path.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def load(exp_id: str, root: Path | None = None) -> dict:
    path = metrics_path(exp_id, root)
    if not path.exists():
        raise FileNotFoundError(path)
    return json.loads(path.read_text(encoding="utf-8"))


def verify(exp_id: str, root: Path | None = None) -> None:
    """ハッシュ不一致なら MetricsTampered を投げる。"""
    doc = load(exp_id, root)
    integrity = doc.get("integrity") or {}
    recorded = integrity.get("value")
    if not recorded:
        raise MetricsTampered(f"{exp_id}: integrity 欄がない。expkit 経由で書き直す。")
    actual = digest(doc)
    if actual != recorded:
        raise MetricsTampered(
            f"{exp_id}: metrics.json が手で編集されている "
            f"(記録 {recorded[:12]}… / 実際 {actual[:12]}…)。"
            " 数値は実験スクリプトから expkit.metrics.write で書き直す。"
        )


def annotate(
    exp_id: str,
    *,
    path: str,
    value: Any,
    by: str,
    root: Path | None = None,
) -> Path:
    """機械が取れない値（LB スコアなど）を、経路を記録した上で後から差す。

    path は "metrics.lb.public" のようなドット表記。
    """
    doc = load(exp_id, root)
    verify(exp_id, root)  # 差す前の状態が健全であることを確かめる

    keys = path.split(".")
    cur: dict[str, Any] = doc
    for k in keys[:-1]:
        cur = cur.setdefault(k, {})
        if not isinstance(cur, dict):
            raise ValueError(f"{path}: 途中の {k} がマッピングではない")
    cur[keys[-1]] = value

    doc.setdefault("provenance", []).append(
        {"at": _now(), "by": by, "fields": [path], "source": "manual"}
    )
    doc["integrity"] = {"algo": "sha256", "value": digest(doc)}

    out = metrics_path(exp_id, root)
    out.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return out


def resolve(doc: dict, ref: str) -> Any:
    """"metrics.cv.folds[2]" のような参照を解決する。見つからなければ KeyError。"""
    import re as _re

    cur: Any = doc
    for token in ref.split("."):
        m = _re.fullmatch(r"([^\[\]]+)((?:\[\d+\])*)", token)
        if not m:
            raise KeyError(ref)
        name, idx_part = m.group(1), m.group(2)
        if not isinstance(cur, dict) or name not in cur:
            raise KeyError(ref)
        cur = cur[name]
        for idx in _re.findall(r"\[(\d+)\]", idx_part):
            if not isinstance(cur, (list, tuple)):
                raise KeyError(ref)
            i = int(idx)
            if i >= len(cur):
                raise KeyError(ref)
            cur = cur[i]
    return cur
