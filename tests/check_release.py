# check_release.py
# SPDX-License-Identifier: CC0-1.0
# This file was created by an AI model: openai/gpt-6.2-sol

"""Check the release ref and emit the publishing destination for GitHub Actions."""

import argparse
import tomllib
from pathlib import Path


def _main() -> None:
	parser = argparse.ArgumentParser(description=__doc__)
	parser.add_argument("ref", help="GitHub ref that triggered the workflow")
	parser.add_argument("--event", required=True, choices=("push", "workflow_dispatch"))
	parser.add_argument("--destination", default="", choices=("", "pypi", "test-pypi"))
	args = parser.parse_args()

	with Path("pyproject.toml").open("rb") as file:
		version = tomllib.load(file)["project"]["version"]
	expected = f"refs/tags/v{version}"
	if args.ref != expected:
		parser.error(f"release requires {expected}; got {args.ref}")

	if args.event == "push":
		destination = "pypi"
	elif args.destination:
		destination = args.destination
	else:
		parser.error("a manual release requires a destination")
	print(f"destination={destination}")


if __name__ == "__main__":
	_main()
