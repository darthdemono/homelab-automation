import pytest

from homelab.runner import RunError


def test_plan_changes_nothing(env):
    tmp, _, _, _, runner = env
    record = runner.make_plan("demo.tidy")
    assert record["status"] == "planned"
    assert (tmp / "lib" / "a.txt").exists() and (tmp / "lib" / "junk.txt").exists()
    assert not (tmp / "lib" / "note.txt").exists()


def test_apply_then_undo_restores_everything(env):
    tmp, _, _, _, runner = env
    run_id = runner.make_plan("demo.tidy")["id"]
    runner.apply(run_id)
    lib = tmp / "lib"
    assert (lib / "sub" / "a.txt").read_text() == "a" and not (lib / "a.txt").exists()
    assert (lib / "note.txt").read_text() == "hello"
    assert not (lib / "junk.txt").exists()
    assert (tmp / "state" / "quarantine" / run_id / "2-junk.txt").read_text() == "junk"
    assert runner.get(run_id)["status"] == "applied"
    runner.undo(run_id)
    assert (lib / "a.txt").read_text() == "a" and (lib / "junk.txt").read_text() == "junk"
    assert not (lib / "note.txt").exists() and not (lib / "sub" / "a.txt").exists()
    assert runner.get(run_id)["status"] == "undone"


def test_nothing_is_ever_deleted(env):
    tmp, _, _, _, runner = env
    run_id = runner.make_plan("demo.tidy")["id"]
    runner.apply(run_id)
    runner.undo(run_id)
    files = {p.name for p in (tmp / "state" / "quarantine" / run_id).iterdir()}
    assert "undo-note.txt" in files


def test_apply_refuses_when_the_library_changed(env):
    tmp, _, _, _, runner = env
    run_id = runner.make_plan("demo.tidy")["id"]
    (tmp / "lib" / "a.txt").unlink()
    with pytest.raises(RunError, match="changed since the plan"):
        runner.apply(run_id)
    assert (tmp / "lib" / "junk.txt").exists()


def test_only_a_planned_run_can_be_applied_once(env):
    runner = env[4]
    run_id = runner.make_plan("demo.tidy")["id"]
    runner.apply(run_id)
    with pytest.raises(RunError, match="only a planned run"):
        runner.apply(run_id)


def test_undo_refuses_after_an_edit(env):
    tmp, _, _, _, runner = env
    run_id = runner.make_plan("demo.tidy")["id"]
    runner.apply(run_id)
    (tmp / "lib" / "note.txt").write_text("edited")
    with pytest.raises(RunError, match="edited after the run"):
        runner.undo(run_id)
    assert (tmp / "lib" / "sub" / "a.txt").exists()


def test_undo_of_a_planned_run_is_refused(env):
    runner = env[4]
    run_id = runner.make_plan("demo.tidy")["id"]
    with pytest.raises(RunError, match="nothing to undo"):
        runner.undo(run_id)


def test_plan_outside_declared_writes_is_refused(env):
    runner = env[4]
    with pytest.raises(RunError, match="outside the declared writes"):
        runner.make_plan("demo.escape")


def test_exec_is_irreversible_and_undo_says_so(env):
    runner = env[4]
    run_id = runner.make_plan("demo.run")["id"]
    runner.apply(run_id)
    record = runner.undo(run_id)
    assert record["skipped_irreversible"] == ["run python"]


def test_plugin_actions_go_through_the_plugin(env):
    runner, found = env[4], env[3]
    log = found.plugins["demo"].apply_action.__globals__["LOG"]
    run_id = runner.make_plan("demo.custom")["id"]
    runner.apply(run_id)
    runner.undo(run_id)
    assert log == [("apply", 7), ("undo", 7)]


def test_unknown_plugin_and_job(env):
    runner = env[4]
    with pytest.raises(RunError, match="no enabled block"):
        runner.make_plan("nope.x")
    with pytest.raises(RunError, match="no job"):
        runner.make_plan("demo.missing")


def test_disabled_plugin_does_not_run(env):
    runner, config = env[4], env[2]
    config.plugins["demo"].enabled = False
    with pytest.raises(RunError, match="no enabled block"):
        runner.make_plan("demo.tidy")


def test_runs_are_listed(env):
    runner = env[4]
    runner.make_plan("demo.tidy")
    assert len(runner.runs()) == 1
    with pytest.raises(RunError, match="no run"):
        runner.get("missing")
