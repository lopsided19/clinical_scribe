from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))

from src.runner import run


if __name__ == "__main__":
    raise SystemExit(run(Path(__file__).resolve().parent))
