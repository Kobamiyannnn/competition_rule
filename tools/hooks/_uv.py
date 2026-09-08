"""hook から uv 越しにこのリポジトリのコマンドを呼ぶための共通部分。

hook 自体は標準ライブラリだけで動く。expkit を import しないのは、
どの Python から呼ばれるか分からないため。実際の処理は `uv run expctl ...`
に投げ、そこで初めてこのプロジェクトの環境が使われる。

uv が無いときの代替経路は用意しない。無いなら案内して止める。
代替を用意すると「uv が無い環境でだけ挙動が違う」状態が生まれ、
再現性を土台にしているこの基盤では、その状態自体が害になる。
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

INSTALL_GUIDE = """\
uv が見つからない。このリポジトリは uv を前提にしている。

  macOS / Linux : curl -LsSf https://astral.sh/uv/install.sh | sh
  Homebrew      : brew install uv
  Windows       : powershell -c "irm https://astral.sh/uv/install.ps1 | iex"

入れたら `uv sync` を一度実行する。
uv が無いままだと記録の検査が走らず、規範を通っていない記録が残る。
"""


def uv_path() -> str | None:
    return shutil.which("uv")


def repo_root(start: Path) -> Path | None:
    for candidate in (start, *start.parents):
        if (candidate / "pyproject.toml").exists() and (candidate / "tools" / "expkit").exists():
            return candidate
    return None


def run_expctl(root: Path, args: list[str], timeout: float = 120.0) -> subprocess.CompletedProcess:
    """`uv run expctl ...` を実行する。uv が無ければ FileNotFoundError。"""
    uv = uv_path()
    if uv is None:
        raise FileNotFoundError("uv")

    env = dict(os.environ)
    env["EXPKIT_ROOT"] = str(root)
    # hook の中から対話的な解決を始めさせない。
    env.setdefault("UV_NO_PROGRESS", "1")

    return subprocess.run(
        [uv, "run", "--project", str(root), "expctl", *args],
        cwd=root, capture_output=True, text=True, timeout=timeout, env=env,
    )
