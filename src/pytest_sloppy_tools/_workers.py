# _workers.py
# SPDX-License-Identifier: CC0-1.0
# This file was created by an AI model: openai/gpt-6.2-sol

"""Worker lifecycle, output transport, and report relay."""

import io
import json
import logging
import os
import signal
import sys
import threading
import time
import traceback
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from multiprocessing import Pipe
from multiprocessing.connection import Connection, wait
from pathlib import Path
from subprocess import DEVNULL, Popen
from tempfile import TemporaryDirectory
from typing import Never
from warnings import WarningMessage

import pytest
from _pytest.capture import CaptureManager
from _pytest.warnings import warning_record_to_str
from pluggy import HookImpl

from ._output import _STREAMS, _Output, _stream_path
from ._pretty import _get_pretty


def _flush() -> None:
	sys.stdout.flush()
	sys.stderr.flush()


def _configure_worker(config: pytest.Config, basetemp: Path | None) -> None:
	manager = config.pluginmanager
	terminal = manager.get_plugin("terminalreporter")
	if terminal is not None:
		manager.unregister(terminal)
	if (progress := manager.get_plugin("terminalprogress")) is not None:
		manager.unregister(progress)
	if (pretty := _get_pretty(config)) is not None:
		manager.unregister(pretty)

	# Disable global capture in the child only. The new capture manager still
	# activates capsys and capfd when tests request them.
	capture = manager.get_plugin("capturemanager")
	if isinstance(capture, CaptureManager):
		capture.stop_global_capturing()
		manager.unregister(capture)
		worker_capture = CaptureManager("no")
		worker_capture.start_global_capturing()
		manager.register(worker_capture, "capturemanager")

	if basetemp is not None and (factory := getattr(config, "_tmp_path_factory", None)) is not None:
		basetemp.mkdir(mode=0o700)
		factory._basetemp = basetemp

	with open(os.devnull, "rb") as stdin:
		os.dup2(stdin.fileno(), 0)
	for stream in (sys.stdout, sys.stderr):
		if isinstance(stream, io.TextIOWrapper):
			stream.reconfigure(line_buffering=True, write_through=True)

	# Keep pytest's caplog handler and report sections. Move its terminal
	# logging to the worker's stderr so both output modes display records.
	root = logging.getLogger()
	log_plugin = manager.get_plugin("logging-plugin")
	if log_plugin is not None:
		root.removeHandler(log_plugin.log_cli_handler)
		formatter = log_plugin.log_cli_handler.formatter or log_plugin.formatter
		level = log_plugin.log_cli_level
		if level is None:
			level = log_plugin.log_level
	else:
		level = None
		formatter = logging.Formatter("%(levelname)s %(name)s: %(message)s")
	level = logging.WARNING if level is None else level
	handler = logging.StreamHandler(sys.stderr)
	handler.setFormatter(formatter)
	handler.setLevel(level)
	root.addHandler(handler)
	root.setLevel(min(root.level, level))


@dataclass(eq=False)
class _WorkerReports:
	channel: Connection
	config: pytest.Config

	def pytest_runtest_logstart(self, nodeid: str, location: tuple[str, int | None, str]) -> None:
		self.channel.send(("logstart", (nodeid, location)))

	def pytest_runtest_logreport(self, report: pytest.TestReport) -> None:
		payload = self.config.hook.pytest_report_to_serializable(config=self.config, report=report)
		self.channel.send(("report", payload))

	def pytest_runtest_logfinish(self, nodeid: str, location: tuple[str, int | None, str]) -> None:
		self.channel.send(("logfinish", (nodeid, location)))

	def pytest_warning_recorded(self, warning_message: WarningMessage, when: str) -> None:
		if when == "runtest" and not self.config.getoption("disable_warnings"):
			sys.stderr.write(warning_record_to_str(warning_message))


