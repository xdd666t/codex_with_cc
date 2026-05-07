#!/usr/bin/env python3
import sys
from runtime import main

if __name__ == "__main__":
    raise SystemExit(main(["verify-artifacts", *sys.argv[1:]]))
