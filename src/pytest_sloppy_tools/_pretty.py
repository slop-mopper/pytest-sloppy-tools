# _pretty.py
# SPDX-License-Identifier: CC0-1.0
# This file was created by an AI model: openai/gpt-6.2-sol

"""Opt-in progress display for the controller and the serial runner."""

from collections import Counter
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass, field

import pytest
from _pytest._io.wcwidth import wcswidth
from _pytest.capture import CaptureManager
from _pytest.terminal import TerminalReporter


_PLUGIN = "sloppy-pretty"
_PROGRESS_GAP = 12
_PERCENT_WIDTH = 4
_MARKS = {
	"passed": ("·", {"green": True}),
	"failed": ("×", {"red": True}),
	"skipped": ("s", {"yellow": True}),
	"xfailed": ("x", {"yellow": True}),
	"xpassed": ("X", {"yellow": True}),
}


@contextmanager
def _quiet_progress(terminal: TerminalReporter) -> Generator[None]:
	"""Keep pytest's report bookkeeping and summary, but suppress its progress."""
	hook = terminal.config.hook
	start = next(impl for impl in hook.pytest_runtest_logstart.get_hookimpls() if impl.plugin is terminal)
	report_hook = next(impl for impl in hook.pytest_runtest_logreport.get_hookimpls() if impl.plugin is terminal)
	original = start.function, report_hook.function, terminal._show_progress_info

	def logstart(nodeid: str, location: tuple[str, int | None, str]) -> None:
		pass

	def logreport(report: pytest.TestReport) -> None:
		terminal._current_logreport = report
		terminal._tests_ran = True
		category, letter, word = hook.pytest_report_teststatus(report=report, config=terminal.config)
		terminal._add_stats(category, [report])
		if letter or word:
			terminal._progress_nodeids_reported.add(getattr(report, "id", report.nodeid))

	# Pluggy binds these callables at registration. Change only this reporter's
	# implementations for the run loop, not the status hook used by summaries.
	start.function, report_hook.function, terminal._show_progress_info = logstart, logreport, False
	try:
		yield
	finally:
		start.function, report_hook.function, terminal._show_progress_info = original


@dataclass(eq=False)
class _Row:
	label: str
	size: int = 0
	marks: dict[int, str] = field(default_factory=dict)
	fresh: set[int] = field(default_factory=set)


@dataclass
class _Case:
	row: _Row
	slot: int
	outcome: str | None = None
	output_shown: bool = False


def _function(item: pytest.Item) -> tuple[str, str]:
	name = item.originalname if isinstance(item, pytest.Function) else item.name
	return (item.parent.nodeid if item.parent is not None else "", name)


