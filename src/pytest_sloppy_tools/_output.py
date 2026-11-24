# _output.py
# SPDX-License-Identifier: CC0-1.0
# This file was created by an AI model: openai/gpt-6.2-sol

"""Read per-test stdout and stderr for serial and worker output."""

import codecs
import os
from collections.abc import Callable
from pathlib import Path
from typing import BinaryIO


_STREAMS = ("stdout", "stderr")
_CHUNK_SIZE = 64 * 1024


def _stream_path(directory: Path, index: int, stream: str) -> Path:
	return directory / f"{index}.{stream}"


class _Output:
	def __init__(self, directory: Path, index: int) -> None:
		self.index = index
		self.paths = [_stream_path(directory, index, stream) for stream in _STREAMS]
		self.files: list[BinaryIO] = []
		self.decoders = [codecs.getincrementaldecoder("utf-8")(errors="replace") for _ in _STREAMS]
		try:
			for path in self.paths:
				self.files.append(path.open("rb"))
		except BaseException:
			self.close()
			raise

	def show(self, heading: Callable[[int, str], None], write: Callable[[str], None], *, final: bool) -> None:
		for stream, file, decoder in zip(_STREAMS, self.files, self.decoders, strict=True):
			size = os.fstat(file.fileno()).st_size - file.tell()
			if not final:
				size = min(size, _CHUNK_SIZE)
			last = ""
			while size:
				data = file.read(min(size, _CHUNK_SIZE))
				if not data:
					break
				size -= len(data)
				if text := decoder.decode(data):
					if not last:
						heading(self.index, stream)
					write(text)
					last = text[-1]
			if final and (tail := decoder.decode(b"", final=True)):
				if not last:
					heading(self.index, stream)
				write(tail)
				last = tail[-1]
			if last and last != "\n":
				write("\n")

	def close(self) -> None:
		for file in self.files:
			file.close()
		for path in self.paths:
			path.unlink(missing_ok=True)
