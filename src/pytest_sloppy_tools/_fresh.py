# _fresh.py
# SPDX-License-Identifier: CC0-1.0
# This file was created by an AI model: openai/gpt-6.2-sol

"""Private pytest bootstrap for workers that start in fresh interpreters."""

import os
import sys
from contextlib import ExitStack
from multiprocessing.connection import Connection
from pathlib import Path

import pytest

from ._parallel import _FRESH_WORKER
from ._workers import _flush, _work


class _FreshWorker:
	def __init__(self, channel: Connection, directory: Path) -> None:
		self.channel = channel
		self.directory = directory
		self.completed = False

	@pytest.hookimpl(tryfirst=True)
	def pytest_configure(self, config: pytest.Config) -> None:
		config.stash[_FRESH_WORKER] = True
		# The controller owns these files. The worker still captures log
		# records for its reports and streams them to the controller.
		config.option.xmlpath = None
		config.option.log_file = os.devnull

	@pytest.hookimpl(tryfirst=True)
	def pytest_runtestloop(self, session: pytest.Session) -> bool:
		if session.testsfailed and not session.config.option.continue_on_collection_errors:
			raise session.Interrupted("worker collection failed")
		self.channel.send(("collected", [item.nodeid for item in session.items]))
		queue = self.channel.recv()
		with ExitStack() as restore:
			for fd in (1, 2):
				saved = os.dup(fd)
				restore.callback(os.close, saved)
				restore.callback(os.dup2, saved, fd)
			try:
				_work(session, queue, self.channel, self.directory, basetemp=None)
			except KeyboardInterrupt:
				self.channel.send(("interrupt", None))
				raise
			except pytest.exit.Exception as exc:
				self.channel.send(("exit", (str(exc), exc.returncode)))
				raise
			finally:
				_flush()
		self.completed = True
		return True


def _main(fd: int) -> int:
	os.set_inheritable(fd, False)
	with Connection(fd) as channel:
		args, plugins, directory = channel.recv()
		worker = _FreshWorker(channel, directory)
		status = pytest.main(args, plugins=[*plugins, worker])
		_flush()
		if worker.completed and status in (pytest.ExitCode.OK, pytest.ExitCode.TESTS_FAILED):
			return 0
		if not worker.completed:
			channel.send(("error", f"fresh interpreter did not complete its test queue (pytest exit code {status})"))
		return int(status) or 1


if __name__ == "__main__":
	sys.exit(_main(int(sys.argv[1])))
