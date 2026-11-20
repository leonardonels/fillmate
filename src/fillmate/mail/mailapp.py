"""Hand the email to the user's mail app instead of sending it directly."""
import os
import shutil
import subprocess
import sys


def open_draft(eml_path, draft=None, attachments=()):
    """Open a compose window. On Linux xdg-email can prefill attachments; elsewhere the .eml is opened."""
    if draft and sys.platform.startswith("linux") and shutil.which("xdg-email"):
        cmd = ["xdg-email", "--subject", draft["subject"], "--body", draft["body"]]
        for c in draft.get("cc", []):
            cmd += ["--cc", c]
        for b in draft.get("bcc", []):
            cmd += ["--bcc", b]
        for a in attachments:
            cmd += ["--attach", str(a)]
        subprocess.Popen(cmd + list(draft.get("to", [])))
        return
    open_path(eml_path)


def open_path(path):
    path = str(path)
    if sys.platform == "darwin":
        subprocess.Popen(["open", path])
    elif os.name == "nt":
        os.startfile(path)  # noqa: S606 - opening a file the app just wrote
    else:
        subprocess.Popen(["xdg-open", path])
