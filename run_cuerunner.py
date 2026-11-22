"""Entry point used for development and by PyInstaller."""

import sys

from cuerunner.main import run

if __name__ == "__main__":
    sys.exit(run())