def _work(session: pytest.Session, channel: Connection, directory: Path, basetemp: Path | None) -> None:
	manager = session.config.pluginmanager
	_configure_worker(session.config, basetemp)
	manager.register(_WorkerReports(channel, session.config))
	index: int | None = channel.recv()
	item: pytest.Item | None = None
	next_index: int | None = None
	requested = False

	def request_next() -> None:
		nonlocal requested, next_index
		requested = True
		if not (session.shouldstop or session.shouldfail):
			channel.send(("next", None))
			next_index = channel.recv()

	def before(hook_name: str, _impls: Sequence[HookImpl], kwargs: Mapping[str, object]) -> None:
		if hook_name != "pytest_runtest_teardown" or kwargs["item"] is not item:
			return
		if not requested:
			request_next()
		# Pluggy passes the same kwargs dict to the teardown hook, so built-in
		# and third-party teardown hooks see the actual next item.
		assert isinstance(kwargs, dict)
		kwargs["nextitem"] = (
			session.items[next_index]
			if next_index is not None and not (session.shouldstop or session.shouldfail)
			else None
		)

	undo = manager.add_hookcall_monitoring(before, lambda *_: None)
	try:
		while index is not None:
			item = session.items[index]
			next_index = None
			requested = False
			for fd, stream in enumerate(_STREAMS, start=1):
				with _stream_path(directory, index, stream).open("wb") as output:
					os.dup2(output.fileno(), fd)
			channel.send(("begin", index))
			item.ihook.pytest_runtest_protocol(item=item, nextitem=None)
			_flush()
			if not requested:
				# A custom protocol can omit the teardown hook altogether.
				request_next()
			channel.send(("decision", (str(session.shouldstop or ""), next_index is not None)))
			proceed = channel.recv()
			if not proceed:
				# A stop can arrive after teardown kept fixtures for the next item.
				session._setupstate.teardown_exact(None)
			_flush()
			channel.send(("end", None))
			index = next_index if proceed else None
	finally:
		undo()


@dataclass
class _Worker:
	pid: int
	channel: Connection
	item: pytest.Item | None = None
	output: _Output | None = None
	stopping: bool = False
	process: Popen[bytes] | None = None
	startup_log: Path | None = None

	def reap(self) -> int:
		if self.process is not None:
			return self.process.wait()
		return os.waitstatus_to_exitcode(os.waitpid(self.pid, 0)[1])


def _spawn(session: pytest.Session, directory: Path, basetemp: Path, workers: list[_Worker]) -> None:
	parent, child = Pipe()
	try:
		pid = os.fork()
	except BaseException:
		parent.close()
		child.close()
		raise
	if pid == 0:
		exit_code = 1
		try:
			os.setpgid(0, 0)
			parent.close()
			for worker in workers:
				worker.channel.close()
			_work(session, child, directory, basetemp)
			exit_code = 0
		except KeyboardInterrupt:
			child.send(("interrupt", None))
		except pytest.exit.Exception as exc:
			child.send(("exit", (str(exc), exc.returncode)))
		except BaseException:
			child.send(("error", traceback.format_exc()))
		finally:
			os._exit(exit_code)
	workers.append(_Worker(pid, parent))
	child.close()


def _spawn_fresh(config: pytest.Config, directory: Path, basetemp: Path, workers: list[_Worker]) -> None:
	number = len(workers)
	log = directory / f"startup-{number}.log"
	cache_dir = directory / f"cache-{number}"
	# Seed pytest's collection-order options without letting workers write
	# to the controller's cache. Cache values set by tests are worker-local.
	if (cache := getattr(config, "cache", None)) is not None:
		values = cache_dir / "v" / "cache"
		values.mkdir(parents=True)
		for key in ("lastfailed", "nodeids"):
			value = cache.get(f"cache/{key}", None)
			if value is not None:
				(values / key).write_text(json.dumps(value), encoding="utf-8")

	invocation = config.invocation_params
	args = list(invocation.args)
	end = args.index("--") if "--" in args else len(args)
	args[end:end] = [f"--basetemp={basetemp}", "-o", f"cache_dir={cache_dir}"]
	parent, child = Pipe()
	try:
		with log.open("wb") as output:
			process = Popen(
				[sys.executable, "-m", "pytest_sloppy_tools._fresh", str(child.fileno())],
				cwd=invocation.dir,
				stdin=DEVNULL,
				stdout=output,
				stderr=output,
				pass_fds=(child.fileno(),),
				start_new_session=True,
			)
	except BaseException:
		parent.close()
		raise
	finally:
		child.close()
	workers.append(_Worker(process.pid, parent, process=process, startup_log=log))
	try:
		parent.send((args, invocation.plugins or (), directory))
	except (EOFError, ConnectionError):
		_crash(workers[-1])