class _Pretty:
	def __init__(self, terminal: TerminalReporter, *, parallel: bool) -> None:
		self.terminal = terminal
		self.parallel = parallel
		config = terminal.config
		capture = config.getoption("capture", "no")
		self.raw_streams = not parallel and capture in ("no", "tee-sys")
		self.direct_streams = not parallel and capture != "fd"
		self.live_logs = (
			not parallel
			and config.pluginmanager.hasplugin("logging")
			and (config.getoption("log_cli_level") is not None or config.getini("log_cli"))
		)
		interactive = any(config.getoption(option) for option in ("usepdb", "trace", "setupshow", "setuponly"))
		self.direct_output = not parallel and (self.direct_streams or self.live_logs or interactive)
		self.in_place = bool(terminal.isatty) and not self.direct_output
		self.cases: list[_Case] = []
		self.rows: dict[tuple[str, str], _Row] = {}
		self.indices: dict[int, int] = {}
		self.percent_column = 0
		self.completed = 0
		self.open_row: _Row | None = None
		self.serial_index: int | None = None
		self.sections: list[tuple[str, str]] = []

	def _prepare(self, items: list[pytest.Item]) -> None:
		keys = [_function(item) for item in items]
		counts = Counter(name for _, name in dict.fromkeys(keys))
		for index, (item, key) in enumerate(zip(items, keys, strict=True)):
			parent, name = key
			if key not in self.rows:
				label = f"{parent}::{name}" if counts[name] > 1 and parent else name
				self.rows[key] = _Row(label if label.isprintable() else repr(label))
			row = self.rows[key]
			self.cases.append(_Case(row, row.size))
			row.size += 1
			self.indices[id(item)] = index
		self.percent_column = max(wcswidth(row.label) + 2 + row.size for row in self.rows.values()) + _PROGRESS_GAP

	def _can_rewrite(self) -> bool:
		return self.in_place and self.percent_column + _PERCENT_WIDTH < self.terminal._tw.fullwidth

	def _seal(self) -> None:
		if self.open_row is not None:
			self.terminal.write("\n", flush=True)
			self.open_row.fresh.clear()
			self.open_row = None

	def _show(self, row: _Row, *, seal: bool = False) -> None:
		writer = self.terminal._tw
		last = max(row.marks, default=-1)
		progress = f"{self.completed * 100 // len(self.cases)}%".rjust(_PERCENT_WIDTH)
		label = f"{row.label}  "
		body_width = wcswidth(label) + last + 1
		in_place = self._can_rewrite() and not seal
		if self.open_row is not row or not in_place:
			self._seal()
		marks = []
		for slot in range(last + 1):
			if slot in row.marks:
				symbol, colour = _MARKS[row.marks[slot]]
				marks.append(writer.markup(symbol, **(colour if slot in row.fresh else {"light": True})))
			else:
				marks.append(" ")
		line = f"{label}{''.join(marks)}{' ' * (self.percent_column - body_width)}{progress}"
		prefix = "\r" if self.open_row is row else ""
		self.terminal.write(prefix + line + ("" if in_place else "\n"), flush=True)
		if in_place:
			self.open_row = row
		else:
			row.fresh.clear()

	def start(self, index: int) -> None:
		row = self.cases[index].row
		if self._can_rewrite() and (self.open_row is None or not self.parallel and self.open_row is not row):
			self._show(row)

	def report(self, index: int, report: pytest.TestReport) -> None:
		case = self.cases[index]
		if report.failed:
			case.outcome = "failed"
		elif case.outcome != "failed":
			if report.skipped:
				case.outcome = "xfailed" if hasattr(report, "wasxfail") else "skipped"
			elif report.when == "call":
				case.outcome = "xpassed" if hasattr(report, "wasxfail") else "passed"

	def _before_output(self, index: int) -> None:
		case = self.cases[index]
		if not case.output_shown and self.open_row is not case.row:
			self._show(case.row, seal=True)
		else:
			self._seal()
		case.output_shown = True

	def heading(self, index: int, title: str) -> None:
		self._before_output(index)
		self.terminal.write(f"{title}\n", flush=True)

	@contextmanager
	def _serial_capture(self) -> Generator[None]:
		capture = self.terminal.config.pluginmanager.get_plugin("capturemanager")
		if self.parallel or self.direct_output or not isinstance(capture, CaptureManager):
			yield
			return

		original = capture.global_and_fixture_disabled

		@contextmanager
		def disabled() -> Generator[None]:
			# Capture fixtures can send output straight to the terminal.
			with original():
				index = self.serial_index
				if index is not None:
					self._before_output(index)
				try:
					yield
				finally:
					if index is not None:
						self.terminal.write("\n", flush=True)

		capture.global_and_fixture_disabled = disabled
		try:
			yield
		finally:
			capture.global_and_fixture_disabled = original

	def finish(self, index: int) -> None:
		case = self.cases[index]
		self.completed += 1
		row = case.row
		if case.outcome is not None:
			row.marks[case.slot] = case.outcome
			row.fresh.add(case.slot)
		if self._can_rewrite() or self.direct_output or case.output_shown or len(row.marks) == row.size:
			self._show(row)

	@pytest.hookimpl(wrapper=True, tryfirst=True)
	def pytest_runtestloop(self, session: pytest.Session) -> Generator[None, object, object]:
		if not session.items:
			return (yield)
		self._prepare(session.items)
		self.terminal.ensure_newline()
		with _quiet_progress(self.terminal), self._serial_capture():
			try:
				return (yield)
			finally:
				self._seal()
				for row in self.rows.values():
					if row.fresh:
						self._show(row, seal=True)

	@pytest.hookimpl(wrapper=True, tryfirst=True)
	def pytest_runtest_protocol(self, item: pytest.Item) -> Generator[None, object, object]:
		if self.parallel:
			return (yield)
		index = self.indices[id(item)]
		self.serial_index = index
		self.sections = []
		self.start(index)
		try:
			result = yield
			if self.direct_streams:
				# Native output can bypass capture and omit the trailing newline.
				self.terminal.write("\n", flush=True)
			for title, text in self.sections:
				parts = title.split()
				if len(parts) < 2 or parts[0] != "Captured" or not text:
					continue
				stream = parts[1]
				if stream == "log" and self.live_logs or stream in ("stdout", "stderr") and self.raw_streams:
					continue
				self.heading(index, f"--- {item.nodeid} [{stream}] ---")
				self.terminal.write(text if text.endswith("\n") else text + "\n", flush=True)
			self.finish(index)
			return result
		finally:
			self.serial_index = None

	@pytest.hookimpl(trylast=True)
	def pytest_runtest_logreport(self, report: pytest.TestReport) -> None:
		if self.serial_index is not None:
			self.report(self.serial_index, report)
			# Each phase report includes the sections from earlier phases.
			self.sections = report.sections


def _install(config: pytest.Config, *, parallel: bool) -> None:
	terminal = config.pluginmanager.get_plugin("terminalreporter")
	if isinstance(terminal, TerminalReporter):
		if (progress := config.pluginmanager.get_plugin("terminalprogress")) is not None:
			config.pluginmanager.unregister(progress)
		config.pluginmanager.register(_Pretty(terminal, parallel=parallel), _PLUGIN)


def _get_pretty(config: pytest.Config) -> _Pretty | None:
	plugin = config.pluginmanager.get_plugin(_PLUGIN)
	return plugin if isinstance(plugin, _Pretty) else None
