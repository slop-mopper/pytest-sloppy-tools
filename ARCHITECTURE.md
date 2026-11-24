# Architecture

The plugin requires pytest at runtime. The optional `cocotb` extra installs
cocotb for users running simulations; the plugin itself does not import it.
The development dependency group includes cocotb for the NVC integration test.

The `pytest11` entry point registers a small, opt-in plugin. Serial runs use
pytest's own run loop. The parallel run loop starts after collection and
deselection finish. On Linux, the controller forks the collected session when
only one thread is present. If Python or `/proc` reports additional threads, the
controller launches fresh interpreters with the original pytest arguments.
Each interpreter collects independently and sends its ordered node IDs to
the controller. Queues are released only after every worker's list matches.
Fresh workers get separate base temporary directories and copies of pytest's
cached collection-order data. Only the controller writes JUnit XML or its
configured pytest log file.

Both launchers run an ordered queue of item indices through pytest's test
protocol in each worker. The queue builder
contains the round-robin scheduling policy; worker execution does not depend
on the policy. Each worker passes its actual next item to pytest so the fixture
manager can reuse broader-scoped fixtures in that process. Each worker uses a
subdirectory of the session's base temporary directory for pytest's `tmp_path`.

The controller owns the terminal and the session result. Each worker sends
lifecycle messages and pytest-serialised reports over a private connection. The
controller replays reports through pytest's reporting hooks and signals whether
the worker should continue once the current test finishes. Only the worker runs
the test and its fixture code.

Workers redirect stdout and stderr at the file descriptor level to a temporary
pair of files per item. The controller reads those files periodically for live
output or at item completion for batched output, and removes them after display.
The streams do not share the report connection and do not fill an IPC pipe
while the controller handles another worker. Separate files prevent a late
write to one item's descriptor from appearing under another item's label.
Worker capture fixtures stay active, and a Python logging handler sends records
to the worker's stderr.

The controller monitors the connections and child process status. A failed or
interrupted run stops and reaps the remaining workers. Each worker has its own
process group so cleanup can also signal its subprocesses.

`--pretty` installs an optional renderer for serial pytest and the parallel
controller. During the run loop it replaces the terminal reporter's progress
hooks and keeps pytest's report categories and summary hooks. The renderer
assigns a slot to each collected item, sizes the percentage column from the
labels and slot counts, and updates the row after teardown. It ends the
progress line when there is output to display. The child process never renders
progress.

Serial runs with `--output` or `--pretty` share the worker stream reader.
The serial adapter redirects file descriptors per item and writes to the
terminal through a separate descriptor. An explicit `--output` mode replaces
global stdout and stderr capture for display, while capture fixtures still work.
Without `--output`, `--pretty` also displays pytest's report capture sections.
Live streams use a reader thread; batched streams are read after teardown.

GitHub Actions builds the Sphinx site and deploys it to Pages from `master`.
The release workflow checks that the tag matches `pyproject.toml`, runs the
tests, and validates the built wheel and source distribution. A separate job
publishes those files with PyPI Trusted Publishing.
