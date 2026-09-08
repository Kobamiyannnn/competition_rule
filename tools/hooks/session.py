#!/usr/bin/env python3
"""セッションの境界を安くする hook。

SessionStart : 引き継ぎをコンテキストに注入する。startup / resume / clear / compact
               すべてで走るので、compact の直後にも durable な状態が戻る。
PreCompact   : compact を止めはしない（コンテキストが埋まった状態で止めると詰む）。
               代わりに、書き出されていない状態を指摘して引き継ぎを保存する。

このスクリプト自体は標準ライブラリだけで動き、引き継ぎの生成は uv 越しに走らせる。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _uv import INSTALL_GUIDE, repo_root, run_expctl, uv_path  # noqa: E402

HANDOFF = Path(".claude") / "handoff.md"


def _find_root(payload: dict) -> Path | None:
    start = Path(payload.get("cwd") or os.environ.get("CLAUDE_PROJECT_DIR") or ".").resolve()
    root = repo_root(start)
    if root is None or not (root / "competition.yaml").exists():
        return None
    return root


def _initialized(root: Path) -> bool:
    """/init-competition が済んでいるか。未初期化なら黙って何もしない。

    yaml を import せずに済ませる。hook はどの Python から呼ばれるか分からない。
    """
    try:
        for line in (root / "competition.yaml").read_text(encoding="utf-8").splitlines():
            if line.startswith("name:"):
                value = line.split(":", 1)[1].strip().strip("\"'")
                return value not in ("", "TODO")
    except OSError:
        return False
    return False


def _brief(root: Path) -> str | None:
    try:
        out = run_expctl(root, ["brief"])
    except FileNotFoundError:
        return None
    except subprocess.SubprocessError as e:
        return f"（引き継ぎを生成できなかった: {type(e).__name__}: {e}）\n"
    if out.returncode != 0:
        return f"（引き継ぎを生成できなかった）\n{out.stderr}"
    return out.stdout


def session_start(payload: dict) -> int:
    root = _find_root(payload)
    if root is None or not _initialized(root):
        return 0

    if uv_path() is None:
        print(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "SessionStart",
                "additionalContext": INSTALL_GUIDE
                + "\n引き継ぎを出せていない。`uv sync` の後で `uv run expctl brief`。\n",
            }
        }, ensure_ascii=False))
        return 0

    text = _brief(root)
    if not text:
        return 0

    if payload.get("source") == "compact":
        text = ("（compact 直後。以下はファイルから読み直した状態であり、"
                "compact の要約ではない。判断はこちらを使う。）\n\n" + text)

    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": text,
        }
    }, ensure_ascii=False))
    return 0


def _unexternalized(root: Path) -> list[str]:
    """セッションと一緒に失われる状態を数える。"""
    problems: list[str] = []

    # 進行中の実験は「record があって metrics が無い」で見分ける。
    # 引き継ぎの生成と同じ判定を expkit に持たせてあるが、ここでは
    # yaml を読まずに済ませたいのでファイルの有無だけを見る。
    exp_dir = root / "experiments"
    if exp_dir.is_dir():
        for d in sorted(exp_dir.iterdir()):
            record = d / "record.yaml"
            if not d.is_dir() or not record.exists():
                continue
            if (d / "metrics.json").exists():
                continue
            text = record.read_text(encoding="utf-8", errors="replace")
            has_notes = any(
                line.startswith("notes:") and line.split(":", 1)[1].strip() not in ("", "null")
                for line in text.splitlines()
            )
            if not has_notes:
                problems.append(
                    f"{d.name} が途中で止まっている（metrics.json がまだ無い）のに notes が空。"
                    f" どこまで進んだかを experiments/{d.name}/record.yaml の notes に書く。"
                )

    try:
        out = subprocess.run(
            ["git", "status", "--porcelain", "--",
             "experiments", "decisions", "knowledge", "ideas"],
            cwd=root, capture_output=True, text=True, timeout=10,
        )
        if out.returncode == 0 and out.stdout.strip():
            n = len(out.stdout.strip().split("\n"))
            problems.append(
                f"記録に未コミットの変更が {n} 件ある。"
                " コミットしないと、次のセッションが git 履歴から辿れない。"
            )
    except (OSError, subprocess.SubprocessError):
        pass
    return problems


def pre_compact(payload: dict) -> int:
    root = _find_root(payload)
    if root is None or not _initialized(root):
        return 0

    msg = ["compact の前に。", ""]

    if uv_path() is None:
        sys.stderr.write("\n".join(msg) + "\n" + INSTALL_GUIDE)
        return 0

    text = _brief(root)
    if text:
        path = root / HANDOFF
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        msg.append(f"引き継ぎを {HANDOFF} に保存した。compact 後は SessionStart hook が"
                   " ファイルから状態を読み直すので、要約ではなく実データが戻る。")

    problems = _unexternalized(root)
    if problems:
        msg += ["", "ただし、書き出されていない状態がある。compact すると失われる。", ""]
        msg += [f"  - {p}" for p in problems]
        msg += ["", "compact を続ける前に、上を書き出すことを勧める。"]
    else:
        msg += ["", "書き出されていない状態は見つからなかった。"]

    msg += [
        "",
        "次から compact を減らすには、実験1本ごとにセッションを切る。"
        " 引き継ぎが自動で復元されるので、切る側のコストはほぼ無い。"
        " データ探索・失敗の原因追跡・公開解法の読み込みはサブエージェントに投げると、"
        " 本体のコンテキストが埋まらない。",
        "",
    ]
    sys.stderr.write("\n".join(msg))
    # compact は止めない。コンテキストが埋まった状態で止めると逃げ場が無くなる。
    return 0


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0
    event = payload.get("hook_event_name", "")
    if event == "SessionStart":
        return session_start(payload)
    if event == "PreCompact":
        return pre_compact(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
