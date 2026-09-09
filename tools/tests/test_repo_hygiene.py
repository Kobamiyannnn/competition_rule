"""リポジトリの衛生。

テンプレートを clone してコンペを始めるとき、
記録が誤って除外されたり、データが誤って追跡されたりしないことを固定する。
どちらも静かに壊れる種類の間違いなので、機械で見る。
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _ignored(path: str) -> bool:
    out = subprocess.run(
        ["git", "check-ignore", "-q", path], cwd=ROOT, capture_output=True
    )
    return out.returncode == 0


@pytest.mark.parametrize("path", [
    "experiments/exp0001/record.yaml",
    "experiments/exp0001/metrics.json",
    "decisions/dec0001.yaml",
    "ideas/backlog.yaml",
    "knowledge/coverage.yaml",
    "knowledge/validation.yaml",
    "knowledge/operations.md",
    "knowledge/cv_lb.md",
    "competition.yaml",
    "lint.yaml",
    "reports/conclusion.md",
])
def test_records_are_tracked(path: str) -> None:
    """記録が除外されると、コンペの資産が静かに消える。"""
    assert not _ignored(path), f"{path} が .gitignore で除外されている"


@pytest.mark.parametrize("path", ["data/train.csv", "input/test.parquet"])
def test_competition_data_is_ignored(path: str) -> None:
    """コンペから受け取ったデータは規約でほぼ確実に再配布禁止。追跡させない。"""
    assert _ignored(path), f"{path} が .gitignore で除外されていない"


@pytest.mark.parametrize("path", [
    "models/lgb.pkl",
    "experiments/exp0001/artifacts/oof.npy",
    "checkpoint.pt",
    "features.feather",
    "wandb/run-1/logs",
])
def test_heavy_artifacts_are_ignored(path: str) -> None:
    """規約ではなく大きさの問題。履歴が肥大化して clone が重くなる。"""
    assert _ignored(path), f"{path} が .gitignore で除外されていない"


@pytest.mark.parametrize("path", ["submissions/exp0007.csv", "output/oof.npy"])
def test_own_outputs_are_ignored_by_default(path: str) -> None:
    """既定では除外するが、理由は再配布禁止ではない。

    提出物はコンペのデータではなく自分の予測。開催中の公開リポジトリに置くと
    「チーム外への共有」に当たりうるため、可視性が分からない側で安全に倒している。
    private なリポジトリなら外してよい、と .gitignore と README に書いてある。
    """
    assert _ignored(path), f"{path} が .gitignore で除外されていない"


def test_gitignore_explains_why_outputs_are_excluded() -> None:
    """理由を書いておかないと、再配布禁止と混同されて外せなくなる。"""
    text = (ROOT / ".gitignore").read_text(encoding="utf-8")
    assert "再配布禁止の対象ではない" in text
    assert "private なリポジトリなら" in text


class TestPrePushHook:
    """テンプレートへの誤プッシュを止める hook。"""

    HOOK = ROOT / "tools" / "githooks" / "pre-push"

    def test_hook_is_executable(self) -> None:
        assert self.HOOK.exists()
        assert self.HOOK.stat().st_mode & 0o111, "実行権限が無い"

    def _run(self, remote_name: str, remote_url: str, cwd: Path) -> int:
        return subprocess.run(
            ["bash", str(self.HOOK), remote_name, remote_url],
            cwd=cwd, capture_output=True,
        ).returncode

    @pytest.fixture()
    def workdir(self, tmp_path: Path) -> Path:
        subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
        (tmp_path / ".template-source").write_text(
            "https://github.com/Kobamiyannnn/competition_rule\n", encoding="utf-8")
        return tmp_path

    @pytest.mark.parametrize("url", [
        "https://github.com/Kobamiyannnn/competition_rule",
        "https://github.com/Kobamiyannnn/competition_rule.git",
        "git@github.com:Kobamiyannnn/competition_rule.git",
        "ssh://git@github.com/Kobamiyannnn/competition_rule.git",
    ])
    def test_blocks_template_in_any_url_form(self, workdir: Path, url: str) -> None:
        assert self._run("origin", url, workdir) != 0, f"{url} を止められていない"

    def test_blocks_remote_named_template(self, workdir: Path) -> None:
        assert self._run("template", "https://github.com/someone/other.git", workdir) != 0

    def test_allows_own_repository(self, workdir: Path) -> None:
        assert self._run("origin", "https://github.com/me/my-competition.git", workdir) == 0

    def test_allows_when_marker_absent(self, tmp_path: Path) -> None:
        """テンプレート由来でないリポジトリでは何もしない。"""
        subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
        assert self._run("origin", "https://github.com/me/anything.git", tmp_path) == 0


class TestBootstrap:
    def test_renames_origin_and_enables_hook(self, tmp_path: Path) -> None:
        """clone してきた状態から、origin を template に付け替える。"""
        work = tmp_path / "comp"
        shutil.copytree(
            ROOT, work,
            ignore=shutil.ignore_patterns(".git", "__pycache__", ".pytest_cache"),
        )
        template = "https://github.com/Kobamiyannnn/competition_rule"
        (work / ".template-source").write_text(template + "\n", encoding="utf-8")

        def git(*args: str) -> subprocess.CompletedProcess:
            return subprocess.run(["git", *args], cwd=work, capture_output=True, text=True)

        git("init", "-q")
        git("remote", "add", "origin", template)

        out = subprocess.run(["bash", "tools/bootstrap.sh"], cwd=work,
                             capture_output=True, text=True)
        assert out.returncode == 0, out.stderr

        remotes = git("remote").stdout.split()
        assert remotes == ["template"], f"origin が残っている: {remotes}"
        assert git("config", "core.hooksPath").stdout.strip() == "tools/githooks"
        assert (work / "data" / "README.md").exists()

    def test_leaves_a_real_origin_alone(self, tmp_path: Path) -> None:
        """すでに自分のリポジトリを向いているなら触らない。"""
        work = tmp_path / "comp"
        shutil.copytree(
            ROOT, work,
            ignore=shutil.ignore_patterns(".git", "__pycache__", ".pytest_cache"),
        )
        (work / ".template-source").write_text(
            "https://github.com/Kobamiyannnn/competition_rule\n", encoding="utf-8")

        def git(*args: str) -> subprocess.CompletedProcess:
            return subprocess.run(["git", *args], cwd=work, capture_output=True, text=True)

        git("init", "-q")
        git("remote", "add", "origin", "https://github.com/me/my-competition.git")

        out = subprocess.run(["bash", "tools/bootstrap.sh"], cwd=work,
                             capture_output=True, text=True)
        assert out.returncode == 0, out.stderr
        assert "origin" in git("remote").stdout.split()
