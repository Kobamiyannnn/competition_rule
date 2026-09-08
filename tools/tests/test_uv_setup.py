"""uv を唯一の入り口にした構成が崩れていないことを確かめる。

pip でのフォールバックを持たない設計なので、入り口が1つであること自体を守る。
2つあると「uv が無い環境でだけ挙動が違う」状態が生まれ、
再現性を土台にしているこの基盤ではそれ自体が害になる。
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def pyproject() -> dict:
    return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))


class TestProjectDefinition:
    def test_declares_the_expctl_entry_point(self, pyproject: dict) -> None:
        assert pyproject["project"]["scripts"]["expctl"] == "expkit.cli:main"

    def test_declares_its_runtime_dependency(self, pyproject: dict) -> None:
        deps = " ".join(pyproject["project"]["dependencies"])
        assert "pyyaml" in deps.lower()

    def test_pins_the_python_version(self, pyproject: dict) -> None:
        assert pyproject["project"]["requires-python"]
        pinned = (ROOT / ".python-version").read_text(encoding="utf-8").strip()
        assert pinned, ".python-version が空"

    def test_pytest_config_lives_in_pyproject(self, pyproject: dict) -> None:
        """設定ファイルを2つに散らさない。"""
        assert "pytest" in pyproject["tool"]
        assert not (ROOT / "pytest.ini").exists(), "pytest.ini が残っている"


class TestLockFile:
    def test_lock_is_committed(self) -> None:
        """uv.lock は再現性の土台なのでコミットする。"""
        import subprocess

        assert (ROOT / "uv.lock").exists()
        out = subprocess.run(
            ["git", "check-ignore", "-q", "uv.lock"], cwd=ROOT, capture_output=True
        )
        assert out.returncode != 0, "uv.lock が .gitignore で除外されている"

    def test_venv_is_ignored(self) -> None:
        import subprocess

        out = subprocess.run(
            ["git", "check-ignore", "-q", ".venv/"], cwd=ROOT, capture_output=True
        )
        assert out.returncode == 0, ".venv が追跡対象になっている"


class TestSingleEntryPoint:
    def test_the_shim_script_is_gone(self) -> None:
        """入り口は uv run expctl の1つだけ。"""
        assert not (ROOT / "tools" / "expctl").exists(), \
            "tools/expctl が残っている。入り口が2つある"

    @pytest.mark.parametrize("doc", [
        "README.md",
        "templates/CLAUDE.md",
        "docs/design.md",
        ".claude/skills/init-competition/SKILL.md",
        ".claude/skills/decide-next/SKILL.md",
        ".claude/skills/log-experiment/SKILL.md",
        ".claude/skills/survey/SKILL.md",
        ".claude/skills/learn-domain/SKILL.md",
        ".claude/skills/write-report/SKILL.md",
    ])
    def test_docs_do_not_reference_the_old_shim(self, doc: str) -> None:
        text = (ROOT / doc).read_text(encoding="utf-8")
        assert "tools/expctl" not in text, f"{doc} が古い呼び出し方を案内している"


class TestMetricsRecordsTheLock:
    def test_lock_fingerprint_is_written(self, repo: Path) -> None:
        """どの依存で回した実験かを1つの値で照合できるようにする。"""
        import hashlib
        import json

        from expkit import metrics

        lock = repo / "uv.lock"
        lock.write_text("# ダミーの lock\n", encoding="utf-8")

        metrics.write("exp0001", metrics={"cv": {"mean": 0.87}}, config={}, root=repo)
        doc = json.loads((repo / "experiments" / "exp0001" / "metrics.json")
                         .read_text(encoding="utf-8"))

        assert doc["env"]["lock"]["file"] == "uv.lock"
        assert doc["env"]["lock"]["sha256"] == hashlib.sha256(lock.read_bytes()).hexdigest()

    def test_absent_lock_is_recorded_as_none(self, repo: Path) -> None:
        import json

        from expkit import metrics

        metrics.write("exp0001", metrics={"cv": {"mean": 0.87}}, config={}, root=repo)
        doc = json.loads((repo / "experiments" / "exp0001" / "metrics.json")
                         .read_text(encoding="utf-8"))
        assert doc["env"]["lock"] is None
