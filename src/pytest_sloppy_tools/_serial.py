# _serial.py
# SPDX-License-Identifier: CC0-1.0
# This file was created by an AI model: openai/gpt-6.2-sol

"""Serial output display without replacing pytest's run loop."""

import logging
import os
import sys
from collections.abc import Generator
from contextlib import ExitStack, contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event, RLock, Thread
from typing import TextIO

import pytest
from _pytest.capture import CaptureManager, MultiCapture
from _pytest.logging import LoggingPlugin, catching_logs
from _pytest.terminal import TerminalReporter

from ._output import _STREAMS, _Output, _stream_path
from ._pretty import _get_pretty, _interactive


@contextmanager
def _redirect_streams(directory: Path, index: int) -> Generator[None]:
	"""Route native writes, Python streams, and inherited child descriptors."""
	with ExitStack() as restore:
		for fd, stream in enumerate(_STREAMS, start=1):
			python_stream = getattr(sys, stream)
			python_stream.flush()
			saved = os.dup(fd)
			restore.callback(os.close, saved)
			restore.callback(os.dup2, saved, fd, inheritable=os.get_inheritable(fd))
			with _stream_path(directory, index, stream).open("ab") as file:
				os.dup2(file.fileno(), fd)
			restore.callback(python_stream.flush)
		yield


@contextmanager
def _capture(config: pytest.Config, *, explicit: bool) -> Generator[None]:
	"""Rebase pytest's capture onto the per-test streams."""
	manager = config.pluginmanager.get_plugin("capturemanager")
	if not isinstance(manager, CaptureManager):
		yield
		return
	original = manager._global_capturing
	manager._global_capturing = None
	try:
		if explicit:
			manager._global_capturing = MultiCapture(in_=None, out=None, err=None)
			manager._global_capturing.start_capturing()
		else:
			manager.start_global_capturing()
		manager.suspend_global_capture(in_=True)
		yield
	finally:
		try:
			manager.stop_global_capturing()
		finally:
			manager._global_capturing = original


@contextmanager
def _logs(config: pytest.Config, *, explicit: bool) -> Generator[None]:
	plugin = config.pluginmanager.get_plugin("logging-plugin")
	if not isinstance(plugin, LoggingPlugin) or not (explicit or plugin._log_cli_enabled()):
		yield
		return

	# Write to the spool even while a capture fixture owns stderr. Pytest's
	# separate caplog, report, and file handlers continue to receive records.
	with os.fdopen(os.dup(2), "w", encoding="utf-8", errors="backslashreplace") as stream:
		handler = logging.StreamHandler(stream)
		cli = plugin.log_cli_handler
		handler.setFormatter(cli.formatter or plugin.formatter)
		if plugin._log_cli_enabled():
			emit = cli.emit
			cli.emit = handler.emit
			try:
				yield
			finally:
				cli.emit = emit
		else:
			level = plugin.log_level if plugin.log_level is not None else logging.WARNING
			with catching_logs(handler, level=level):
				yield


