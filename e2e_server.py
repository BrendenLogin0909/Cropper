"""Test-only server entry. Monkeypatches the folder dialog; not used in production."""

import os
from pathlib import Path

import server


def _stub_dialog():
    stub = os.environ.get("CROPPER_TEST_DIALOG_FOLDER", "").strip()
    if not stub:
        return None
    return None if stub == "-" else Path(stub)


if __name__ == "__main__":
    if os.environ.get("CROPPER_TEST_DIALOG_FOLDER", "").strip():
        server.choose_folder_dialog = _stub_dialog
    server.main()
