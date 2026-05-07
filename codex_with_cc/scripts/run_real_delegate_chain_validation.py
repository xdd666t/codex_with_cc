#!/usr/bin/env python3
import sys
from runtime import main

if __name__ == "__main__":
    raise SystemExit(main(["run-real-chain-validation", *sys.argv[1:]]))
