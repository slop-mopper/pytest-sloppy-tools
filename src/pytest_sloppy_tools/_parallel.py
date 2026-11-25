# _parallel.py
# SPDX-License-Identifier: CC0-1.0
# This file was created by an AI model: openai/gpt-6.2-sol

"""Pytest entry point for the Linux parallel runner."""

import os
import sys
from argparse import ArgumentTypeError

import pytest


_FRESH_WORKER = pytest.StashKey[bool]()


def _worker_count(value: str) -> int:
	try:
		count = int(value)
	except ValueError:
		count = 0
	if count < 1:
		raise ArgumentTypeError("expected a positive integer")
	return count


def pytest_addoption(parser: pytest.Parser) -> None:
	group = parser.getgroup("sloppy-tools", "pytest-sloppy-tools")
	# Pytest's public addoption reserves lower-case short flags for pytest core.
	group._addoption(
		"-j",
		"--parallel",
		nargs="?",
		const=0,  # An omitted count requests automatic sizing.
		type=_worker_count,
		metavar="N",
		help="run with up to N workers (Linux only; omit N to use half the available CPUs, at least one)",
	)
	group.addoption(
		"--output",
		choices=("live", "batched"),
		help="display test output live or after each test (default: live in parallel runs)",
	)
	group.addoption("--pretty", action="store_true", help="show compact, grouped test progress")


@pytest.hookimpl(trylast=True)
def pytest_configure(config: pytest.Config) -> None:
	if config.stash.get(_FRESH_WORKER, False):
		return
	count = config.getoption("parallel")
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
		raise pytest.UsageError("--parallel is supported only on Linux")
	if xdist:
		raise pytest.UsageError("--parallel cannot be combined with xdist distribution")
	if config.getoption("usepdb") or config.getoption("trace"):
		raise pytest.UsageError("--parallel does not support interactive debugging")


@pytest.hookimpl(tryfirst=True)
def pytest_runtestloop(session: pytest.Session) -> bool | None:
	config = session.config
	count = config.getoption("parallel")
	if config.stash.get(_FRESH_WORKER, False) or count is None or config.option.collectonly or not session.items:
		return None
	if session.testsfailed and not config.option.continue_on_collection_errors:
		# Let pytest report collection failures.
		return None
	if session.shouldfail:
		raise session.Failed(session.shouldfail)
	if session.shouldstop:
		raise session.Interrupted(session.shouldstop)

	from ._workers import _run

	if count == 0:
		count = max(1, (os.process_cpu_count() or 1) // 2)
	_run(session, count, live=config.getoption("output") != "batched")
	return True
