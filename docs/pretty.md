# Pretty progress

Use `--pretty` to replace pytest's progress display in serial and parallel runs:

```sh
uv run --no-dev pytest --pretty
uv run --no-dev pytest --pretty --output=batched
uv run --no-dev pytest --pretty --workers=4 --output=live
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
`--output=live` and `--output=batched`. Without an explicit mode, serial runs
show captured output after each test and display uncaptured output live,
including pytest's live logs. Enabling live output does not split the row for
quiet cases.

Redirected output uses plain lines without cursor movement. Quiet cases are
grouped on one line per function when progress doesn't need to make room for
output. Colours follow pytest's `--color` setting. Pytest still prints its
regular tracebacks and summary. Use `--show-capture=no` to avoid repeating
captured output in failure details. `--pretty` does not support xdist distribution.
