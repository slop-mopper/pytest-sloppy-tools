# pytest-sloppy-tools

Helpers and fixtures for pytest workflows, including parallel execution for suites
that launch cocotb simulations.

Install from this checkout in a Python 3.13 environment with `uv sync`.
Parallel execution is **Linux only**. To run four workers, choose an output mode:

```sh
uv run pytest --sloppy-workers=4 --sloppy-output=live --log-cli-level=INFO
uv run pytest --sloppy-workers=4 --sloppy-output=batched --log-cli-level=INFO
```

Each parametrised case is scheduled separately. Output is labelled with its test
ID and stream, including output from subprocesses that inherit stdout and stderr.
Omit both options to run pytest normally.

See the [parallel execution guide](docs/parallel.md) for logging, fixtures, and
simulator build-directory isolation.
