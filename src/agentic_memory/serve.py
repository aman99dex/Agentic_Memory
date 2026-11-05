"""Run the REST API: `python -m agentic_memory.serve`, the same as `agentic-memory serve`."""

import sys

from agentic_memory.cli import main

if __name__ == "__main__":
    sys.exit(main(["serve"]))
