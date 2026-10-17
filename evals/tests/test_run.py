from dataclasses import replace
from pathlib import Path

import pytest

from evals import run
from evals.evalset import load_eval_set

FIXTURE = Path(__file__).parent / "fixtures" / "mini"


@pytest.fixture
def harness(monkeypatch):
    appended = []
    monkeypatch.setattr(run, "load_eval_set", lambda: load_eval_set(FIXTURE))
    monkeypatch.setattr(run, "load_settings", lambda: None)
    monkeypatch.setattr(run, "append_rows", lambda rows: appended.append(rows))
    return appended


def test_change_is_required():
    with pytest.raises(SystemExit):
        run.main(["--experiment", "E1"])


def test_unimplemented_experiment_logs_nothing(harness):
    assert run.main(["--experiment", "E4", "--change", "x"]) == 2
    assert harness == []


def test_invalid_eval_set_runs_nothing(harness, monkeypatch):
    broken = load_eval_set(FIXTURE)
    broken = replace(broken, transcripts={k: v for k, v in broken.transcripts.items() if k != "ana-s02"})
    monkeypatch.setattr(run, "load_eval_set", lambda: broken)
    monkeypatch.setitem(run.RUNNERS, "E1", lambda evalset, settings, change: pytest.fail("ran on an invalid eval set"))
    assert run.main(["--experiment", "E1", "--change", "x"]) == 1
    assert harness == []


def test_failed_run_logs_nothing(harness, monkeypatch):
    def boom(evalset, settings, change):
        raise RuntimeError("rate limited halfway through")

    monkeypatch.setitem(run.RUNNERS, "E1", boom)
    with pytest.raises(RuntimeError):
        run.main(["--experiment", "E1", "--change", "x"])
    assert harness == []


def test_successful_run_appends_rows(harness, monkeypatch):
    monkeypatch.setitem(run.RUNNERS, "E1", lambda evalset, settings, change: [{"change": change}])
    assert run.main(["--experiment", "E1", "--change", "baseline"]) == 0
    assert harness == [[{"change": "baseline"}]]


def test_variant_option_runs_only_those_variants(harness, monkeypatch):
    monkeypatch.setitem(run.RUNNERS, "E1", lambda evalset, settings, change, only=None: [{"only": only}])
    assert run.main(["--experiment", "E1", "--change", "x", "--variant", "turn", "--variant", "atomic"]) == 0
    assert harness == [[{"only": ("turn", "atomic")}]]
