# test_cocotb_parallel.py
# SPDX-License-Identifier: CC0-1.0
# This file was created by an AI model: openai/gpt-6.2-sol

import shutil
import subprocess
import sys
from pathlib import Path
from textwrap import dedent

import pytest


@pytest.mark.skipif(sys.platform != "linux" or shutil.which("nvc") is None, reason="requires Linux and NVC")
def test_parallel_nvc_runs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
	(tmp_path / "top.vhd").write_text(
		dedent("""\
		entity top is
		    generic (case_number : integer := 0);
		    port (value : out integer);
		end entity;

		architecture rtl of top is
		begin
		    value <= case_number;
		end architecture;
		"""),
		encoding="utf-8",
	)
	(tmp_path / "sim_check.py").write_text(
		dedent("""\
		import os

		import cocotb
		from cocotb.triggers import Timer

		@cocotb.test()
		async def check_value(dut):
		    await Timer(1, unit="ns")
		    assert int(dut.value.value) == int(os.environ["EXPECTED_CASE"])
		    dut._log.info("SIMULATOR_CASE=%d", int(dut.value.value))
		"""),
		encoding="utf-8",
	)
	(tmp_path / "test_run.py").write_text(
		dedent("""\
		import pytest
		from cocotb_tools.runner import get_runner

		@pytest.mark.parametrize("case", [11, 22])
		def test_sim(case, tmp_path):
		    runner = get_runner("nvc")
		    runner.build(sources=["top.vhd"], hdl_toplevel="top", build_dir=tmp_path)
		    runner.test(
		        test_module="sim_check",
		        hdl_toplevel="top",
		        parameters={"case_number": case},
		        extra_env={"EXPECTED_CASE": str(case)},
		    )
		"""),
		encoding="utf-8",
	)
	monkeypatch.setenv("PYTEST_DISABLE_PLUGIN_AUTOLOAD", "1")
	monkeypatch.setenv("PYTEST_ADDOPTS", "")
	monkeypatch.setenv("PYTEST_PLUGINS", "")
	result = subprocess.run(
		[sys.executable, "-m", "pytest", "-p", "sloppy_tools", "-q", "--workers=2", "--output=batched"],
		cwd=tmp_path,
		capture_output=True,
		text=True,
		timeout=30,
		check=False,
	)
	assert result.returncode == 0, result.stdout + result.stderr
	assert "2 passed" in result.stdout
	for case in (11, 22):
		assert f"test_run.py::test_sim[{case}] [stdout]" in result.stdout
		assert f"SIMULATOR_CASE={case}" in result.stdout
