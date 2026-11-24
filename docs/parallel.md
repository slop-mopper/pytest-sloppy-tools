# Parallel execution

Parallel execution requires **Linux** with `/proc` available. The plugin collects
tests once, then forks worker processes from the collected session. Running pytest
without the parallel options leaves its normal execution and capture settings
unchanged.

```sh
uv run pytest --sloppy-workers=4 --sloppy-output=live --log-cli-level=INFO
uv run pytest --sloppy-workers=4 --sloppy-output=batched --log-cli-level=INFO
```

`--sloppy-workers=N` starts up to `N` workers. `--sloppy-output` is required for a
parallel run; neither mode is the default.

## Output and logs

In `live` mode, the controller displays output as the worker flushes it. For
subprocesses that buffer their output, configure the subprocess to flush or run
unbuffered. In `batched` mode, the controller displays a test's output together
after its setup, call, and teardown finish. Both modes display output for passing
and failing tests and label stdout and stderr with the pytest node ID. Output
within each stream is displayed in order; ordering between stdout and stderr is
not guaranteed. The controller is the only process that displays pytest progress
and the final summary.

Python logging records that reach the root logger are written to the test's
stderr stream. The display level comes from `log_cli_level` (for example,
`--log-cli-level=INFO`), then `log_level`, and defaults to `WARNING`. Log records
also remain available through `caplog` and pytest's report sections. Existing
console log handlers may display the same record a second time.

The plugin uses its own worker output capture, including native writes and
subprocesses that inherit the worker's stdout and stderr. `capsys` and `capfd`
still work: output consumed by a capture fixture does not appear on the terminal.
The worker streams are for terminal display, not for pytest's captured stdout
and stderr report sections or JUnit attachments. Warnings emitted during a test
appear with its stderr unless you pass `--disable-warnings`.

## Scheduling and fixtures

Each collected item, including every parametrised case, is scheduled
independently. The runner distributes items round-robin and runs each worker's
items in collection order. It does not move queued items to an idle worker.
Module- and session-scoped fixtures are local to each worker using them, so a
simulator build in a module-scoped fixture can run more than once.

Use pytest's `tmp_path` fixture for per-test cocotb build directories, or use
`tmp_path_factory.mktemp(...)` for a build reused within a worker. Do not have
parallel tests write to the same build or result directory. Wait for any
subprocesses the test starts before it ends so their output reaches the terminal.

## Limitations

Imports and collection hooks must not leave background threads running when
workers fork. Tests that require interactive input, pytest's interactive debugger,
or xdist distribution must run separately. Plugins that depend on all test hooks
and session hooks running in the same process may not work: test execution hooks
run in the workers and the controller replays their reports.

When `-x` or `--maxfail` triggers, tests that already started finish and workers
stop taking new items. If a worker crashes, pytest reports the crash and terminates
the other workers.