def _abort(workers: list[_Worker]) -> None:
	if not workers:
		return
	for sig in (signal.SIGTERM, signal.SIGKILL):
		for worker in workers:
			try:
				os.killpg(worker.pid, sig)
			except ProcessLookupError:
				# The child may not have created its process group yet.
				try:
					os.kill(worker.pid, sig)
				except ProcessLookupError:
					pass
		if sig == signal.SIGTERM:
			time.sleep(0.1)
	for worker in workers:
		worker.reap()
		worker.channel.close()


def _worker_status(worker: _Worker) -> os.waitid_result | None:
	return os.waitid(os.P_PID, worker.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)


def _startup_detail(worker: _Worker) -> str:
	if worker.startup_log is None:
		return ""
	with worker.startup_log.open("rb") as log:
		log.seek(max(0, os.fstat(log.fileno()).st_size - 4096))
		text = log.read(4096).decode("utf-8", errors="replace").strip()
	return f"\nWorker startup or shutdown output:\n{text}" if text else ""


def _crash(worker: _Worker) -> Never:
	status = _worker_status(worker)
	detail = ""
	if status is not None:
		reason = "exit status" if status.si_code == os.CLD_EXITED else "signal"
		detail = f" ({reason} {status.si_status})"
	pytest.exit(
		f"parallel worker {worker.pid} exited before finishing its tests{detail}{_startup_detail(worker)}",
		pytest.ExitCode.INTERNAL_ERROR,
	)


def _check_collection(expected: list[str], actual: list[str], worker: _Worker) -> None:
	for position, (left, right) in enumerate(zip(expected, actual)):
		if left != right:
			detail = f"item {position + 1}: controller collected {left!r}, worker collected {right!r}"
			break
	else:
		if len(expected) == len(actual):
			return
		detail = f"controller collected {len(expected)} items, worker collected {len(actual)}"
	pytest.exit(
		f"fresh worker {worker.pid} collected different tests ({detail})",
		pytest.ExitCode.INTERNAL_ERROR,
	)


