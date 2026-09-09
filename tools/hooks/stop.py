#!/usr/bin/env python3
"""ターンの終わりに実験と決定の記録を検査する。

PostToolUse hook は Write / Edit / MultiEdit にしか掛からないので、
Bash のヒアドキュメント（`cat > record.yaml <<'EOF'`）で書かれた記録は
検査を素通りする。書き方によらず捕まえるには、ターンの終わりに
全件を回すのが確実で、実測で実験50本あたり 0.2 秒しかかからない。

止めるのは一度だけ。stop_hook_active が立っていたら通す。
直せない指摘で無限に止まると、規範ごと外されることになる。
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _uv import INSTALL_GUIDE, repo_root, run_expctl, uv_path  # noqa: E402


def _initialized(root: Path) -> bool:
    try:
        for line in (root / "competition.yaml").read_text(encoding="utf-8").splitlines():
            if line.startswith("name:"):
                return line.split(":", 1)[1].strip().strip("\"'") not in ("", "TODO")
    except OSError:
        return False
    return False


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0
    if payload.get("hook_event_name") != "Stop":
        return 0

    root = repo_root(Path(payload.get("cwd") or ".").resolve())
    if root is None or not (root / "competition.yaml").exists() or not _initialized(root):
        return 0

    if uv_path() is None:
        sys.stderr.write(INSTALL_GUIDE)
        return 0

    try:
        # 実験と決定だけを見る。台帳（landscape / domain / backlog）が未記入でも
        # それぞれのゲートが進行を止めるので、立ち上げ途中に毎ターン止めない。
        out = run_expctl(root, ["lint", "records"], timeout=120.0)
    except (FileNotFoundError, subprocess.SubprocessError):
        return 0

    if out.returncode == 0:
        return 0

    # 一度指摘したら、次は通す。直せない指摘で無限に止めない。
    if payload.get("stop_hook_active"):
        sys.stderr.write(
            "記録がまだ規範を通っていない。直せないなら、"
            "語彙規則は lint_waived で理由つきで抜けられる。\n"
        )
        return 0

    sys.stderr.write(out.stdout)
    sys.stderr.write(
        "\n規範を通っていない記録が残っている。ターンを終える前に直す。\n"
        "Write / Edit だけでなく、Bash で書いた記録もここで検査される。\n"
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
