# test_distribution.py
# SPDX-License-Identifier: CC0-1.0
# This file was created by an AI model: openai/gpt-6.2-sol

"""Validate the source distribution and wheel before publishing."""

import argparse
import re
import tarfile
import tomllib
from configparser import ConfigParser
from email.parser import BytesParser
from pathlib import Path
from zipfile import BadZipFile, ZipFile


def _validate(dist: Path) -> None:
	with Path("pyproject.toml").open("rb") as file:
		project = tomllib.load(file)["project"]
	name, version = project["name"], project["version"]
	stem = f"{re.sub(r'[-_.]+', '_', name).lower()}-{version}"
	wheels = sorted(dist.glob("*.whl"))
	sdists = sorted(dist.glob("*.tar.gz"))
	if len(wheels) != 1 or len(sdists) != 1:
		raise ValueError("expected one wheel and one source distribution")
	wheel, sdist = wheels[0], sdists[0]
	if not wheel.name.startswith(f"{stem}-") or sdist.name != f"{stem}.tar.gz":
		raise ValueError(f"expected distribution filenames for {stem}")

	info = f"{stem}.dist-info"
	with ZipFile(wheel) as archive:
		wheel_metadata = archive.read(f"{info}/METADATA")
		entry_points = ConfigParser(interpolation=None)
		entry_points.read_string(archive.read(f"{info}/entry_points.txt").decode("utf-8"))
		for plugin, target in project["entry-points"]["pytest11"].items():
			if entry_points.get("pytest11", plugin, fallback=None) != target:
				raise ValueError(f"wheel is missing the pytest entry point {plugin}")
			module = target.split(":", 1)[0].replace(".", "/")
			if f"{module}.py" not in archive.namelist():
				raise ValueError(f"wheel is missing the pytest plugin module {module}")

	with tarfile.open(sdist, "r:gz") as archive:
		member = archive.getmember(f"{stem}/PKG-INFO")
		if not member.isfile() or (file := archive.extractfile(member)) is None:
			raise ValueError("invalid source distribution metadata")
		with file:
			sdist_metadata = file.read()

	for path, metadata in ((wheel, wheel_metadata), (sdist, sdist_metadata)):
		headers = BytesParser().parsebytes(metadata, headersonly=True)
		if headers.get_all("Name") != [name] or headers.get_all("Version") != [version]:
			raise ValueError(f"unexpected package name or version in {path.name}")


def _main() -> None:
	parser = argparse.ArgumentParser(description=__doc__)
	parser.add_argument("--dist", required=True, type=Path, help="directory containing the built distributions")
	args = parser.parse_args()
	try:
		_validate(args.dist)
	except (ValueError, OSError, KeyError, BadZipFile, tarfile.TarError) as exc:
		parser.error(str(exc))
	print(f"Validated wheel and source distribution in {args.dist}")


if __name__ == "__main__":
	_main()
