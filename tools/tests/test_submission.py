"""提出物の扱い。

重みも提出物も git に入れないので、指紋が無いと
「LB 0.8734 を出したのはどのファイルか」を実験を回し直すまで確かめられない。
Code Competition ではローカルに提出ファイルが無いので、強制はしない。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
import yaml

from conftest import base_record, make_experiment
from expkit.metrics import fingerprint_submission

ROOT = Path(__file__).resolve().parents[2]


def _write_csv(path: Path, rows: int) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["id,target"] + [f"{i},0.5" for i in range(rows)]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


class TestFingerprint:
    def test_records_hash_and_size(self, tmp_path: Path) -> None:
        f = _write_csv(tmp_path / "exp0007.csv", 10)
        info = fingerprint_submission(f)
        assert info["file"] == "exp0007.csv"
        assert info["sha256"] == hashlib.sha256(f.read_bytes()).hexdigest()
        assert info["bytes"] == len(f.read_bytes())

    def test_counts_rows_excluding_the_header(self, tmp_path: Path) -> None:
        """途中で切れたファイルを出す事故を捕まえるための行数。"""
        assert fingerprint_submission(_write_csv(tmp_path / "a.csv", 1000))["rows"] == 1000
        assert fingerprint_submission(_write_csv(tmp_path / "b.csv", 1))["rows"] == 1

    def test_counts_rows_without_a_trailing_newline(self, tmp_path: Path) -> None:
        f = tmp_path / "c.csv"
        f.write_text("id,target\n1,0.5\n2,0.5", encoding="utf-8")
        assert fingerprint_submission(f)["rows"] == 2

    def test_binary_formats_have_no_row_count(self, tmp_path: Path) -> None:
        f = tmp_path / "d.parquet"
        f.write_bytes(b"PAR1\x00\x01")
        info = fingerprint_submission(f)
        assert "rows" not in info
        assert info["sha256"]

    def test_different_content_gives_a_different_fingerprint(self, tmp_path: Path) -> None:
        a = fingerprint_submission(_write_csv(tmp_path / "a.csv", 10))
        b = fingerprint_submission(_write_csv(tmp_path / "b.csv", 11))
        assert a["sha256"] != b["sha256"]


class TestLbCommand:
    def _lb(self, repo: Path, capsys: pytest.CaptureFixture[str], **over: object) -> str:
        import argparse

        from expkit.cli import cmd_lb

        args = argparse.Namespace(
            experiment="exp0007", public="0.8734", private=None, file=None,
        )
        for k, v in over.items():
            setattr(args, k, v)
        assert cmd_lb(args) == 0
        return capsys.readouterr().out

    def _metrics(self, repo: Path) -> dict:
        return json.loads(
            (repo / "experiments" / "exp0007" / "metrics.json").read_text(encoding="utf-8"))

    @pytest.fixture()
    def prepared(self, repo: Path) -> Path:
        make_experiment(repo, "exp0007", cv_mean=0.8712, record=base_record("exp0007"))
        return repo

    def _set_kind(self, repo: Path, kind: str) -> None:
        comp = yaml.safe_load((repo / "competition.yaml").read_text(encoding="utf-8"))
        comp["submission"] = {"kind": kind, "extension": "csv"}
        (repo / "competition.yaml").write_text(
            yaml.safe_dump(comp, allow_unicode=True), encoding="utf-8")

    def test_records_the_submission_when_present(
        self, prepared: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _write_csv(prepared / "submissions" / "exp0007.csv", 1000)
        out = self._lb(prepared, capsys)
        assert "exp0007.csv" in out and "1000 行" in out
        sub = self._metrics(prepared)["metrics"]["lb"]["submission"]
        assert sub["rows"] == 1000 and sub["sha256"]

    def test_warns_when_missing(
        self, prepared: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        out = self._lb(prepared, capsys)
        assert "提出ファイルが見つからない" in out
        assert "submission" not in self._metrics(prepared)["metrics"]["lb"]

    def test_quiet_for_notebook_competitions(
        self, prepared: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Code Competition ではローカルに提出ファイルが無いのが正常。"""
        self._set_kind(prepared, "notebook")
        out = self._lb(prepared, capsys)
        assert "提出ファイルが見つからない" not in out
        assert "notebook 提出" in out

    def test_explicit_path_overrides_the_convention(
        self, prepared: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        f = _write_csv(prepared / "out" / "blend.csv", 42)
        out = self._lb(prepared, capsys, file=str(f))
        assert "blend.csv" in out
        assert self._metrics(prepared)["metrics"]["lb"]["submission"]["rows"] == 42

    def test_finds_other_extensions(
        self, prepared: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        (prepared / "submissions").mkdir(exist_ok=True)
        (prepared / "submissions" / "exp0007.parquet").write_bytes(b"PAR1\x00")
        out = self._lb(prepared, capsys)
        assert "exp0007.parquet" in out


def test_submissions_are_not_committed() -> None:
    """コンペデータの派生物なので git に入れない。"""
    import subprocess

    out = subprocess.run(["git", "check-ignore", "-q", "submissions/exp0007.csv"],
                         cwd=ROOT, capture_output=True)
    assert out.returncode == 0
