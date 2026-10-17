import csv
import subprocess

import pytest

from evals.results import COLUMNS, append_rows, git_commit

ROW = {c: "" for c in COLUMNS} | {"experiment": "E1", "variant": "atomic", "change": "a, b"}


def test_append_writes_header_once(tmp_path):
    path = tmp_path / "results.csv"
    append_rows([ROW], path)
    append_rows([ROW], path)
    rows = list(csv.DictReader(path.open(encoding="utf-8")))
    assert len(rows) == 2
    assert rows[0]["change"] == "a, b"
    assert "\r" not in path.read_text(encoding="utf-8")


def test_append_rejects_unknown_columns(tmp_path):
    with pytest.raises(ValueError):
        append_rows([ROW | {"surprise": "1"}], tmp_path / "results.csv")


def _git(repo, *args):
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
                   cwd=repo, check=True, capture_output=True)


def test_git_commit_marks_dirty_except_for_results_csv(tmp_path):
    (tmp_path / "evals").mkdir()
    (tmp_path / "evals" / "results.csv").write_text("header\n")
    (tmp_path / "code.py").write_text("x = 1\n")
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-q", "-m", "init")
    assert not git_commit(tmp_path).endswith("-dirty")
    (tmp_path / "evals" / "results.csv").write_text("header\nrow\n")
    assert not git_commit(tmp_path).endswith("-dirty")
    (tmp_path / "code.py").write_text("x = 2\n")
    assert git_commit(tmp_path).endswith("-dirty")
