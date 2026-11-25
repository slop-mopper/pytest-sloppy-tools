# test_pretty.py
# SPDX-License-Identifier: CC0-1.0
# This file was created by an AI model: openai/gpt-6.2-sol

import errno
import os
import re
import select
import subprocess
import sys
import time
from pathlib import Path
from textwrap import dedent

import pytest


def _command(*options: str) -> list[str]:
	return [sys.executable, "-m", "pytest", "-p", "sloppy_tools", "-q", "--pretty", *options]


def _env() -> dict[str, str]:
	return {
		**os.environ,
		"PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
		"PYTEST_ADDOPTS": "",
		"PYTEST_PLUGINS": "",
		"COLUMNS": "80",
	}


def _run(directory: Path, *options: str) -> subprocess.CompletedProcess[str]:
	return subprocess.run(
		_command(*options), cwd=directory, env=_env(), text=True, capture_output=True, timeout=20, check=False
	)


def _write(directory: Path, source: str, name: str = "test_example.py") -> None:
	(directory / name).write_text(dedent(source), encoding="utf-8")


@pytest.mark.parametrize("mode", ["serial", "batched", "live"])
def test_pretty_groups_cases_and_reprints_after_output(tmp_path: Path, mode: str) -> None:
	if mode != "serial" and sys.platform != "linux":
		pytest.skip("parallel runner requires Linux")
	_write(
		tmp_path,
		"""
		import os
		import pytest

		@pytest.mark.parametrize("case", [0, 1, 2, 3])
		def test_sim(case):
		    if case == 2:
		        os.write(2, b"simulation trace\\n")
		        assert False, "simulation failed"
		    if case == 3:
		        pytest.skip("simulator unavailable")
		""",
	)
	_write(
		tmp_path,
		"""
		def pytest_terminal_summary(terminalreporter):
		    terminalreporter.write_line("CUSTOM SUMMARY")
		""",
		"conftest.py",
	)
	workers = [] if mode == "serial" else ["--parallel=1", f"--output={mode}"]
	result = _run(tmp_path, "-v", "--no-header", "--color=yes", *workers)
	assert result.returncode == 1, result.stdout + result.stderr
	progress, summary = result.stdout.split("CUSTOM SUMMARY", 1)
	rows = [line for line in progress.splitlines() if line.startswith("test_sim ")]
	passed = "\x1b[32m·\x1b[0m"
	dimmed = "\x1b[2m·\x1b[0m"
	failed = "\x1b[31m×\x1b[0m"
	skipped = "\x1b[33m-\x1b[0m"
	assert len(rows) == 3
	assert passed * 2 in rows[0] and rows[0].endswith("50%")
	assert dimmed * 2 + failed in rows[1] and rows[1].endswith("75%")
	assert dimmed * 2 + "\x1b[2m×\x1b[0m" + skipped in rows[2] and rows[2].endswith("100%")
	assert progress.index(rows[0]) < progress.index("simulation trace\n") < progress.index(rows[1])
	assert progress.count("simulation trace\n") == 1
	assert "PASSED" not in progress
	assert "\x1b[2m————— end stderr —————\x1b[0m\n\n" in progress
	plain_progress = re.sub(r"\x1b\[[0-9;]*m", "", progress)
	assert plain_progress.startswith("————— test session start —————\n————— collected 4 items\ntest_sim ")
	assert "collecting ..." not in plain_progress
	assert (
		"\n————— stderr │ test_sim (2) —————\nsimulation trace\n————— end stderr —————\n\ntest_sim " in plain_progress
	)
	plain = re.sub(r"\x1b\[[0-9;]*m", "", summary)
	assert "  × test_sim (2) — AssertionError: simulation failed\n\nResults: 2 passed, 1 failed, 1 skipped in " in plain
	assert "short test summary info" not in plain
	assert "simulation trace" not in plain


def test_pretty_keeps_header_details_and_deselection_count(tmp_path: Path) -> None:
	_write(tmp_path, "def test_keep(): pass\n\ndef test_drop(): pass\n")
	_write(
		tmp_path,
		"""
		def pytest_report_header(config):
		    return "CUSTOM HEADER"

		def pytest_report_collectionfinish(items):
		    return f"CUSTOM COLLECTION: {len(items)} selected"
		""",
		"conftest.py",
	)
	result = _run(tmp_path, "-v", "--color=no", "-k", "keep")
	assert result.returncode == 0, result.stdout + result.stderr
	assert result.stdout.startswith("————— test session start —————\nplatform ")
	header, collection = result.stdout.split("————— collected ", 1)
	assert "CUSTOM HEADER\n" in header
	assert collection.startswith("2 items / 1 deselected / 1 selected\n")
	assert "CUSTOM COLLECTION: 1 selected\ntest_keep " in result.stdout


