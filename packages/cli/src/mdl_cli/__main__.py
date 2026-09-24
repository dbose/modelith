"""Module entry point so `python -m mdl_cli` runs the CLI.

This is the last-resort invocation the VS Code extension falls back to on
locked-down Windows/macOS boxes where the `mdl` / `mdl.exe` console script is
installed but not on the GUI process's PATH (e.g. a `pip install --user` whose
`%APPDATA%\\Python\\Python3xx\\Scripts` dir was never added to PATH). Given ANY
interpreter that can import `mdl_cli`, `python -m mdl_cli` reaches the same
`main()` the console script wraps, so no PATH surgery is required.
"""

from mdl_cli.main import main

if __name__ == "__main__":
    main()
