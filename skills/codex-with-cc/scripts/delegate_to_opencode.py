#!/usr/bin/env python3
import sys
from codex_with_cc_runtime.process_cleanup import install_child_process_cleanup
from runtime import main

if __name__ == "__main__":
    install_child_process_cleanup()
    raise SystemExit(main(["opencode", "delegate_task", *sys.argv[1:]]))
