# _parallel.py
# SPDX-License-Identifier: CC0-1.0
# This file was created by an AI model: openai/gpt-6.2-sol

"""Pytest entry point for the Linux parallel runner."""

import sys
from argparse import ArgumentTypeError

import pytest


def _worker_count(value: str) -> int:
	try:
		count = int(value)
	except ValueError:
		count = 0
	if count < 1:
		raise ArgumentTypeError("--sloppy-workers expects a positive integer")
	return count


def pytest_addoption(parser: pytest.Parser) -> None:
	group = parser.getgroup("sloppy-tools", "pytest-sloppy-tools")
	group.addoption(
		"--sloppy-workers",
		type=_worker_count,
		metavar="N",
		help="run tests on N worker processes (Linux only)",
	)
	group.addoption(
		"--sloppy-output",
		choices=("live", "batched"),
		help="display worker output live or after each test (required with --sloppy-workers)",
	)


def pytest_configure(config: pytest.Config) -> None:
	count = config.getoption("sloppy_workers")
	output = config.getoption("sloppy_output")
	if count is None:
		if output is not None:
			raise pytest.UsageError("--sloppy-output requires --sloppy-workers")
		return
	if config.option.collectonly:
		return
	if sys.platform != "linux":
		raise pytest.UsageError("--sloppy-workers is supported only on Linux")
	if config.getoption("numprocesses", 0) or config.getoption("dist", "no") != "no":
		raise pytest.UsageError("--sloppy-workers cannot be combined with xdist distribution")
	if config.getoption("usepdb") or config.getoption("trace"):
		raise pytest.UsageError("--sloppy-workers does not support interactive debugging")
	if output is None:
		raise pytest.UsageError("--sloppy-workers requires --sloppy-output=live or --sloppy-output=batched")


@pytest.hookimpl(tryfirst=True)
def pytest_runtestloop(session: pytest.Session) -> bool | None:
	config = session.config
	count = config.getoption("sloppy_workers")
	if count is None or config.option.collectonly or not session.items:
		return None
	if session.testsfailed and not config.option.continue_on_collection_errors:
		# Let pytest report collection failures.
		return None
	if session.shouldfail:
		raise session.Failed(session.shouldfail)
	if session.shouldstop:
		raise session.Interrupted(session.shouldstop)

	from ._workers import _run

	_run(session, count, live=config.getoption("sloppy_output") == "live")
	return True