class _Serial:
	def __init__(self, config: pytest.Config, mode: str | None) -> None:
		self.pretty = _get_pretty(config)
		terminal = config.pluginmanager.get_plugin("terminalreporter")
		self.terminal = terminal if isinstance(terminal, TerminalReporter) else None
		self.mode = mode
		self.interactive = _interactive(config)
		self.capture = config.getoption("capture", "no")
		self.live_logs = False
		self.directory: Path | None = None
		self.index: int | None = None
		self.sections: list[tuple[str, str]] = []
		self.lock = RLock()

	@pytest.hookimpl(wrapper=True)
	def pytest_runtestloop(self, session: pytest.Session) -> Generator[None, object, object]:
		logging_plugin = session.config.pluginmanager.get_plugin("logging-plugin")
		self.live_logs = isinstance(logging_plugin, LoggingPlugin) and logging_plugin._log_cli_enabled()
		with TemporaryDirectory(prefix="pytest-sloppy-serial-") as directory:
			self.directory = Path(directory)
			try:
				return (yield)
			finally:
				self.directory = None

	@contextmanager
	def _display(self, item: pytest.Item, index: int) -> Generator[None]:
		assert self.directory is not None
		terminal = self.terminal
		writer = terminal._tw if terminal is not None else None
		original = writer._file if writer is not None else sys.stdout
		original.flush()
		with (
			os.fdopen(os.dup(1), "w", encoding=original.encoding or "utf-8", errors=original.errors) as display,
			_redirect_streams(self.directory, index),
		):
			if writer is not None:
				writer._file = display
			try:
				with (
					self._read_output(item, index, display),
					_logs(item.config, explicit=self.mode is not None),
					_capture(item.config, explicit=self.mode is not None),
				):
					yield
			finally:
				if writer is not None:
					writer._file = original

	@contextmanager
	def _read_output(self, item: pytest.Item, index: int, display: TextIO) -> Generator[None]:
		assert self.directory is not None
		output = _Output(self.directory, index, item.nodeid)

		def heading(index: int, title: str) -> None:
			if self.pretty is not None:
				self.pretty.heading(index, title)
			else:
				if self.terminal is not None:
					self.terminal.ensure_newline()
				write(f"{title}\n")

		def write(text: str) -> None:
			if self.terminal is not None:
				self.terminal.write(text, flush=True)
			else:
				print(text, end="", file=display, flush=True)

		stop = Event()
		errors: list[Exception] = []

		def poll() -> None:
			try:
				while not stop.wait(0.03):
					with self.lock:
						output.show(heading, write, final=False)
			except Exception as exc:
				errors.append(exc)

		thread = None
		try:
			if self.mode == "live" or self.mode is None and (self.capture != "fd" or self.live_logs):
				thread = Thread(target=poll, name="pytest-sloppy-output")
				thread.start()
			try:
				yield
			finally:
				try:
					for stream in _STREAMS:
						getattr(sys, stream).flush()
				finally:
					if thread is not None:
						stop.set()
						thread.join()
				output.show(heading, write, final=True)
			if errors:
				raise errors[0]
		finally:
			output.close()

	@pytest.hookimpl(wrapper=True, tryfirst=True)
	def pytest_runtest_protocol(self, item: pytest.Item) -> Generator[None, object, object]:
		pretty = self.pretty
		index = pretty.indices[id(item)] if pretty is not None else 0
		self.index = index
		self.sections = []
		if pretty is not None:
			pretty.start(index)
		try:
			if self.interactive:
				result = yield
			else:
				with self._display(item, index):
					result = yield
			if pretty is not None:
				for title, text in self.sections:
					prefix, _, stream = title.partition(" ")
					stream = stream.split(" ", 1)[0]
					if prefix != "Captured" or not text:
						continue
					if stream == "log" and (self.live_logs or self.mode is not None):
						continue
					if self.capture == "tee-sys" and stream in _STREAMS:
						continue
					pretty.heading(index, f"--- {item.nodeid} [{stream}] ---")
					pretty.terminal.write(text if text.endswith("\n") else text + "\n", flush=True)
				pretty.finish(index)
			return result
		finally:
			self.index = None

	@pytest.hookimpl(wrapper=True, tryfirst=True)
	def pytest_runtest_logreport(self, report: pytest.TestReport) -> Generator[None, object, object]:
		with self.lock:
			if self.pretty is not None and self.index is not None:
				self.pretty.report(self.index, report)
				# Each phase report includes the sections from earlier phases.
				self.sections = report.sections
			return (yield)

	@pytest.hookimpl(wrapper=True, tryfirst=True)
	def pytest_runtest_logstart(self) -> Generator[None, object, object]:
		with self.lock:
			return (yield)

	@pytest.hookimpl(wrapper=True, tryfirst=True)
	def pytest_runtest_logfinish(self) -> Generator[None, object, object]:
		with self.lock:
			return (yield)


def _install_serial(config: pytest.Config, mode: str | None) -> None:
	if mode is not None and _interactive(config):
		raise pytest.UsageError("--output does not support interactive debugging or fixture inspection")
	config.pluginmanager.register(_Serial(config, mode), "sloppy-serial")
