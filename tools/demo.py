#!/usr/bin/env python3
"""Open fillmate with made-up data, separate from your real settings.

Creates (once) a demo home with the example packs, a fictional profile and test mode on,
then launches the app in it. Useful for trying things out and for screenshots.

    python tools/demo.py              # demo home in ./demo-home
    python tools/demo.py --reset      # start again from scratch
"""
import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def setup(home):
    os.environ["FILLMATE_HOME"] = str(home)
    from fillmate import store
    from fillmate.pack import import_pack
    from fillmate.paths import examples_dir

    for folder in sorted(examples_dir().iterdir()):
        if (folder / "pack.yaml").is_file():
            import_pack(folder, store.packs_dir(), replace=True)
    store.save_profile({"full_name": "Alex Example", "employee_id": "E-1024", "department": "Engineering",
                        "manager_email": "manager@example.com"})
    s = store.load_settings()
    s.update(test_mode=True, test_recipients=["tester@example.com"], sender="mail_app")
    store.save_settings(s)
    make_signature(home)


def make_signature(home):
    """A made-up signature for the demo profile: two variants of the same scribble."""
    import tempfile

    import pymupdf

    from fillmate import signatures

    tmp = Path(tempfile.mkdtemp())
    images = []
    for k, lift in enumerate((0, 9)):
        pdf = pymupdf.open()
        page = pdf.new_page(width=260, height=110)
        shape = page.new_shape()
        shape.draw_bezier((30, 70), (60, 10 + lift), (80, 100), (110, 50))
        shape.draw_bezier((110, 50), (140, 20), (150, 90 - lift), (230, 40 + lift))
        shape.finish(color=(0.08, 0.12, 0.42), width=2.2)
        shape.commit()
        path = tmp / f"demo-signature-{k}.png"
        page.get_pixmap(dpi=200).save(path)
        images.append(path)
    signatures.add_images(images, "Alex Example")
    for path in images:
        path.unlink()
    tmp.rmdir()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--home", type=Path, default=ROOT / "demo-home")
    ap.add_argument("--reset", action="store_true")
    args = ap.parse_args()
    home = args.home.resolve()
    if args.reset and home.exists():
        shutil.rmtree(home)
    if not home.exists():
        setup(home)
        print(f"demo home created: {home}")
    env = {**os.environ, "FILLMATE_HOME": str(home)}
    sys.exit(subprocess.call([sys.executable, "-m", "fillmate"], env=env))


if __name__ == "__main__":
    main()
