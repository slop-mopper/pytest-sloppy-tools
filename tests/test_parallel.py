# test_parallel.py
# SPDX-License-Identifier: CC0-1.0
# This file was created by an AI model: openai/gpt-6.2-sol

import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path
from textwrap import dedent

import pytest


pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="parallel runner requires Linux")


def _write(directory: Path, source: str, name: str = "test_example.py") -> None:
	(directory / name).write_text(dedent(source), encoding="utf-8")


def _command(*args: str) -> list[str]:
	return [sys.executable, "-m", "pytest", "-p", "sloppy_tools", "-q", "--color=no", *args]


def _env() -> dict[str, str]:
	return {**os.environ, "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1", "PYTEST_ADDOPTS": "", "PYTEST_PLUGINS": ""}


def _pytest(directory: Path, *args: str) -> subprocess.CompletedProcess[str]:
	return subprocess.run(
		_command(*args), cwd=directory, env=_env(), text=True, capture_output=True, timeout=20, check=False
	)


@pytest.fixture(params=[False, True], ids=["fork", "fresh"])
def fresh_workers(tmp_path: Path, request: pytest.FixtureRequest) -> bool:
	if request.param:
		_write(
			tmp_path,
			"""
			from threading import Event, Thread

			Thread(target=Event().wait, name="example-collection-thread", daemon=True).start()
			""",
			"conftest.py",
		)
	return request.param


@pytest.mark.usefixtures("fresh_workers")
def test_available_worker_drains_shared_queue(tmp_path: Path) -> None:
	_write(
		tmp_path,
		"""
		import os
		import time
		from pathlib import Path

		import pytest

		@pytest.mark.parametrize("case", range(5))
		def test_case(case):
		    if case == 0:
		        deadline = time.monotonic() + 5
		        while not Path("queue_drained").exists():
		            assert time.monotonic() < deadline, "pending cases stuck behind the slow case"
		            time.sleep(0.01)
		    print(f"CASE {case} PID {os.getpid()}")
		    if case == 4:
		        Path("queue_drained").touch()
		""",
	)
	result = _pytest(tmp_path, "--workers=2", "--output=batched")
	assert result.returncode == 0, result.stdout + result.stderr
	matches = sorted(re.findall(r"CASE (\d) PID (\d+)", result.stdout))
	assert [case for case, _ in matches] == list("01234")
	assert len({pid for _, pid in matches[1:]}) == 1
	assert matches[0][1] != matches[1][1]


@pytest.mark.parametrize("mode", ["live", "batched"])
def test_parallel_cases_and_output(tmp_path: Path, mode: str, fresh_workers: bool) -> None:
	_write(
		tmp_path,
		"""
		import logging
		import os
		import subprocess
		import sys
		import time
		import warnings
		from pathlib import Path

		import pytest

		with Path("collected").open("a") as collected:
		    collected.write("one collection\\n")

		@pytest.fixture(scope="module")
		def module_state(tmp_path_factory):
		    build = tmp_path_factory.mktemp("build", numbered=False)
		    print(f"SETUP {build}")
		    yield
		    print("TEARDOWN")

		@pytest.mark.parametrize("case", range(6))
		def test_sim(case, module_state, tmp_path, caplog):
		    assert tmp_path.is_dir()
		    # Neither first case can finish unless the other worker starts.
		    Path(f"started-{case}").touch()
		    if case < 2:
		        deadline = time.monotonic() + 5
		        while not Path(f"started-{1 - case}").exists():
		            assert time.monotonic() < deadline, "tests did not overlap"
		            time.sleep(0.01)
		    print(f"CASE {case} PID {os.getpid()}")
		    print(f"STDERR {case}", file=sys.stderr)
		    with caplog.at_level(logging.INFO):
		        logging.getLogger("sim").info("SIM_LOG %d", case)
		    assert caplog.messages == [f"SIM_LOG {case}"]
		    warnings.warn(f"SIM_WARNING {case}", stacklevel=1)
		    subprocess.run(
		        [sys.executable, "-c", f"import os; os.write(1, b'NATIVE {case}\\\\n')"],
		        check=True,
		    )
		""",
	)
	result = _pytest(tmp_path, "--workers=2", f"--output={mode}", "--log-cli-level=INFO", "--basetemp=test-tmp")
	assert result.returncode == 0, result.stdout + result.stderr
	assert (tmp_path / "collected").read_text() == "one collection\n" * (3 if fresh_workers else 1)
	if fresh_workers:
		assert result.stdout.count("Starting fresh workers") == 1
		assert "2 OS threads; Python threads: example-collection-thread" in result.stdout
	else:
		assert "Starting fresh workers" not in result.stdout
	assert "6 passed" in result.stdout
	assert result.stderr == ""
	assert len(set(re.findall(r"CASE \d+ PID (\d+)", result.stdout))) == 2
	assert result.stdout.count("SETUP ") == result.stdout.count("TEARDOWN") == 2
	assert len(list((tmp_path / "test-tmp").glob("*/build"))) == 2
	for case in range(6):
		assert result.stdout.count(f"CASE {case} PID ") == 1
		assert f"test_example.py::test_sim[{case}] [stdout]" in result.stdout
		assert f"test_example.py::test_sim[{case}] [stderr]" in result.stdout
		assert f"STDERR {case}\n" in result.stdout
		assert f"SIM_LOG {case}\n" in result.stdout
		assert f"UserWarning: SIM_WARNING {case}\n" in result.stdout
		assert f"NATIVE {case}\n" in result.stdout


