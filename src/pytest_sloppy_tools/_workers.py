# _workers.py
# SPDX-License-Identifier: CC0-1.0
# This file was created by an AI model: openai/gpt-6.2-sol

"""Worker lifecycle, output transport, and report relay."""

import io
import logging
import os
import signal
import sys
import threading
import time
import traceback
from dataclasses import dataclass
from multiprocessing import Pipe
from multiprocessing.connection import Connection, wait
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Never
from warnings import WarningMessage

import pytest
from _pytest.capture import CaptureManager
from _pytest.warnings import warning_record_to_str

from ._output import _STREAMS, _Output, _stream_path
from ._pretty import _get_pretty


def _item_queues(size: int, count: int) -> list[range]:
	"""Distribute collected item indices, keeping collection order within each worker."""
	return [range(start, size, count) for start in range(count)]


def _flush() -> None:
	sys.stdout.flush()
	sys.stderr.flush()


def _configure_worker(config: pytest.Config, basetemp: Path) -> None:
	manager = config.pluginmanager
	terminal = manager.get_plugin("terminalreporter")
	if terminal is not None:
		manager.unregister(terminal)
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

	if (factory := getattr(config, "_tmp_path_factory", None)) is not None:
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


def _work(session: pytest.Session, queue: range, channel: Connection, directory: Path, basetemp: Path) -> None:
	_configure_worker(session.config, basetemp)
	session.config.pluginmanager.register(_WorkerReports(channel, session.config))
	for position, index in enumerate(queue):
		item = session.items[index]
		nextitem = session.items[queue[position + 1]] if position + 1 < len(queue) else None
		for fd, stream in enumerate(_STREAMS, start=1):
			with _stream_path(directory, index, stream).open("wb") as output:
				os.dup2(output.fileno(), fd)
		channel.send(("begin", index))
		item.ihook.pytest_runtest_protocol(item=item, nextitem=nextitem)
		_flush()
		channel.send(("decision", (str(session.shouldstop or ""), nextitem is not None)))
		proceed = channel.recv()
		if not proceed:
			# On an early stop, the last protocol may have kept broader-scoped
			# fixtures for a queued item that will no longer run.
			session._setupstate.teardown_exact(None)
		_flush()
		channel.send(("end", None))
		if not proceed:
			break


@dataclass
class _Worker:
	pid: int
	channel: Connection
	item: pytest.Item | None = None
	output: _Output | None = None
	stopping: bool = False


def _spawn(session: pytest.Session, queue: range, directory: Path, basetemp: Path, workers: list[_Worker]) -> None:
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
			_work(session, queue, child, directory, basetemp)
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
		os.waitpid(worker.pid, 0)
		worker.channel.close()


def _worker_status(worker: _Worker) -> os.waitid_result | None:
	return os.waitid(os.P_PID, worker.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)


def _crash(worker: _Worker) -> Never:
	status = _worker_status(worker)
	detail = ""
	if status is not None:
		reason = "exit status" if status.si_code == os.CLD_EXITED else "signal"
		detail = f" ({reason} {status.si_status})"
	pytest.exit(
		f"parallel worker {worker.pid} exited before finishing its tests{detail}", pytest.ExitCode.INTERNAL_ERROR
	)


def _collect(session: pytest.Session, directory: Path, workers: list[_Worker], *, live: bool) -> None:
	terminal = session.config.pluginmanager.get_plugin("terminalreporter")
	pretty = _get_pretty(session.config)

	def heading(index: int, title: str) -> None:
		if pretty is not None:
			pretty.heading(index, title)
			return
		if terminal is not None:
			terminal.ensure_newline()
		write(f"\n{title}\n")

	def write(text: str) -> None:
		if terminal is None:
			print(text, end="", flush=True)
		else:
			terminal.write(text, flush=True)

	try:
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
				if event == "begin":
					worker.item = session.items[data]
					worker.output = _Output(directory, data, worker.item.nodeid)
					if pretty is not None:
						pretty.start(data)
				elif event == "interrupt":
					raise KeyboardInterrupt
				elif event == "exit":
					pytest.exit(data[0], data[1])
				elif event == "error":
					pytest.exit(f"parallel worker {worker.pid} failed:\n{data}", pytest.ExitCode.INTERNAL_ERROR)
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
								_, status = os.waitpid(worker.pid, 0)
								worker.channel.close()
								workers.remove(worker)
								if status:
									pytest.exit(
										f"parallel worker {worker.pid} failed during shutdown",
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
		raise pytest.UsageError("--workers requires /proc")
	threads = [thread.name for thread in threading.enumerate() if thread is not threading.current_thread()]
	tasks = len(os.listdir("/proc/self/task"))
	if threads or tasks != 1:
		detail = f"; Python threads: {', '.join(threads)}" if threads else "; no Python thread names available"
		raise pytest.UsageError(
			f"--workers cannot safely fork after collection ({tasks} OS threads{detail}). "
			"A test import or plugin may have started a thread. Defer that initialisation to a fixture, "
			"disable the plugin if it is not needed, or run without --workers."
		)
	_flush()
	with TemporaryDirectory(prefix="pytest-sloppy-") as path:
		directory = Path(path)
		factory = getattr(session.config, "_tmp_path_factory", None)
		basetemp = factory.getbasetemp() if factory is not None else directory
		workers: list[_Worker] = []
		try:
			for number, queue in enumerate(_item_queues(len(session.items), min(count, len(session.items)))):
				_spawn(session, queue, directory, basetemp / f"sloppy-{number}", workers)
		except BaseException:
			_abort(workers)
			raise
		_collect(session, directory, workers, live=live)
	if session.shouldfail:
		raise session.Failed(session.shouldfail)
	if session.shouldstop:
		raise session.Interrupted(session.shouldstop)
