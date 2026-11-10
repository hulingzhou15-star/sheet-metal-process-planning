"""Start the local editor using the self-contained migrated parser."""
from pathlib import Path
import argparse
import os
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
sys.path[:0] = [str(ROOT)]
scratch = ROOT.parent / ".local/annotation-tmp"
scratch.mkdir(parents=True, exist_ok=True)
tempfile.tempdir = str(scratch)
os.environ["TEMP"] = os.environ["TMP"] = str(scratch)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="二维加工动作标注工具")
    parser.add_argument("--port", type=int)
    parser.add_argument("--config", default="configs/app.json", help="本模块内的零件配置文件")
    args = parser.parse_args()
    from extract_app.server import run
    run(ROOT, args.port, args.config)
