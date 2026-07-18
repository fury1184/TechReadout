"""Ensure the repository root is importable so `import app...` works under pytest
regardless of the directory pytest is invoked from."""
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
