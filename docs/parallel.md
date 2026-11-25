# Parallel execution

Parallel execution requires **Linux** with `/proc` available. If only one thread
is running after collection, the plugin forks workers from the collected
session. If extra threads are present, it starts fresh Python interpreters.
Running pytest without the plugin's options leaves its normal execution and
capture settings unchanged.

```sh
uv run --no-dev pytest --parallel --log-cli-level=INFO
uv run --no-dev pytest -j4 --output=batched --log-cli-level=INFO
```

`--parallel=N` (or `-j N`) starts up to `N` workers. Without a count,
`--parallel` and `-j` use half the logical CPUs available to pytest, rounded
down to a minimum of one worker. If you omit the count, place test paths
before the flag or separate them with `--`, as in `pytest -j -- tests/`.
In 0.3.0, `--parallel` replaces `--workers`.

The runner does not require cocotb. If your tests use cocotb, install it
separately or pass `--extra cocotb` to `uv run`.

## Output and logs

Parallel runs default to `--output=live`. Both `--output=live` and
`--output=batched` can also be selected for serial runs. In `live` mode,
flushed output appears while the test runs. If a subprocess buffers its
output, configure it to flush or run unbuffered. In `batched` mode, output
appears after the test's setup, call, and teardown finish. Both modes display
output for passing and failing tests and label stdout and stderr with the
pytest node ID. Output within each stream is displayed in order; ordering
between stdout and stderr is not guaranteed. In parallel runs, only the
controller displays pytest progress and the summary.

Python logging records that reach the root logger are written to the test's
stderr stream. The display level comes from `log_cli_level` (for example,
`--log-cli-level=INFO`), then `log_level`, and defaults to `WARNING`. Log records
also remain available through `caplog` and pytest's report sections. Existing
console log handlers may display the same record a second time.

In parallel runs and serial runs with an explicit `--output` mode, the plugin
captures native writes and output from subprocesses that inherit stdout and
stderr. `capsys` and `capfd` still work: output consumed by a capture fixture
does not appear on the terminal.
The streams are for terminal display and are not added to pytest's captured
stdout and stderr report sections or JUnit attachments. Without `--output`,
serial runs keep pytest's normal capture reporting. Parallel workers display
warnings with stderr unless you pass `--disable-warnings`.

## Scheduling and fixtures

Workers take individual test cases, including parametrised cases, from a shared
queue in collection order. Each worker reserves at most one next case, at the
start of teardown. Faster workers can take more unassigned cases, and the
assignment can change between runs. `--pretty` still displays result marks in
collection order.

Pytest can reuse broader-scoped fixtures when the next case on the same worker
shares them. Module- and session-scoped fixtures are local to each worker, so a
simulator build in a module-scoped fixture can run more than once.

Use pytest's `tmp_path` fixture for per-test cocotb build directories, or use
`tmp_path_factory.mktemp(...)` for a build reused within a worker. Do not have
parallel tests write to the same build or result directory. Wait for any
subprocesses the test starts before it ends so their output reaches the terminal.

## Fresh workers

A test import or plugin can start background threads during collection, including
native threads from NumPy or SciPy. Forking at this point can leave a worker
waiting on a lock from a thread it did not inherit. `--parallel` automatically
starts fresh interpreters instead and prints one notice with the thread count.
It does not change your environment's BLAS thread settings. If workers compete
for CPU time, adjust the worker count or your BLAS thread budget.

Each fresh worker repeats collection before any tests run. This can increase
startup time. Test imports and collection hooks must tolerate running in several
processes, and all workers must collect the same node IDs in the same order.
The run stops before executing tests if that check fails. Fresh workers use
separate caches and temporary directories. The controller writes JUnit XML;
fresh workers stream their logs to the terminal and do not write to the
controller's pytest log file.

Fresh workers replay the original pytest arguments and plugin configuration.
Plugins passed as objects to `pytest.main()` and pytest's `--debug` file are not
supported with this fallback. Use importable plugin names and leave off `--debug`.

## Limitations

Tests that require interactive input, pytest's interactive debugger, or xdist
distribution must run separately. Plugins that depend on all test hooks and
session hooks running in the same process may not work: test execution hooks
run in the workers and the controller replays their reports. In a worker,
`pytest_runtest_protocol` receives `nextitem=None`; `pytest_runtest_teardown`
receives the next assigned item, if any.

When `-x` or `--maxfail` triggers, tests that already started finish and workers
stop taking new items. If a worker crashes, pytest reports the crash and terminates
the other workers.
