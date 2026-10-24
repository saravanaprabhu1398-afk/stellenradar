import sys
from pathlib import Path

# Lambda code is packaged with src/ as the root, so tests import it the same way.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
