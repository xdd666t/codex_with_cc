#!/usr/bin/env python3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from runtime import main

if __name__ == "__main__":
    raise SystemExit(main(["test-runtime", *sys.argv[1:]]))
