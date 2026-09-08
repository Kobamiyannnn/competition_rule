"""ドキュメントが実装からずれないようにする。

README のコマンド表やスキル表は手で書くので、実装を足したときに更新を忘れる。
規範の文書と実装がずれると、読んだ人が存在しないコマンドを叩くことになる。
スキルの例文と linter の一致を固定しているのと同じ理由でここも固定する。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
README = ROOT / "README.md"


def _readme() -> str:
    return README.read_text(encoding="utf-8")


def test_command_table_matches_the_cli() -> None:
    import sys

    sys.path.insert(0, str(ROOT / "tools"))
    from expkit.cli import build_parser

    actions = build_parser()._subparsers._group_actions[0]  # type: ignore[attr-defined]
    implemented = set(actions.choices)
    documented = set(re.findall(r"^\| `expctl (\w+)", _readme(), re.MULTILINE))

    assert documented - implemented == set(), \
        f"README に載っているが実装に無い: {documented - implemented}"
    assert implemented - documented == set(), \
        f"実装にあるが README に載っていない: {implemented - documented}"


def test_skill_table_matches_the_skill_directory() -> None:
    on_disk = {p.name for p in (ROOT / ".claude" / "skills").iterdir() if p.is_dir()}
    documented = set(re.findall(r"^\| `/([\w-]+)`", _readme(), re.MULTILINE))
    assert on_disk == documented, (
        f"README にだけある: {documented - on_disk} / "
        f"ディレクトリにだけある: {on_disk - documented}"
    )


@pytest.mark.parametrize("doc", ["README.md", "docs/design.md", "docs/open-questions.md"])
def test_relative_links_resolve(doc: str) -> None:
    text = (ROOT / doc).read_text(encoding="utf-8")
    base = (ROOT / doc).parent
    for target in re.findall(r"\]\((?!https?://)([^)#]+)", text):
        assert (base / target).exists(), f"{doc}: リンク先が無い — {target}"


def test_readme_points_at_the_design_doc() -> None:
    """なぜこの形なのかは README ではなく設計ドキュメントに置く。

    README に設計論を書き足していくと、使い方が埋もれて案内の役目を果たさなくなる。
    """
    text = _readme()
    assert "docs/design.md" in text
    # 使い方が前半に来ていること。設計論で始まっていないことの粗い検査。
    assert text.index("## 始める") < len(text) // 2


def test_test_count_in_readme_is_current() -> None:
    """「167件」のような数字は放っておくとずれる。"""
    import subprocess

    out = subprocess.run(
        ["python3", "-m", "pytest", "--collect-only", "-q"],
        cwd=ROOT, capture_output=True, text=True,
    )
    m = re.search(r"(\d+) tests? collected", out.stdout)
    if not m:
        pytest.skip("収集結果を読めなかった")
    actual = int(m.group(1))

    stated = re.search(r"tests/\s+(\d+)件", _readme())
    assert stated, "README の構成に件数が書かれていない"
    assert int(stated.group(1)) == actual, (
        f"README は {stated.group(1)}件、実際は {actual}件"
    )