def test_pretty_serial_uncaptured_output(tmp_path: Path) -> None:
	_write(
		tmp_path,
		"""
		import os
		import pytest

		@pytest.mark.parametrize("case", [0, 1])
		def test_sim(case):
		    if case == 1:
		        os.write(1, b"native output without newline")
		""",
	)
	result = _run(tmp_path, "-s", "--color=no")
	assert result.returncode == 0, result.stdout + result.stderr
	assert re.search(
		r"^test_sim  · +50%\n————— stdout │ test_sim \(1\) —————\nnative output without newline\n"
		r"————— end stdout —————\ntest_sim  ·· +100%$",
		result.stdout,
		re.MULTILINE,
	)
	assert "\x1b" not in result.stdout


@pytest.mark.parametrize(
	"options",
	[("--capture=tee-sys",), ("--log-cli-level=INFO",), ("--output=batched",), ("--output=live",)],
)
def test_pretty_serial_displays_output_once(tmp_path: Path, options: tuple[str, ...]) -> None:
	_write(
		tmp_path,
		"""
		import logging
		import subprocess
		import sys

		def test_sim(caplog):
		    print("PYTHON_MARKER", flush=True)
		    subprocess.run([sys.executable, "-c", "print('SUBPROCESS_MARKER')"], check=True)
		    logging.warning("LOG_MARKER")
		    assert "LOG_MARKER" in caplog.text
		""",
	)
	result = _run(tmp_path, *options)
	assert result.returncode == 0, result.stdout + result.stderr
	for marker in ("PYTHON_MARKER", "SUBPROCESS_MARKER", "LOG_MARKER"):
		assert result.stdout.count(marker) == 1


def test_pretty_phase_outcomes_and_disambiguation(tmp_path: Path) -> None:
	_write(
		tmp_path,
		"""
		import pytest

		@pytest.fixture
		def cleanup():
		    yield
		    raise ValueError("teardown error")

		def test_sim(cleanup):
		    pass

		@pytest.mark.skip(reason="setup skip")
		def test_skip():
		    assert False
		""",
		"test_a.py",
	)
	_write(tmp_path, "def test_sim(): pass\n", "test_b.py")
	result = _run(tmp_path, "--color=no")
	assert result.returncode == 1, result.stdout + result.stderr
	rows = [
		line for line in result.stdout.splitlines() if line.startswith(("test_a.py::", "test_skip ", "test_b.py::"))
	]
	width = len("test_a.py::test_sim  ×") + 12 + 4
	assert rows == [
		f"{'test_a.py::test_sim  ×':<{width - 4}} 33%",
		f"{'test_skip  -':<{width - 4}} 66%",
		f"{'test_b.py::test_sim  ·':<{width - 4}}100%",
	]
	assert "  × test_a.py::test_sim — teardown: ValueError: teardown error" in result.stdout
	assert "Results: 1 passed, 1 failed, 1 skipped in " in result.stdout


@pytest.mark.skipif(sys.platform != "linux", reason="parallel runner requires Linux")
def test_pretty_parallel_uses_collection_order(tmp_path: Path) -> None:
	_write(
		tmp_path,
		"""
		import time
		from pathlib import Path

		import pytest

		@pytest.mark.parametrize("case", [0, 1, 2, 3])
		def test_sim(case):
		    if case == 0:
		        deadline = time.monotonic() + 5
		        while not Path("worker_one_finished").exists():
		            assert time.monotonic() < deadline
		            time.sleep(0.01)
		    if case == 3:
		        Path("worker_one_finished").touch()
		    if case == 1:
		        pytest.skip("second case skipped")
		""",
	)
	result = _run(tmp_path, "--parallel=2", "--output=batched", "--color=no")
	assert result.returncode == 0, result.stdout + result.stderr
	assert re.findall(r"^test_sim  (.*?) +(\d+%)$", result.stdout, re.MULTILINE) == [("·-··", "100%")]


def _run_terminal(directory: Path, *options: str) -> str:
	master, slave = os.openpty()
	try:
		with subprocess.Popen(
			_command("--color=no", *options), cwd=directory, env=_env(), stdout=slave, stderr=subprocess.STDOUT
		) as process:
			try:
				os.close(slave)
				slave = -1
				deadline = time.monotonic() + 10
				output = bytearray()
				while True:
					assert select.select([master], [], [], max(0, deadline - time.monotonic()))[0], output
					try:
						output.extend(os.read(master, 8192))
					except OSError as exc:
						if exc.errno != errno.EIO:
							raise
						break
				assert process.wait(timeout=2) == 0, output
			finally:
				if process.poll() is None:
					process.kill()
	finally:
		os.close(master)
		if slave != -1:
			os.close(slave)
	return output.decode()


