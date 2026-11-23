"""Package the Briefcase Windows app as an unsigned .msix for the Microsoft Store.

Run on Windows after `briefcase build windows app`. The Store signs the package on upload,
so no certificate is needed (an unsigned .msix can't be installed directly, though).

    python packaging/windows/build_msix.py
"""
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
ICON = ROOT / "packaging" / "icons" / "fillmate-512.png"
# Store logo sizes: StoreLogo 50, Square44x44Logo 44, Square150x150Logo 150.
ASSETS = {"StoreLogo.png": 50, "Square44x44Logo.png": 44, "Square150x150Logo.png": 150}


def msix_version(version):
    # MSIX wants four numeric parts, and the Store requires the last one to be 0.
    parts = [int(p) for p in version.split(".")[:3]]
    parts += [0] * (3 - len(parts))
    return ".".join(map(str, parts + [0]))


def find_makeappx():
    found = shutil.which("makeappx")
    if found:
        return found
    kits = Path(r"C:\Program Files (x86)\Windows Kits\10\bin")
    candidates = sorted(kits.glob(r"10.*\x64\makeappx.exe"))
    if not candidates:
        sys.exit("makeappx.exe not found: install the Windows 10/11 SDK")
    return str(candidates[-1])


def main():
    config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    version = config["tool"]["briefcase"]["version"]

    exes = list((ROOT / "build" / "fillmate" / "windows" / "app").rglob("fillmate.exe"))
    if len(exes) != 1:
        sys.exit(f"expected one fillmate.exe in the Briefcase build, found {exes}")
    app_dir = exes[0].parent

    manifest = (HERE / "AppxManifest.xml").read_text(encoding="utf-8")
    if "TODO-" in manifest:
        sys.exit("packaging/windows/AppxManifest.xml still has TODO values: "
                 "copy them from Partner Center → Product management → Product identity")
    manifest = manifest.replace("@VERSION@", msix_version(version)).replace("@EXECUTABLE@", exes[0].name)

    stage = ROOT / "build" / "msix"
    shutil.rmtree(stage, ignore_errors=True)
    shutil.copytree(app_dir, stage)
    (stage / "AppxManifest.xml").write_text(manifest, encoding="utf-8")
    assets = stage / "Assets"
    assets.mkdir()
    with Image.open(ICON) as icon:
        for name, size in ASSETS.items():
            icon.resize((size, size), Image.LANCZOS).save(assets / name)

    out = ROOT / "dist" / f"fillmate-{version}.msix"
    out.parent.mkdir(exist_ok=True)
    subprocess.run([find_makeappx(), "pack", "/d", str(stage), "/p", str(out), "/o"], check=True)
    print(out)


if __name__ == "__main__":
    main()
