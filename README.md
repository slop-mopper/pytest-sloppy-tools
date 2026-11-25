# pytest-sloppy-tools

Helpers and fixtures for pytest workflows, including parallel execution for suites
that launch cocotb simulations.

Install from this checkout in a Python 3.13 environment with `uv sync --no-dev`.
The parallel runner works without cocotb. Add `--extra cocotb` to `uv sync` or
`uv run` to install cocotb if your tests need it. For development, `uv sync`
includes cocotb for the simulator integration test, which also requires Linux
and NVC.

Parallel execution is **Linux only**. To run four workers, choose an output mode:

```sh
uv run --no-dev pytest --workers=4 --output=live --log-cli-level=INFO
uv run --no-dev pytest --workers=4 --output=batched --log-cli-level=INFO
```

Workers take individual test cases from a shared queue. If collection leaves
background threads running, the runner starts fresh interpreters instead of
forking.
Output is labelled with its test ID and stream, including output from
subprocesses that inherit stdout and stderr.
Use `--output` without `--workers` to display test output in a serial run,
or omit both options to keep pytest's normal capture.

Add [`--pretty`](docs/pretty.md) for compact, coloured progress in serial or
parallel runs. See the [parallel execution guide](docs/parallel.md) for logging,
fixtures, and simulator build-directory isolation.
