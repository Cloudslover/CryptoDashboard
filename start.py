"""
Start script that loads .env and runs main.py in the same process so environment variables
are available to the application without modifying the existing main.py file.

Usage:
  python start.py

This file is intentionally small and safe: it uses python-dotenv to load .env from the
project root (if present) and then executes main.py with runpy.run_path so main.py runs
in the same interpreter and can read os.environ values.
"""
from dotenv import load_dotenv
import runpy
import os
from pathlib import Path

# Load environment variables from .env in project root (if present)
load_dotenv()

# Optional: show a friendly message if FRED_API_KEY not set
if not os.getenv("FRED_API_KEY"):
    print("Warning: FRED_API_KEY is not set. Some macro features may not work.")

# Execute main.py if it exists in the repository root
main_path = Path("main.py")
if main_path.exists():
    runpy.run_path(str(main_path), run_name="__main__")
else:
    print("main.py not found in repository root. Please run your project's entrypoint manually.")
