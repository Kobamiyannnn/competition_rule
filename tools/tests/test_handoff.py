"""端末をまたいだ引き継ぎ。

記録は git で運べるが、端末ごとの設定は運べない。
clone しただけだと core.hooksPath が空のままになり、
テンプレートへの誤プッシュを止めるガードが黙って無効になる。
黙って無効になるのが一番まずいので、気づける状態を固定する。
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SESSION_HOOK = ROOT / "tools" / "hooks" / "session.py"


@pytest.fixture()
def cloned(tmp_path: Path) -> Path:
    """別の端末で clone した直後を模す。git の設定は引き継がれない。"""
    work = tmp_path / "machineB"
    shutil.copytree(
        ROOT, work,
        ignore=shutil.ignore_patterns(".git", "__pycache__", ".pytest_cache",
                                      ".venv", ".expkit", "data"),
    )
    subprocess.run(["git", "init", "-q"], cwd=work, check=True)
    (work / "competition.yaml").write_text(
        (work / "competition.yaml").read_text(encoding="utf-8")
        .replace("name: TODO", "name: テストコンペ"),
        encoding="utf-8",
    )
    return work


def _git(work: Path, *args: str) -> str:
    out = subprocess.run(["git", *args], cwd=work, capture_output=True, text=True)
    return out.stdout.strip()


class TestSetupWarning:
    """clone しただけの端末で Claude Code を開いたら気づけること。"""

    def _context(self, work: Path) -> str:
        import json

        payload = json.dumps({
            "hook_event_name": "SessionStart", "source": "startup", "cwd": str(work),
        })
        out = subprocess.run(
            ["python3", str(SESSION_HOOK)], input=payload,
            cwd=work, capture_output=True, text=True, timeout=180,
        )
        if not out.stdout.strip():
            return ""
        return json.loads(out.stdout)["hookSpecificOutput"]["additionalContext"]

    def test_warns_when_hooks_path_is_unset(self, cloned: Path) -> None:
        text = self._context(cloned)
        assert "この端末の設定が足りない" in text
        assert "pre-push hook が無効" in text

    def test_silent_once_configured(self, cloned: Path) -> None:
        _git(cloned, "config", "core.hooksPath", "tools/githooks")
        assert "この端末の設定が足りない" not in self._context(cloned)

    def test_warning_comes_before_the_brief(self, cloned: Path) -> None:
        """引き継ぎに埋もれると読まれない。"""
        text = self._context(cloned)
        assert text.index("この端末の設定が足りない") < text.index("# 引き継ぎ")


class TestBootstrapOnASecondMachine:
    """bootstrap.sh は自分のコンペリポジトリでも使えること。"""

    def test_sets_hooks_path_without_renaming_origin(self, cloned: Path) -> None:
        _git(cloned, "remote", "add", "origin", "https://github.com/me/my-competition.git")
        out = subprocess.run(["bash", "tools/bootstrap.sh"], cwd=cloned,
                             capture_output=True, text=True, timeout=300)
        assert out.returncode == 0, out.stderr

        assert _git(cloned, "config", "core.hooksPath") == "tools/githooks"
        assert "origin" in _git(cloned, "remote").split(), "origin が改名されてしまった"
        assert "テンプレートを指していない" in out.stdout


class TestWhatTravelsWithGit:
    """何が git で運ばれ、何が運ばれないかを明示しておく。"""

    @pytest.mark.parametrize("path", [
        "experiments", "decisions", "knowledge", "ideas", "feedback",
        ".claude/settings.json", "competition.yaml", "uv.lock",
    ])
    def test_records_and_config_travel(self, path: str) -> None:
        out = subprocess.run(["git", "check-ignore", "-q", path],
                             cwd=ROOT, capture_output=True)
        assert out.returncode != 0, f"{path} が git で運ばれない"

    @pytest.mark.parametrize("path", [".venv/", ".expkit/", "data/"])
    def test_machine_local_things_do_not(self, path: str) -> None:
        out = subprocess.run(["git", "check-ignore", "-q", path],
                             cwd=ROOT, capture_output=True)
        assert out.returncode == 0, f"{path} が git に入ってしまう"


class TestDoctor:
    def test_reports_the_missing_hook_path(self, cloned: Path) -> None:
        out = subprocess.run(["uv", "run", "expctl", "doctor"], cwd=cloned,
                             capture_output=True, text=True, timeout=300)
        assert out.returncode == 1
        assert "pre-push hook が無効" in out.stdout
        assert "bash tools/bootstrap.sh" in out.stdout

    def test_passes_once_configured(self, cloned: Path) -> None:
        _git(cloned, "config", "core.hooksPath", "tools/githooks")
        (cloned / "data").mkdir(exist_ok=True)
        out = subprocess.run(["uv", "run", "expctl", "doctor"], cwd=cloned,
                             capture_output=True, text=True, timeout=300)
        assert out.returncode == 0, out.stdout
        assert "使える状態になっている" in out.stdout
