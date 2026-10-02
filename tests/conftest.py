"""Test-wide setup.

The display app and the scripts keep runtime state (stats.json, text_log.json,
sessions, the active campaign…) in <data-root>/.runtime. Tests that load the
display app post stats to it, so without this they overwrite the live
display's party with test fixtures. Point every test at a throwaway runtime
dir before any module resolves the path.
"""
import os
import tempfile

os.environ["DND_RUNTIME_DIR"] = tempfile.mkdtemp(prefix="dnd-test-runtime-")