@pytest.mark.skipif(sys.platform != "linux", reason="requires a Linux pseudo-terminal")
@pytest.mark.parametrize(
	"options",
	[
		(),
		("-s",),
		("--capture=sys",),
		("--capture=tee-sys",),
		("--log-cli-level=INFO",),
		("--output=live",),
		("--output=batched",),
	],
)
def test_pretty_quiet_cases_update_one_terminal_row(tmp_path: Path, options: tuple[str, ...]) -> None:
	_write(
		tmp_path,
		"""
		import pytest

		@pytest.mark.parametrize("case", [0, 1, 2])
		def test_sim(case, capsys):
		    with capsys.disabled():
		        pass
		""",
	)
	transcript = _run_terminal(tmp_path, *options)
	(row,) = [line for line in transcript.split("\r\n") if line.startswith("test_sim ")]
	updates = row.split("\r")
	assert [len(update) for update in updates] == [len("test_sim  ···") + 12 + 4] * 4
	assert re.findall(r"test_sim  (·*) +(\d+%)", row) == [("", "0%"), ("·", "33%"), ("··", "66%"), ("···", "100%")]


@pytest.mark.skipif(sys.platform != "linux", reason="requires a Linux pseudo-terminal")
@pytest.mark.parametrize("mode", ["serial", "batched", "live"])
def test_pretty_terminal_output_starts_after_progress_row(tmp_path: Path, mode: str) -> None:
	_write(
		tmp_path,
		"""
		import os

		def test_sim(capfd):
		    with capfd.disabled():
		        os.write(1, b"native output without newline")
		    print("captured Python output")
		""",
	)
	workers = [] if mode == "serial" else ["--parallel=1", f"--output={mode}"]
	transcript = _run_terminal(tmp_path, "-v", "--no-header", *workers)
	assert transcript.startswith("————— test session start —————\r\n————— collected 1 item\r\ntest_sim ")
	assert re.search(
		r"test_sim +0%\r\n————— stdout │ test_sim —————\r\nnative output without newline",
		transcript,
	)
	assert re.search(r"\r\n————— end stdout —————\r\ntest_sim  · {12}100%\r\n", transcript)
	assert transcript.count("stdout │ test_sim") == 1


def test_pretty_parameter_labels_in_output_and_summary(tmp_path: Path) -> None:
	_write(
		tmp_path,
		"""
		import logging
		import pytest

		@pytest.mark.parametrize("width", [32])
		@pytest.mark.parametrize("mode", ["active-low"])
		def test_positional(mode, width):
		    print("POSITIONAL_OUTPUT")

		@pytest.mark.parametrize("case", [0], ids=["width=32;mode=active-low"])
		def test_named(case):
		    logging.warning("SIMULATOR_LOG")
		    try:
		        raise SystemExit(1)
		    except SystemExit as exc:
		        raise RuntimeError("Simulation failed: exit code 1") from exc
		""",
	)
	result = _run(tmp_path, "--color=yes")
	assert result.returncode == 1, result.stdout + result.stderr
	assert "\x1b[36mactive\x1b[0m\x1b[2m-\x1b[0m" in result.stdout
	assert "\x1b[35mwidth\x1b[0m\x1b[2m=\x1b[0m\x1b[36m32\x1b[0m" in result.stdout
	plain = re.sub(r"\x1b\[[0-9;]*m", "", result.stdout)
	assert "————— stdout │ test_positional (active-low-32) —————\nPOSITIONAL_OUTPUT\n————— end stdout —————\n" in plain
	assert "————— log │ test_named (width=32; mode=active-low) —————" in plain
	assert "  × test_named (width=32; mode=active-low) — RuntimeError: Simulation failed: exit code 1" in plain
	assert plain.count("SIMULATOR_LOG") == 1
	assert "During handling of the above exception" not in plain
	assert "captured" not in plain.lower()

	detailed = _run(tmp_path, "--color=no", "--tb=short", "--show-capture=no")
	assert detailed.returncode == 1
	assert "SystemExit: 1" in detailed.stdout
	assert "RuntimeError: Simulation failed: exit code 1" in detailed.stdout
	assert detailed.stdout.count("SIMULATOR_LOG") == 1
	assert "Results: 1 passed, 1 failed in " in detailed.stdout


def test_pretty_collection_error_keeps_the_reason(tmp_path: Path) -> None:
	_write(tmp_path, 'raise RuntimeError("collection broke")\n', "test_broken.py")
	result = _run(tmp_path, "-v", "--no-header", "--color=no")
	assert result.returncode == 2, result.stdout + result.stderr
	assert result.stdout.startswith("————— test session start —————\n————— collected 0 items / 1 error\n")
	assert "× test_broken.py [collection] — RuntimeError: collection broke\n\nResults:" in result.stdout
	assert "1 collection error" in result.stdout
