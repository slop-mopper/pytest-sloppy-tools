# pytest-sloppy-tools

Helpers and fixtures for pytest workflows, including parallel execution for suites
that launch cocotb simulations.

Install from this checkout in a Python 3.13 environment with `uv sync --no-dev`.
The parallel runner works without cocotb. Add `--extra cocotb` to `uv sync` or
`uv run` to install cocotb if your tests need it. For development, `uv sync`
includes cocotb for the simulator integration test, which also requires Linux
and NVC.

Parallel execution is **Linux only** and displays output live by default:

```sh
uv run --no-dev pytest --parallel --log-cli-level=INFO
uv run --no-dev pytest -j4 --output=batched --log-cli-level=INFO
```

Use `--parallel` or `-j` without a count to use half the CPUs available to
pytest, rounded down to at least one worker.

Workers take individual test cases from a shared queue. If collection leaves
background threads running, the runner starts fresh interpreters instead of
forking.
Output is labelled with its test ID and stream, including output from
subprocesses that inherit stdout and stderr.
Use `--output` without `--parallel` to display test output in a serial run,
or omit both options to keep pytest's normal capture.

Add [`--pretty`](docs/pretty.md) for compact, coloured progress in serial or
parallel runs. See the [parallel execution guide](docs/parallel.md) for logging,
fixtures, and simulator build-directory isolation.
