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
		raise ArgumentTypeError("--workers expects a positive integer")
	return count


def pytest_addoption(parser: pytest.Parser) -> None:
	group = parser.getgroup("sloppy-tools", "pytest-sloppy-tools")
	group.addoption(
		"--workers",
		type=_worker_count,
		metavar="N",
		help="run tests on N worker processes (Linux only)",
	)
	group.addoption(
		"--output",
		choices=("live", "batched"),
		help="display test output live or after each test (required with --workers)",
	)
	group.addoption("--pretty", action="store_true", help="show compact, grouped test progress")


@pytest.hookimpl(trylast=True)
def pytest_configure(config: pytest.Config) -> None:
	count = config.getoption("workers")
	output = config.getoption("output")
	xdist = config.getoption("numprocesses", 0) or config.getoption("dist", "no") != "no"
	if config.getoption("pretty") and not config.option.collectonly:
		if xdist or config.getoption("distload", False):
			raise pytest.UsageError("--pretty cannot be combined with xdist distribution")
		from ._pretty import _install

		_install(config, parallel=count is not None)
	if count is None:
		if not config.option.collectonly and (config.getoption("pretty") or output is not None):
			if xdist or config.getoption("distload", False):
				raise pytest.UsageError("--output cannot be combined with xdist distribution")
			from ._serial import _install_serial

			_install_serial(config, output)
		return
	if config.option.collectonly:
		return
	if sys.platform != "linux":
		raise pytest.UsageError("--workers is supported only on Linux")
	if xdist:
		raise pytest.UsageError("--workers cannot be combined with xdist distribution")
	if config.getoption("usepdb") or config.getoption("trace"):
		raise pytest.UsageError("--workers does not support interactive debugging")
	if output is None:
		raise pytest.UsageError("--workers requires --output=live or --output=batched")


@pytest.hookimpl(tryfirst=True)
def pytest_runtestloop(session: pytest.Session) -> bool | None:
	config = session.config
	count = config.getoption("workers")
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

	_run(session, count, live=config.getoption("output") == "live")
	return True
