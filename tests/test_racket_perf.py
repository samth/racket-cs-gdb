"""Unit tests plus opt-in real Racket/perf integration tests.

Run integrations under rackup with RACKET_PERF_TEST_RACKET set to the
toolchain's real executable. They require Linux ptrace and perf permissions.
"""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import chez_perf_map


class MapNames(unittest.TestCase):
    def test_short_names_and_unique_names(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "map"
            entries = [(0x1000, 16, "p"), (0x2000, 16, "#f"), (0x3000, 16, "fib")]
            chez_perf_map.write_perf_map(path, entries)
            self.assertEqual(path.read_text(), "1000 10 [p]\n2000 10 [#f]\n3000 10 fib\n")
            chez_perf_map.write_perf_map(path, entries, unique=True)
            self.assertIn("p [0x1000]", path.read_text())


@unittest.skipUnless(os.environ.get("RACKET_PERF_TEST_RACKET"), "set RACKET_PERF_TEST_RACKET for live tests")
class Recording(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="racket perf test ")
        self.addCleanup(self.directory.cleanup)
        self.directory_path = Path(self.directory.name)
        self.output = self.directory_path / "profile"
        self.command = [str(ROOT / "racket-perf" / "bin" / "racket-perf"), "--racket", os.environ["RACKET_PERF_TEST_RACKET"],
                        "-o", str(self.output), "--rows", "5"]

    def run_profile(self, arguments, code=0):
        result = subprocess.run(self.command + arguments, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, code, result.stdout + result.stderr)
        metadata = json.loads((self.output / "run.json").read_text())
        self.assertFalse(metadata.get("error"), metadata)
        self.assertGreater(metadata["code_objects"], 0)
        self.assertIsNone(metadata["map_error"])
        for filename in [metadata["map"], "perf.data", "report.txt", "perf.log"]:
            self.assertGreater((self.output / filename).stat().st_size, 0)
        return result, metadata

    def test_hotspot_module_main_and_arguments(self):
        source = self.directory_path / "work load.rkt"
        source.write_text('''#lang racket/base
(define (go n) (if (< n 2) n (+ (go (- n 1)) (go (- n 2)))))
(module+ main
  (displayln (vector->list (current-command-line-arguments)))
  (for ([i (in-range 1500)]) (go 25)))
''')
        result, metadata = self.run_profile([str(source), "two words", "--unique"])
        self.assertIn("(two words --unique)", result.stdout)
        self.assertIn("[go]", (self.output / "report.txt").read_text())
        self.assertFalse(metadata["unique"])

    def test_short_run_and_nonzero_exit(self):
        _, metadata = self.run_profile(["--", "-e", "(exit 7)"], code=7)
        self.assertEqual(metadata["exit_code"], 7)

    def test_duration_and_unique_map(self):
        _, metadata = self.run_profile(["--duration", "0.4", "--unique", "--", "-e", "(let loop () (loop))"])
        self.assertTrue(metadata["duration_reached"])
        self.assertTrue(metadata["unique"])
        self.assertIn("[0x", (self.output / metadata["map"]).read_text())

    def test_default_rackup_environment(self):
        expression = "(displayln (find-system-path 'addon-dir))"
        expected = subprocess.check_output(["rackup", "run", os.environ["RACKUP_TOOLCHAIN"],
                                            "--", "racket", "-e", expression], text=True).strip()
        self.command = [str(ROOT / "racket-perf" / "bin" / "racket-perf"), "-o", str(self.output), "--rows", "1"]
        result, metadata = self.run_profile(["--", "-e", expression])
        self.assertIn(expected, result.stdout)
        self.assertEqual(metadata["toolchain"], os.environ["RACKUP_TOOLCHAIN"])

    def test_invalid_event_cleans_up_child(self):
        result = subprocess.run(self.command + ["-e", "not-a-perf-event", "--", "-e", "(sleep 30)"],
                                capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 1)
        metadata = json.loads((self.output / "run.json").read_text())
        self.assertTrue(metadata["error"])
        self.assertFalse(Path("/proc/%d" % metadata["pid"]).exists())

    def test_ctrl_c_saves_report(self):
        process = subprocess.Popen(self.command + ["--", "-e", "(sleep 30)"],
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                   start_new_session=getattr(self, "interrupt_group", False))
        try:
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                log = self.output / "perf.log"
                if log.exists() and "Events enabled" in log.read_text():
                    break
                time.sleep(0.02)
            # Give Racket time to finish booting before the interruption.
            time.sleep(0.4)
            if getattr(self, "interrupt_group", False):
                os.killpg(process.pid, signal.SIGINT)
            else:
                process.send_signal(signal.SIGINT)
            stdout, stderr = process.communicate(timeout=15)
            self.assertNotEqual(process.returncode, 0, stdout + stderr)
            metadata = json.loads((self.output / "run.json").read_text())
            self.assertIsNone(metadata["map_error"])
            self.assertTrue((self.output / "report.txt").exists())
            self.assertFalse(Path("/proc/%d" % metadata["pid"]).exists())
        finally:
            if process.poll() is None:
                if getattr(self, "interrupt_group", False):
                    os.killpg(process.pid, signal.SIGKILL)
                else:
                    process.kill()
                process.communicate()


@unittest.skipUnless(os.environ.get("RACKET_PERF_TEST_RACO"), "set RACKET_PERF_TEST_RACO for installed command tests")
class RacoRecording(Recording):
    def setUp(self):
        super().setUp()
        self.raco = os.environ["RACKET_PERF_TEST_RACO"]
        self.command = [self.raco, "racket-perf", "-o", str(self.output), "--rows", "5"]

    def test_default_rackup_environment(self):
        expression = "(displayln (find-system-path 'addon-dir))"
        expected = subprocess.check_output([os.environ["RACKET_PERF_TEST_RACKET"], "-e", expression],
                                            text=True).strip()
        result, metadata = self.run_profile(["--", "-e", expression])
        self.assertIn(expected, result.stdout)
        self.assertEqual(metadata["command"][0], os.path.realpath(os.environ["RACKET_PERF_TEST_RACKET"]))

    def test_does_not_invoke_python(self):
        blocked = self.directory_path / "blocked python"
        blocked.mkdir()
        marker = blocked / "invoked"
        for name in ("python", "python3"):
            script = blocked / name
            script.write_text("#!/bin/sh\n: > '" + str(marker) + "'\nexit 99\n")
            script.chmod(0o755)
        environment = dict(os.environ, PATH=str(blocked) + os.pathsep + os.environ["PATH"])
        for index, command in enumerate(([self.raco, "racket-perf"], [str(ROOT / "racket-perf" / "bin" / "racket-perf")])):
            output = self.directory_path / ("no python %d" % index)
            result = subprocess.run(command + ["-o", str(output), "--", "-e", "(void)"],
                                    env=environment, capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            metadata = json.loads((output / "run.json").read_text())
            self.assertGreater(metadata["code_objects"], 0)
            self.assertFalse(marker.exists(), "profiling invoked Python")

    def test_help_registration(self):
        for arguments in (["racket-perf", "--help"], ["help", "racket-perf"]):
            result = subprocess.run([self.raco] + arguments, capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("usage: raco racket-perf", result.stdout)

    def test_terminal_ctrl_c_through_rackup_shim(self):
        self.command[0] = "raco"
        self.interrupt_group = True
        self.test_ctrl_c_saves_report()


if __name__ == "__main__":
    unittest.main()
