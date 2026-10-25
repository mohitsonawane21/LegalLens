"""Tiny dependency-free runner:  python tests/run_all.py   (the tests are also plain pytest tests)."""
import importlib
import sys
import time
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

failed = passed = 0
for f in sorted(HERE.glob("test_*.py")):
    mod = importlib.import_module(f.stem)
    for name in sorted(n for n in dir(mod) if n.startswith("test_")):
        t = time.time()
        try:
            getattr(mod, name)()
            passed += 1
            print(f"PASS  {f.stem}::{name}  ({time.time() - t:.1f}s)")
        except Exception:
            failed += 1
            print(f"FAIL  {f.stem}::{name}")
            traceback.print_exc()
print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