@pytest.mark.parametrize("mode", ["live", "batched"])
@pytest.mark.parametrize("worker_options", [(), ("--workers=1",)], ids=["serial", "parallel"])
def test_output_timing(tmp_path: Path, mode: str, worker_options: tuple[str, ...]) -> None:
	_write(
		tmp_path,
		"""
		import time
		from pathlib import Path

		def test_example():
		    print("OUTPUT_MARKER", flush=True)
		    Path("printed").touch()
		    deadline = time.monotonic() + 10
		    while not Path("finish").exists():
		        assert time.monotonic() < deadline
		        time.sleep(0.01)
		""",
	)
	log = tmp_path / "terminal.log"
	with (
		log.open("w") as output,
		subprocess.Popen(
			_command(*worker_options, f"--output={mode}"),
			cwd=tmp_path,
			env=_env(),
			stdout=output,
			stderr=subprocess.STDOUT,
		) as process,
	):
		try:
			deadline = time.monotonic() + 5
			while not (tmp_path / "printed").exists() or (mode == "live" and "OUTPUT_MARKER" not in log.read_text()):
				assert process.poll() is None, log.read_text()
				assert time.monotonic() < deadline, log.read_text()
				time.sleep(0.01)
			if mode == "batched":
				time.sleep(0.25)
				assert "OUTPUT_MARKER" not in log.read_text()
		finally:
			(tmp_path / "finish").touch()
		assert process.wait(timeout=5) == 0, log.read_text()
	assert "OUTPUT_MARKER" in log.read_text()


@pytest.mark.parametrize("worker_options", [(), ("--workers=2",)], ids=["serial", "parallel"])
def test_fixture_capture_and_pytest_outcomes(tmp_path: Path, worker_options: tuple[str, ...]) -> None:
	_write(
		tmp_path,
		"""
		import os

		import pytest

		def test_capture(capsys):
		    print("SYS_CAPTURE_ONLY")
		    assert capsys.readouterr().out == "SYS_CAPTURE_ONLY\\n"

		def test_capture_fd(capfd):
		    os.write(2, b"FD_CAPTURE_ONLY\\n")
		    assert capfd.readouterr().err == "FD_CAPTURE_ONLY\\n"

		def test_failure():
		    print("OUTPUT_FROM_FAILURE")
		    assert 1 == 2

		@pytest.mark.skip(reason="skip this")
		def test_skip():
		    pass

		@pytest.mark.xfail(reason="expected failure")
		def test_xfail():
		    assert False
		""",
	)
	result = _pytest(tmp_path, *worker_options, "--output=batched", "--junitxml=report.xml")
	assert result.returncode == 1, result.stdout + result.stderr
	assert "1 failed, 2 passed, 1 skipped, 1 xfailed" in result.stdout
	assert "OUTPUT_FROM_FAILURE" in result.stdout
	assert "SYS_CAPTURE_ONLY" not in result.stdout
	assert "FD_CAPTURE_ONLY" not in result.stdout
	assert (tmp_path / "report.xml").read_text().count("<testcase ") == 5


