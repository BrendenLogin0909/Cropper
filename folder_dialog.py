"""Native folder picker. Run as a one-shot process so Tk never lives in the server.

The Cropper HTTP server is ThreadingHTTPServer: /api/pick-folder, /api/folder-status
and /api/settings all run on worker threads. Tcl 8.6 (tcl86t.dll) crashes with
Tcl_AsyncDelete if Tk is created or destroyed off the process main thread, or if a
Tk root is garbage-collected on a later request thread. This script is the only
place Cropper imports tkinter. The server launches it per dialog and never imports
this module.
"""

from __future__ import annotations

import json
import sys


def _emit(payload: dict) -> None:
    blob = json.dumps(payload, ensure_ascii=False).encode("utf-8", "surrogatepass")
    sys.stdout.buffer.write(blob)
    sys.stdout.buffer.flush()


def _open_dialog() -> str:
    import tkinter as tk
    from tkinter import filedialog

    root = tk.Tk()
    root.withdraw()
    try:
        try:
            root.attributes("-topmost", True)
        except tk.TclError:
            pass
        chosen = filedialog.askdirectory(title="Choose folder")
    finally:
        try:
            root.destroy()
        except tk.TclError:
            pass
    return chosen or ""


def _self_test() -> str:
    """Create and destroy a hidden Tk root on this process's main thread."""
    import tkinter as tk

    root = tk.Tk()
    root.withdraw()
    try:
        root.update_idletasks()
    finally:
        try:
            root.destroy()
        except tk.TclError:
            pass
    return "ok"


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else list(argv)
    try:
        if args == ["--self-test"]:
            _self_test()
            _emit({"ok": True})
        elif args:
            sys.stderr.buffer.write(b"usage: folder_dialog.py [--self-test]\n")
            return 2
        else:
            chosen = _open_dialog()
            if chosen:
                _emit({"path": chosen})
            else:
                _emit({"cancelled": True})
    except Exception as exc:
        sys.stderr.buffer.write(str(exc).encode("utf-8", "surrogatepass"))
        sys.stderr.buffer.flush()
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
