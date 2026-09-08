#!/usr/bin/env python3
"""Claude Code の hook。記録の保存時に lint を強制し、metrics.json の直接編集を止める。

PreToolUse  : metrics.json への Write/Edit をブロックする。
              数値は実験スクリプトか `expctl lb` からしか書けない。
PostToolUse : record.yaml / decisions/*.yaml が書かれたら lint を回し、
              error があれば終了コード 2 で内容をエージェントに差し戻す。

終了コード 2 の stderr が Claude に渡る。0 は素通し。
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _target_path(payload: dict) -> Path | None:
    fp = (payload.get("tool_input") or {}).get("file_path")
    if not fp:
        return None
    return Path(fp)


def _kind(path: Path) -> str | None:
    parts = path.resolve().parts
    if path.name == "metrics.json" and "experiments" in parts:
        return "metrics"
    if path.name == "record.yaml" and "experiments" in parts:
        return "record"
    if path.suffix in (".yaml", ".yml") and path.parent.name == "decisions":
        return "decision"
    if path.name == "backlog.yaml" and path.parent.name == "ideas":
        return "backlog"
    if path.name == "landscape.yaml" and path.parent.name == "knowledge":
        return "landscape"
    return None


def pre(payload: dict) -> int:
    path = _target_path(payload)
    if path is None or _kind(path) != "metrics":
        return 0
    sys.stderr.write(
        "metrics.json は機械しか書かない。手で編集すると integrity ハッシュがずれて "
        "lint が落ちる。\n"
        "  - 実験の数値: 実験スクリプトから expkit.metrics.write(...) で書く\n"
        "  - LB スコア  : tools/expctl lb <exp_id> --public <score>\n"
        "数値を直したいなら、実験を回し直すか、上のいずれかを使う。\n"
    )
    return 2


def post(payload: dict) -> int:
    path = _target_path(payload)
    if path is None:
        return 0
    kind = _kind(path)
    if kind not in ("record", "decision", "backlog", "landscape"):
        return 0
    if not path.exists():
        return 0

    # リポジトリを特定して lint を回す
    root = path.resolve()
    for parent in root.parents:
        if (parent / "competition.yaml").exists() or (parent / ".git").exists():
            os.environ.setdefault("EXPKIT_ROOT", str(parent))
            break

    try:
        from expkit.cli import main as expctl_main
    except Exception as e:  # noqa: BLE001
        sys.stderr.write(f"lint を回せなかった: {type(e).__name__}: {e}\n")
        return 0

    import io
    from contextlib import redirect_stdout

    buf = io.StringIO()
    try:
        with redirect_stdout(buf):
            code = expctl_main(["lint", str(path)])
    except Exception as e:  # noqa: BLE001
        sys.stderr.write(f"lint が例外で落ちた: {type(e).__name__}: {e}\n")
        return 0

    if code == 0:
        return 0

    sys.stderr.write(buf.getvalue())
    if kind == "landscape":
        sys.stderr.write(
            "\n地固めが検証を通っていない。"
            " これが通るまで modeling 系の軸には進めない。\n"
            "  - 出典は実際に開いた URL だけを書く（捏造すると evidence が意味を失う）\n"
            "  - takeaway は30字以上で、何をどうするのかまで書く\n"
            "  - 出典の種類を偏らせない（論文だけ・解法だけにしない）\n"
            "  - 調べたら在庫に移して transferred_to に書き戻す\n"
            "`tools/expctl landscape check` で URL の生存を確かめられる。\n"
        )
    elif kind == "backlog":
        sys.stderr.write(
            "\nこのアイデアは在庫として数えられない。"
            " 在庫の下限は「打ち手がない」と言わせないための仕組みなので、"
            "中身の薄い項目を数に入れない。\n"
            "  - action は1実験の粒度まで割る（20字以上）\n"
            "  - evidence は出典を指す"
            "（priors/common.md#c01 / exp0003 / dec0002 / URL）\n"
            "  - expected.magnitude と cost は数値で書く\n"
            "`tools/expctl idea add` を使うと、入れる前に検証がかかる。\n"
        )
    else:
        sys.stderr.write(
            "\nこの記録は規範を通っていない。上の指摘を直してから次に進む。\n"
            "語彙規則だけは lint_waived に規則名と20字以上の理由を書けば抜けられる。\n"
            "反証条件・改竄検知・スキーマは抜けられない。\n"
            "反証条件が書けない考えは、hypothesis の要素ごと消すのが正しい対処。\n"
        )
    return 2


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0
    event = payload.get("hook_event_name", "")
    if event == "PreToolUse":
        return pre(payload)
    if event == "PostToolUse":
        return post(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