@pytest.mark.usefixtures("fresh_workers")
def test_maxfail_runs_fixture_finalisers(tmp_path: Path) -> None:
	_write(
		tmp_path,
		"""
		import time
		from pathlib import Path

		import pytest

		@pytest.fixture(scope="module", autouse=True)
		def state():
		    yield
		    print("FINALISED")

		def test_failure():
		    deadline = time.monotonic() + 5
		    while not Path("started").exists():
		        assert time.monotonic() < deadline
		        time.sleep(0.01)
		    assert False

		def test_in_progress():
		    Path("started").touch()
		    time.sleep(0.4)

		@pytest.mark.parametrize("case", range(4))
		def test_queued(case):
		    print("UNEXPECTED TEST")
		""",
	)
	result = _pytest(tmp_path, "-x", "--workers=2", "--output=batched")
	assert result.returncode == 1, result.stdout + result.stderr
	assert result.stdout.count("FINALISED") == 2
	assert "UNEXPECTED TEST" not in result.stdout


@pytest.mark.usefixtures("fresh_workers")
def test_teardown_failure_cancels_reserved_case(tmp_path: Path) -> None:
	_write(
		tmp_path,
		"""
		import pytest

		@pytest.fixture(scope="module", autouse=True)
		def state():
		    yield
		    print("MODULE FINALISED")

		@pytest.fixture
		def broken():
		    yield
		    raise RuntimeError("teardown failed")

		def test_first(broken):
		    pass

		def test_reserved():
		    pytest.fail("reserved case should not run")
		""",
	)
	result = _pytest(tmp_path, "-x", "--workers=1", "--output=batched")
	assert result.returncode == 1, result.stdout + result.stderr
	assert "RuntimeError: teardown failed" in result.stdout
	assert result.stdout.count("MODULE FINALISED") == 1
	assert "test_reserved" not in result.stdout


@pytest.mark.usefixtures("fresh_workers")
def test_worker_crash_is_reported_with_output(tmp_path: Path) -> None:
	_write(
		tmp_path,
		"""
		import os

		def test_crash():
		    print("BEFORE_CRASH")
		    os._exit(7)
		""",
	)
	result = _pytest(tmp_path, "--workers=2", "--output=batched")
	assert result.returncode == 3, result.stdout + result.stderr
	assert "BEFORE_CRASH" in result.stdout
	assert "exited before finishing its tests" in result.stdout + result.stderr


@pytest.mark.usefixtures("fresh_workers")
def test_ctrl_c_reaps_worker(tmp_path: Path) -> None:
	_write(
		tmp_path,
		"""
		import os
		import time
		from pathlib import Path

		def test_wait():
		    Path("pid").write_text(str(os.getpid()))
		    time.sleep(10)
		""",
	)
	with subprocess.Popen(
		_command("--workers=1", "--output=batched"),
		cwd=tmp_path,
		env=_env(),
		stdout=subprocess.PIPE,
		stderr=subprocess.STDOUT,
		text=True,
	) as process:
		try:
			deadline = time.monotonic() + 5
			pid_path = tmp_path / "pid"
			while not (pid_path.exists() and pid_path.stat().st_size):
				assert time.monotonic() < deadline
				time.sleep(0.01)
			pid = int(pid_path.read_text())
			process.send_signal(signal.SIGINT)
			output, _ = process.communicate(timeout=5)
			assert process.returncode == 2, output
			assert not Path(f"/proc/{pid}").exists()
		finally:
			if process.poll() is None:
				process.kill()


