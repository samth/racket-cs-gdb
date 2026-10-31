"""Integration tests for chez_gdb.py's unwinder, on x86_64 or arm64 Linux.

Set CHEZ_GDB_TEST_SCHEME to a Chez Scheme executable (with its boot files
where it finds them) and CHEZ_GDB_TEST_EQUATES to the equates.h of the
same build (boot/ta6le/equates.h or boot/tarm64le/equates.h). Needs gdb, a
C compiler, and ptrace permission. Set CHEZ_GDB_TEST_RACKET to a Racket CS
executable to also check a Racket backtrace (its layout is found as usual,
or from CHEZ_GDB_TEST_RACKET_EQUATES).
"""
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
HERE = Path(__file__).resolve().parent / "gdb"


def gdb_bt(args, commands, layout=None):
    script = ["set pagination off", "set confirm off", "set breakpoint pending on",
              "source %s" % (ROOT / "chez_gdb.py")]
    if layout:
        script.append("chez-layout %s" % layout)
    script += commands
    with tempfile.NamedTemporaryFile("w", suffix=".gdb", delete=False) as f:
        f.write("\n".join(script) + "\n")
        path = f.name
    try:
        out = subprocess.run(["gdb", "-q", "-batch", "-x", path, "--args"] + args,
                             capture_output=True, text=True, timeout=600)
    finally:
        os.unlink(path)
    return out.stdout + out.stderr


def frame_names(output):
    """Function names of `bt` lines, newest first."""
    names = []
    for line in output.splitlines():
        m = re.match(r"#\d+\s+(?:0x[0-9a-f]+ in )?(\[scheme\] )?(\S+)", line)
        if m:
            names.append(m.group(2))
    return names


def in_order(names, wanted):
    i = 0
    for n in names:
        if i < len(wanted) and n == wanted[i]:
            i += 1
    return i == len(wanted)


@unittest.skipUnless(os.environ.get("CHEZ_GDB_TEST_SCHEME") and shutil.which("gdb"),
                     "set CHEZ_GDB_TEST_SCHEME (and CHEZ_GDB_TEST_EQUATES)")
class ChezBacktrace(unittest.TestCase):
    def test_callback(self):
        with tempfile.TemporaryDirectory() as d:
            lib = Path(d) / "callback.so"
            subprocess.run(["cc", "-shared", "-fPIC", "-O1", "-o", str(lib),
                            str(HERE / "callback.c")], check=True)
            out = gdb_bt([os.environ["CHEZ_GDB_TEST_SCHEME"], "--script",
                          str(HERE / "callback.ss"), str(lib)],
                         ["break stop_here", "run", "bt 200", "chez-stack 20"],
                         os.environ.get("CHEZ_GDB_TEST_EQUATES"))
        names = frame_names(out)
        self.assertTrue(in_order(names, ["stop_here", "inner", "in-callback", "S_call_help",
                                         "<foreign-callable", "call_it", "outer", "S_call_help",
                                         "main"]),
                        out)
        self.assertEqual(names.count("inner"), 5, out)  # the last call is a tail call


@unittest.skipUnless(os.environ.get("CHEZ_GDB_TEST_RACKET") and shutil.which("gdb"),
                     "set CHEZ_GDB_TEST_RACKET")
class RacketBacktrace(unittest.TestCase):
    def test_collect(self):
        out = gdb_bt([os.environ["CHEZ_GDB_TEST_RACKET"], "-n", "-e", "(collect-garbage)"],
                     ["break S_gc", "run", "bt 200", "chez-where"],
                     os.environ.get("CHEZ_GDB_TEST_RACKET_EQUATES"))
        names = frame_names(out)
        self.assertTrue(in_order(names, ["S_gc", "collect/report", "S_call_help", "racket_boot"]), out)


if __name__ == "__main__":
    unittest.main()
