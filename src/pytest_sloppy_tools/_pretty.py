# _pretty.py
# SPDX-License-Identifier: CC0-1.0
# This file was created by an AI model: openai/gpt-6.2-sol

"""Opt-in terminal display for the controller and the serial runner."""

import re
from collections import Counter
from collections.abc import Callable, Generator
from contextlib import contextmanager
from dataclasses import dataclass, field

import pytest
from _pytest._io.wcwidth import wcswidth
from _pytest.terminal import TerminalReporter, format_session_duration


_PLUGIN = "sloppy-pretty"
_EDGE = "—" * 5
_PROGRESS_GAP = 12
_PERCENT_WIDTH = 4
_STREAM_COLOURS = {"stdout": "blue", "stderr": "yellow", "log": "purple"}
_SUMMARY_COLOURS = {"error": "red", "warnings": "yellow", "deselected": "cyan"}
_MARKS = {
	"passed": ("·", {"green": True}),
	"failed": ("×", {"red": True}),
	"skipped": ("-", {"yellow": True}),
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


@contextmanager
def _replace_summary(terminal: TerminalReporter, summary: Callable[[], None]) -> Generator[None]:
	"""Replace pytest's own summary sections without disabling third-party hooks."""
	config = terminal.config
	methods = ["short_test_summary", "summary_stats", "summary_passes", "summary_xpasses"]
	if config.getoption("tbstyle") == "auto" and not config.getoption("fulltrace"):
		methods.extend(("summary_failures", "summary_errors", "summary_xfailures"))
	originals = {method: getattr(terminal, method) for method in methods}
	try:
		for method in methods:
			setattr(terminal, method, summary if method == "summary_stats" else _noop)
		yield
	finally:
		for method, original in originals.items():
			setattr(terminal, method, original)


def _noop() -> None:
	pass


def _inline(text: str) -> str:
	"""Keep each label or diagnostic on a single terminal line."""
	return "".join(char if char.isprintable() else ascii(char)[1:-1] for char in text)


def _reason(report: pytest.TestReport | pytest.CollectReport) -> str:
	longrepr = report.longrepr
	crash = getattr(longrepr, "reprcrash", None)
	if crash is not None:
		text = crash.message
	elif isinstance(longrepr, tuple):
		text = longrepr[2]
	else:
		text = str(longrepr) if longrepr is not None else ""
	lines = [line.strip() for line in text.splitlines() if line.strip()]
	message = _inline(lines[0] if crash is not None and lines else lines[-1] if lines else "")
	return message[:197] + "…" if len(message) > 200 else message


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
	parameters: str | None
	outcome: str | None = None
	output_shown: bool = False
	finished: bool = False
	failures: list[tuple[str, str]] = field(default_factory=list)


def _function(item: pytest.Item) -> tuple[str, str]:
	name = item.originalname if isinstance(item, pytest.Function) else item.name
	return (item.parent.nodeid if item.parent is not None else "", name)


def _parameters(item: pytest.Item, name: str) -> str | None:
	# Strip the known function name first: a parameter ID may itself contain
	# brackets, path separators, or even pytest's "::" separator.
	suffix = item.name.removeprefix(name)
	return suffix[1:-1] if suffix.startswith("[") and suffix.endswith("]") else None


class _Pretty:
	def __init__(self, terminal: TerminalReporter, *, parallel: bool) -> None:
		self.terminal = terminal
		self.parallel = parallel
		self.in_place = bool(terminal.isatty) and (parallel or not _interactive(terminal.config))
		self.cases: list[_Case] = []
		self.rows: dict[tuple[str, str], _Row] = {}
		self.indices: dict[int, int] = {}
		self.percent_column = 0
		self.completed = 0
		self.open_row: _Row | None = None
		self.output_block: tuple[int, str] | None = None

	def _prepare(self, items: list[pytest.Item]) -> None:
		keys = [_function(item) for item in items]
		counts = Counter(name for _, name in dict.fromkeys(keys))
		for index, (item, key) in enumerate(zip(items, keys, strict=True)):
			parent, name = key
			if key not in self.rows:
				label = f"{parent}::{name}" if counts[name] > 1 and parent else name
				self.rows[key] = _Row(_inline(label))
			row = self.rows[key]
			self.cases.append(_Case(row, row.size, _parameters(item, name)))
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
		self._end_output()
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
			case.failures.append((report.when, _reason(report)))
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

	def _case_label(self, case: _Case) -> str:
		markup = self.terminal._tw.markup
		name = markup(case.row.label, bold=True)
		if case.parameters is None:
			return name
		params = _inline(case.parameters)
		named = [part.partition("=") for part in params.split(";")]
		if all(separator and key.strip().isidentifier() for key, separator, _ in named):
			parts = [
				markup(key.strip(), purple=True) + markup("=", light=True) + markup(value.strip(), cyan=True)
				for key, _, value in named
			]
			formatted = markup("; ", light=True).join(parts)
		else:
			# Positional IDs can contain literal hyphens. Emphasise separators
			# without changing the ID or assuming where each value starts.
			formatted = "".join(
				markup(part, light=True) if part == "-" else markup(part, cyan=True)
				for part in re.split(r"(?<=\w)(-)(?=\w)", params)
			)
		return name + markup(" (", light=True) + formatted + markup(")", light=True)

	def heading(self, index: int, stream: str) -> None:
		if self.output_block == (index, stream):
			return
		self._end_output()
		self._before_output(index)
		markup = self.terminal._tw.markup
		colour = _STREAM_COLOURS.get(stream, "blue")
		label = markup(stream, **{colour: True, "bold": True})
		edge = markup(_EDGE, light=True)
		case = self._case_label(self.cases[index])
		self.terminal.write(f"{edge} {label} {markup('│', light=True)} {case} {edge}\n", flush=True)
		self.output_block = index, stream

	def _end_output(self) -> None:
		if self.output_block is not None:
			_, stream = self.output_block
			self.output_block = None
			footer = self.terminal._tw.markup(f"{_EDGE} end {stream} {_EDGE}", light=True)
			breaks = "\n\n" if stream == "stderr" else "\n"
			self.terminal.write(footer + breaks, flush=True)

	def finish(self, index: int) -> None:
		case = self.cases[index]
		if self.output_block is not None and self.output_block[0] == index:
			self._end_output()
		case.finished = True
		self.completed += 1
		row = case.row
		if case.outcome is not None:
			row.marks[case.slot] = case.outcome
			row.fresh.add(case.slot)
		if self._can_rewrite() or case.output_shown or len(row.marks) == row.size:
			self._show(row)

	@pytest.hookimpl(wrapper=True, tryfirst=True)
	def pytest_sessionstart(self) -> Generator[None, object, object]:
		"""Restyle the title while keeping pytest's header details and timing."""
		terminal = self.terminal
		original = terminal.write_sep

		def write_sep(sep: str, title: str | None = None, fullwidth: int | None = None, **markup: bool) -> None:
			if sep == "=" and title == "test session starts":
				terminal.write_line(f"{_EDGE} test session start {_EDGE}", **markup)
			else:
				original(sep, title, fullwidth, **markup)

		terminal.write_sep = write_sep
		try:
			return (yield)
		finally:
			terminal.write_sep = original

	@pytest.hookimpl(wrapper=True, tryfirst=True)
	def pytest_collection(self, session: pytest.Session) -> Generator[None, object, object]:
		"""Replace the live collection counter with a single final line."""
		terminal = self.terminal
		start = next(impl for impl in session.config.hook.pytest_collection.get_hookimpls() if impl.plugin is terminal)
		original = start.function, terminal.report_collect

		def report_collect(final: bool = False) -> None:
			if not final or terminal.verbosity < 0:
				return
			count = terminal._numcollected
			line = f"{_EDGE} collected {count} {'item' if count == 1 else 'items'}"
			for category, singular, plural in (
				("error", "error", "errors"),
				("deselected", "deselected", "deselected"),
				("skipped", "skipped", "skipped"),
			):
				if total := len(terminal.stats.get(category, ())):
					line += f" / {total} {singular if total == 1 else plural}"
			if len(session.items) < count:
				line += f" / {len(session.items)} selected"
			terminal.write_line(line)

		start.function, terminal.report_collect = _noop, report_collect
		try:
			return (yield)
		finally:
			start.function, terminal.report_collect = original

	@pytest.hookimpl(wrapper=True, tryfirst=True)
	def pytest_runtestloop(self, session: pytest.Session) -> Generator[None, object, object]:
		if not session.items:
			return (yield)
		self._prepare(session.items)
		self.terminal.ensure_newline()
		with _quiet_progress(self.terminal):
			try:
				return (yield)
			finally:
				self._end_output()
				self._seal()
				for row in self.rows.values():
					if row.fresh:
						self._show(row, seal=True)

	def _summary_stats(self) -> None:
		terminal = self.terminal
		markup = terminal._tw.markup
		counts = Counter(case.outcome for case in self.cases if case.finished)
		collection_errors = [
			report for report in terminal.stats.get("error", ()) if isinstance(report, pytest.CollectReport)
		]
		collection_skips = sum(isinstance(report, pytest.CollectReport) for report in terminal.stats.get("skipped", ()))
		if not terminal.no_summary and (collection_errors or any(case.failures for case in self.cases)):
			terminal.write_line("Failed tests", red=True, bold=True)
			cross = markup("×", red=True)
			for report in collection_errors:
				reason = _reason(report)
				label = f"{_inline(report.nodeid) or '<collection>'} [collection]"
				terminal.write_line(f"  {cross} {label}" + (f" — {reason}" if reason else ""))
			for case in self.cases:
				if not case.failures:
					continue
				details = []
				for phase, reason in case.failures:
					if phase == "call":
						details.append(reason)
					else:
						details.append(f"{phase}: {reason}" if reason else phase)
				reasons = "; ".join(filter(None, details))
				terminal.write_line(f"  {cross} {self._case_label(case)}" + (f" — {reasons}" if reasons else ""))
			terminal.write_line("")
		parts = [
			markup(f"{counts[outcome]} {outcome}", **colour)
			for outcome, (_, colour) in _MARKS.items()
			if counts[outcome] or outcome in ("passed", "failed") and self.completed
		]
		if not parts:
			parts.append("no tests ran")
		for category, count in (
			("error", len(collection_errors)),
			("skipped at collection", collection_skips),
			("warnings", len(terminal.stats.get("warnings", ()))),
			("deselected", len(terminal.stats.get("deselected", ()))),
		):
			if count:
				label = "collection error" if category == "error" else category
				if category == "error" and count != 1:
					label += "s"
				if category == "warnings" and count == 1:
					label = "warning"
				parts.append(markup(f"{count} {label}", **{_SUMMARY_COLOURS.get(category, "yellow"): True}))
		duration = format_session_duration(terminal._session_start.elapsed().seconds)
		terminal.write_line(markup("Results: ", bold=True) + ", ".join(parts) + f" in {duration}")

	@pytest.hookimpl(wrapper=True, tryfirst=True)
	def pytest_sessionfinish(self) -> Generator[None]:
		with _replace_summary(self.terminal, self._summary_stats):
			yield


def _interactive(config: pytest.Config) -> bool:
	return any(config.getoption(option) for option in ("usepdb", "trace", "setupshow", "setuponly"))


def _install(config: pytest.Config, *, parallel: bool) -> None:
	terminal = config.pluginmanager.get_plugin("terminalreporter")
	if isinstance(terminal, TerminalReporter):
		if (progress := config.pluginmanager.get_plugin("terminalprogress")) is not None:
			config.pluginmanager.unregister(progress)
		config.pluginmanager.register(_Pretty(terminal, parallel=parallel), _PLUGIN)


def _get_pretty(config: pytest.Config) -> _Pretty | None:
	plugin = config.pluginmanager.get_plugin(_PLUGIN)
	return plugin if isinstance(plugin, _Pretty) else None