def test_serial_and_collection_are_unchanged(tmp_path: Path) -> None:
	_write(tmp_path, 'def test_example():\n    print("SERIAL_CAPTURED")\n')
	serial = _pytest(tmp_path)
	assert serial.returncode == 0, serial.stdout + serial.stderr
	assert "1 passed" in serial.stdout
	assert "SERIAL_CAPTURED" not in serial.stdout
	collect = _pytest(tmp_path, "--collect-only", "--workers=2")
	assert collect.returncode == 0, collect.stdout + collect.stderr
	assert "1 test collected" in collect.stdout
	assert "SERIAL_CAPTURED" not in collect.stdout


@pytest.mark.parametrize(
	("options", "message"),
	[
		(["--workers=0"], "expects a positive integer"),
		(["--workers=2"], "requires --output"),
		(["--output=unknown"], "invalid choice"),
	],
)
def test_invalid_options(tmp_path: Path, options: list[str], message: str) -> None:
	result = _pytest(tmp_path, *options)
	assert result.returncode == 4, result.stdout + result.stderr
	assert message in result.stderr


def test_fresh_workers_verify_order_before_running(tmp_path: Path) -> None:
	_write(
		tmp_path,
		"""
		from pathlib import Path

		def test_a():
		    Path("ran").touch()

		def test_b():
		    Path("ran").touch()
		""",
	)
	_write(
		tmp_path,
		"""
		from pathlib import Path
		from threading import Event, Thread

		Thread(target=Event().wait, daemon=True).start()

		def pytest_collection_modifyitems(items):
		    marker = Path("collected")
		    if marker.exists():
		        items.reverse()
		    marker.touch()
		""",
		"conftest.py",
	)
	result = _pytest(tmp_path, "--workers=2", "--output=batched")
	assert result.returncode == 3, result.stdout + result.stderr
	assert "collected different tests" in result.stdout + result.stderr
	assert "controller collected 'test_example.py::test_a', worker collected 'test_example.py::test_b'" in (
		result.stdout + result.stderr
	)
	assert not (tmp_path / "ran").exists()


def test_fresh_workers_replay_selection_and_keep_one_junit_report(tmp_path: Path) -> None:
	_write(tmp_path, "[pytest]\naddopts = -m keep\nmarkers = keep: selected tests\n", "pytest.ini")
	_write(
		tmp_path,
		"""
		from threading import Event, Thread

		import pytest

		Thread(target=Event().wait, daemon=True).start()

		@pytest.mark.keep
		@pytest.mark.parametrize("case", [0, 1])
		def test_selected(case):
		    print(f"SELECTED={case}")
		    assert case == 0

		@pytest.mark.keep
		def test_filtered():
		    pytest.fail("filtered by keyword")

		def test_selected_without_marker():
		    pytest.fail("filtered by marker")
		""",
	)
	# Keep the in-repo cache, CLI selection, and the config/env addopts path in
	# the replay. No extra options are passed through after the positional "--".
	env = {**_env(), "PYTEST_ADDOPTS": "-k selected"}
	command = _command("--workers=2", "--output=batched", "--pretty", "--junitxml=report.xml", "--", "test_example.py")
	result = subprocess.run(command, cwd=tmp_path, env=env, text=True, capture_output=True, timeout=20, check=False)
	assert result.returncode == 1, result.stdout + result.stderr
	assert "Results: 1 passed, 1 failed, 2 deselected in " in result.stdout
	assert "SELECTED=0" in result.stdout and "SELECTED=1" in result.stdout
	assert (tmp_path / "report.xml").read_text().count("<testcase ") == 2
	# Last-failed selection depends on the controller cache. Workers must use
	# the same starting state and must not replace that cache at shutdown.
	command = _command("--workers=2", "--output=batched", "--lf", "--", "test_example.py")
	result = subprocess.run(command, cwd=tmp_path, env=env, text=True, capture_output=True, timeout=20, check=False)
	assert result.returncode == 1, result.stdout + result.stderr
	assert "Starting fresh workers" in result.stdout
	assert "1 failed, 3 deselected" in result.stdout
	assert "SELECTED=0" not in result.stdout
