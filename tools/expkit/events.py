"""基盤そのものの摩擦を記録する。

実験の記録ではなく、この基盤が使う側の邪魔をした回数を数える。
`.expkit/events.jsonl` に追記し、`.gitignore` で除外する。
git の履歴に混ぜないのは、実験の記録と混同させないため。

何のためにあるか。docs/open-questions.md に並んでいる当て推量の数値
（在庫の下限、tier クォータ、飽和基準、誤り分析の連続回数）は、
実際に使って初めて当たり外れが分かる。そのとき「感想」ではなく
「回数」で判断できるようにしておく。
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

RELATIVE = Path(".expkit") / "events.jsonl"

# 1ファイルが際限なく育たないようにする。摩擦の傾向が見たいだけなので、
# 上限を超えたら古い行から捨てる。
MAX_LINES = 5000


def path(root: Path) -> Path:
    return root / RELATIVE


def record(root: Path, kind: str, **fields: object) -> None:
    """1件追記する。失敗しても作業は止めない。"""
    if os.environ.get("EXPKIT_NO_EVENTS"):
        return
    entry = {
        "at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "kind": kind,
        **fields,
    }
    p = path(root)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        _trim(p)
    except OSError:
        pass


def _trim(p: Path) -> None:
    try:
        lines = p.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    if len(lines) <= MAX_LINES:
        return
    p.write_text("\n".join(lines[-MAX_LINES:]) + "\n", encoding="utf-8")


def load(root: Path) -> list[dict]:
    p = path(root)
    if not p.exists():
        return []
    out: list[dict] = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(entry, dict):
            out.append(entry)
    return out
