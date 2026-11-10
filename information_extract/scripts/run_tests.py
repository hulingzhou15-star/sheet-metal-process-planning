"""Run module tests without creating files outside information_extract."""
from pathlib import Path
import os
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
sys.path[:0] = [str(ROOT)]
scratch = ROOT / "outputs/test_tmp"
scratch.mkdir(parents=True, exist_ok=True)
tempfile.tempdir = str(scratch)
os.environ["TEMP"] = os.environ["TMP"] = str(scratch)

if __name__ == "__main__":
    suite = unittest.defaultTestLoader.discover(str(ROOT / "tests"), pattern="test_*.py")
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    raise SystemExit(0 if result.wasSuccessful() else 1)
