PR: Reliability & CI Improvements

This small file is added to create a visible diff between the `btc` branch and `main` so a Pull Request can be opened.

Summary of the work on branch `btc`:

- Centralized HTTP helper with retries (utils/http.py)
- Standardized logging configuration (utils/logging_config.py)
- Enabled SQLite WAL and added defensive DB error handling
- Replaced prints with logging across core modules
- Fixed syntax issues and improved robustness in brain modules
- Added GitHub Actions CI workflow to run pytest
- Added minimal requirements.txt and basic unit tests

Please review the changes in `btc` and merge into `main` when ready.
