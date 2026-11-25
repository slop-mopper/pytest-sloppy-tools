# Pretty progress

Use `--pretty` for compact progress, output labels, and a results summary in
serial and parallel runs:

```sh
uv run --no-dev pytest --pretty
uv run --no-dev pytest --pretty --output=batched
uv run --no-dev pytest --pretty --parallel=4
```

Each row uses the function name, with a module or class qualifier if names
collide. Parametrised cases share a row and their marks follow collection order,
even when workers finish out of order. The percentage counts completed items
across the whole run, including teardown. Percentages share a right-aligned
column with 12 spaces between the longest possible row and the percentage field.

| Mark | Outcome | Colour |
| --- | --- | --- |
| `·` | Passed | Green |
| `×` | Failed, including setup or teardown errors | Red |
| `s` | Skipped | Yellow |
| `x` / `X` | Expected failure / unexpected pass | Yellow |

On a terminal, a quiet case updates its row in place when it fits. Test output
starts on its own line; when the row continues, marks on earlier lines are dimmed
and new marks keep their colours. Both serial and parallel runs honour
`--output=live` and `--output=batched`. Parallel runs default to live output.
Without an explicit mode, serial runs show captured output after each test
and display uncaptured output live, including pytest's live logs. Enabling
live output does not split the row for quiet cases.

Output headers show the stream and case name between pairs of em dashes.
A dim `—— end <stream> ——` footer closes each block. Consecutive live output
from the same case and stream shares a block. Parameter labels distinguish
named keys from values and de-emphasise separators in pytest's hyphenated IDs.
For example:

```text
—— stderr │ test_counter (width=32; reset=async) ——
Simulation failed: exit code 1
—— end stderr ——
```

The summary lists failed cases with a short reason for each. A blank line
separates the list from the outcome counts and elapsed time. Setup and teardown
errors count as failures of the affected case, even if its test body passed.
Pytest's default tracebacks and repeated capture sections are hidden.
Use `--tb=short --show-capture=no` to restore
tracebacks without repeating captured output, or `--tb=long` for more detail.
`--no-summary` hides the failed-case list while keeping the outcome counts.

Redirected output uses plain lines without cursor movement. Quiet cases are
grouped on one line per function when progress doesn't need to make room for
output. Colours follow pytest's `--color` setting. `--pretty` does not support
xdist distribution.
