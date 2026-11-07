import sys
from pathlib import Path

# Run the tests against the source tree, installed or not.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
