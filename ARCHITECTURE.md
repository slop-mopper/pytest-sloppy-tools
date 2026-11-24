# Architecture

The plugin requires pytest at runtime. The optional `cocotb` extra installs
cocotb for users running simulations; the plugin itself does not import it.
The development dependency group includes cocotb for the NVC integration test.

The `pytest11` entry point registers a small, opt-in plugin. Serial runs use
pytest's own run loop. The parallel run loop starts after collection and
deselection finish, and forks the collected session on Linux. Workers each run an
ordered queue of item indices through pytest's test protocol. The queue builder
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