def _collect(session: pytest.Session, directory: Path, workers: list[_Worker], *, live: bool) -> None:
	terminal = session.config.pluginmanager.get_plugin("terminalreporter")
	pretty = _get_pretty(session.config)

	def heading(index: int, stream: str) -> None:
		if pretty is not None:
			pretty.heading(index, stream)
			return
		if terminal is not None:
			terminal.ensure_newline()
		write(f"\n--- {session.items[index].nodeid} [{stream}] ---\n")

	def write(text: str) -> None:
		if terminal is None:
			print(text, end="", flush=True)
		else:
			terminal.write(text, flush=True)

	unassigned = iter(range(len(session.items)))

	def assign(worker: _Worker) -> None:
		index = None if session.shouldfail or session.shouldstop else next(unassigned, None)
		try:
			worker.channel.send(index)
		except (EOFError, ConnectionError):
			_crash(worker)

	pending = {worker.pid for worker in workers if worker.process is not None}
	expected = [item.nodeid for item in session.items] if pending else []
	try:
		if not pending:
			for worker in workers:
				assign(worker)
		while workers:
			ready = wait([worker.channel for worker in workers], timeout=0.05 if live else 0.1)
			for worker in workers[:]:
				if worker.channel not in ready:
					# A forked grandchild may still hold open the control socket.
					if _worker_status(worker) is not None and not worker.channel.poll():
						_crash(worker)
					if live and worker.output is not None:
						worker.output.show(heading, write, final=False)
					continue
				try:
					event, data = worker.channel.recv()
				except (EOFError, ConnectionError):
					_crash(worker)
				if event == "collected":
					_check_collection(expected, data, worker)
					pending.remove(worker.pid)
					if not pending:
						for candidate in workers:
							assign(candidate)
				elif event == "begin":
					worker.item = session.items[data]
					worker.output = _Output(directory, data)
					if pretty is not None:
						pretty.start(data)
				elif event == "interrupt":
					raise KeyboardInterrupt
				elif event == "exit":
					pytest.exit(data[0], data[1])
				elif event == "error":
					pytest.exit(
						f"parallel worker {worker.pid} failed:\n{data}{_startup_detail(worker)}",
						pytest.ExitCode.INTERNAL_ERROR,
					)
				else:
					assert worker.item is not None and worker.output is not None
					hook = worker.item.ihook
					match event:
						case "logstart":
							hook.pytest_runtest_logstart(nodeid=data[0], location=data[1])
						case "report":
							config = session.config
							report = config.hook.pytest_report_from_serializable(config=config, data=data)
							assert isinstance(report, pytest.TestReport)
							hook.pytest_runtest_logreport(report=report)
							if pretty is not None:
								pretty.report(worker.output.index, report)
						case "logfinish":
							hook.pytest_runtest_logfinish(nodeid=data[0], location=data[1])
						case "next":
							assign(worker)
						case "decision":
							stop, has_next = data
							if stop and not session.shouldstop:
								session.shouldstop = stop
							worker.stopping = bool(session.shouldfail or session.shouldstop or not has_next)
							try:
								worker.channel.send(not worker.stopping)
							except (EOFError, ConnectionError):
								_crash(worker)
						case "end":
							worker.output.show(heading, write, final=True)
							if pretty is not None:
								pretty.finish(worker.output.index)
							worker.output.close()
							worker.output = worker.item = None
							if worker.stopping:
								status = worker.reap()
								worker.channel.close()
								workers.remove(worker)
								if status:
									pytest.exit(
										f"parallel worker {worker.pid} failed during shutdown{_startup_detail(worker)}",
										pytest.ExitCode.INTERNAL_ERROR,
									)
				if live and worker.output is not None:
					worker.output.show(heading, write, final=False)
	finally:
		_abort(workers)
		for worker in workers:
			if worker.output is not None:
				try:
					worker.output.show(heading, write, final=True)
				finally:
					worker.output.close()
		workers.clear()


def _run(session: pytest.Session, count: int, *, live: bool) -> None:
	if not Path("/proc/self/task").is_dir():
		raise pytest.UsageError("--parallel requires /proc")
	threads = [thread.name for thread in threading.enumerate() if thread is not threading.current_thread()]
	tasks = len(os.listdir("/proc/self/task"))
	fresh = bool(threads or tasks != 1)
	if fresh:
		config = session.config
		if any(not isinstance(plugin, str) for plugin in config.invocation_params.plugins or ()):
			raise pytest.UsageError(
				"fresh workers cannot replay pytest.main() plugins passed as objects; use importable plugin names"
			)
		if config.getoption("debug", False):
			raise pytest.UsageError("fresh workers cannot share a pytest debug file; rerun without --debug")
		if terminal := config.pluginmanager.get_plugin("terminalreporter"):
			detail = f"; Python threads: {', '.join(threads)}" if threads else ""
			terminal.ensure_newline()
			terminal.write_line(f"Starting fresh workers ({tasks} OS threads{detail}); each worker repeats collection.")
	_flush()
	with TemporaryDirectory(prefix="pytest-sloppy-") as path:
		directory = Path(path)
		factory = getattr(session.config, "_tmp_path_factory", None)
		basetemp = factory.getbasetemp() if factory is not None else directory
		workers: list[_Worker] = []
		try:
			for number in range(min(count, len(session.items))):
				worker_temp = basetemp / f"sloppy-{number}"
				if fresh:
					_spawn_fresh(session.config, directory, worker_temp, workers)
				else:
					_spawn(session, directory, worker_temp, workers)
		except BaseException:
			_abort(workers)
			raise
		_collect(session, directory, workers, live=live)
	if session.shouldfail:
		raise session.Failed(session.shouldfail)
	if session.shouldstop:
		raise session.Interrupted(session.shouldstop)
