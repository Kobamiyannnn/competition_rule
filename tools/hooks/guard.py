#!/usr/bin/env python3
"""Claude Code の hook。記録の保存時に lint を強制し、metrics.json の直接編集を止める。

PreToolUse  : metrics.json への Write/Edit をブロックする。
              数値は実験スクリプトか `uv run expctl lb` からしか書けない。
PostToolUse : record.yaml / decisions / backlog / landscape / domain が書かれたら
              lint を回し、error があれば終了コード 2 で内容をエージェントに差し戻す。

終了コード 2 の stderr が Claude に渡る。0 は素通し。
このスクリプト自体は標準ライブラリだけで動き、検査は uv 越しに走らせる。
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _uv import INSTALL_GUIDE, repo_root, run_expctl, uv_path  # noqa: E402

# lint の対象。ここに無いファイルは素通しする。
LINTED = ("record", "decision", "backlog", "landscape", "domain")

ADVICE = {
    "record": (
        "\nこの記録は規範を通っていない。上の指摘を直してから次に進む。\n"
        "語彙規則だけは lint_waived に規則名と20字以上の理由を書けば抜けられる。\n"
        "反証条件・改竄検知・スキーマは抜けられない。\n"
        "反証条件が書けない考えは、hypothesis の要素ごと消すのが正しい対処。\n"
    ),
    "backlog": (
        "\nこのアイデアは在庫として数えられない。"
        " 在庫の下限は「打ち手がない」と言わせないための仕組みなので、"
        "中身の薄い項目を数に入れない。\n"
        "  - action は1実験の粒度まで割る（20字以上）\n"
        "  - evidence は出典を指す（priors/common.md#c01 / exp0003 / s0001 / d0007 / URL）\n"
        "  - expected.magnitude と cost は数値で書く\n"
        "`uv run expctl idea add` を使うと、入れる前に検証がかかる。\n"
    ),
    "landscape": (
        "\n地固めが検証を通っていない。"
        " これが通るまで modeling 系の軸には進めない。\n"
        "  - 出典は実際に開いた URL だけを書く（捏造すると evidence が意味を失う）\n"
        "  - takeaway は30字以上で、何をどうするのかまで書く\n"
        "  - 出典の種類を偏らせない（論文だけ・解法だけにしない）\n"
        "  - 調べたら在庫に移して transferred_to に書き戻す\n"
        "`uv run expctl landscape check` で URL の生存を確かめられる。\n"
    ),
    "domain": (
        "\nドメイン知識の台帳が検証を通っていない。\n"
        "  - confidence を必ず付ける（confirmed / likely / assumed）\n"
        "  - confirmed を名乗るなら、他人が再確認できる根拠を書く"
        "（実験 exp0003 / URL / 出典 s0001）\n"
        "  - implication は25字以上。打ち手にどう効くかが書けない事実は雑学\n"
        "  - 自分で確かめた事実が最低2件は要る（読んだだけの知識で進まない）\n"
    ),
}
ADVICE["decision"] = ADVICE["record"]


def _target_path(payload: dict) -> Path | None:
    fp = (payload.get("tool_input") or {}).get("file_path")
    return Path(fp) if fp else None


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
    if path.name == "domain.yaml" and path.parent.name == "knowledge":
        return "domain"
    return None


def _require_uv(kind: str) -> bool:
    """uv が無いなら案内を出して止める。代替経路は用意しない。"""
    if uv_path() is not None:
        return True
    sys.stderr.write(INSTALL_GUIDE)
    sys.stderr.write(f"\n（{kind} の検査を実行できなかった）\n")
    return False


def pre(payload: dict) -> int:
    path = _target_path(payload)
    if path is None or _kind(path) != "metrics":
        return 0
    sys.stderr.write(
        "metrics.json は機械しか書かない。手で編集すると integrity ハッシュがずれて "
        "lint が落ちる。\n"
        "  - 実験の数値: 実験スクリプトから expkit.metrics.write(...) で書く\n"
        "  - LB スコア  : uv run expctl lb <exp_id> --public <score>\n"
        "数値を直したいなら、実験を回し直すか、上のいずれかを使う。\n"
    )
    return 2


def post(payload: dict) -> int:
    path = _target_path(payload)
    if path is None:
        return 0
    kind = _kind(path)
    if kind not in LINTED or not path.exists():
        return 0

    root = repo_root(path.resolve())
    if root is None:
        return 0
    if not _require_uv(kind):
        return 2

    try:
        out = run_expctl(root, ["lint", str(path)])
    except FileNotFoundError:
        return 2 if _require_uv(kind) else 2
    except subprocess.SubprocessError as e:
        sys.stderr.write(f"lint を実行できなかった: {type(e).__name__}: {e}\n")
        return 0

    if out.returncode == 0:
        return 0

    sys.stderr.write(out.stdout)
    if out.stderr:
        sys.stderr.write(out.stderr)
    sys.stderr.write(ADVICE.get(kind, ""))
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
