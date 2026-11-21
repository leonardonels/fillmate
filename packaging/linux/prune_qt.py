"""Remove the parts of PySide6 that fillmate never loads, to shrink the .deb.

Run with the bundled Python on the build machine (it needs `ldd`):
    python3 prune_qt.py <site-packages>

Kept: the Python modules fillmate imports (MODULES), the Qt libraries they link to,
and the plugins a desktop app loads (windowing on X11 and Wayland, images, icons,
input methods). Everything else (QML, Designer, SQL drivers, tools, docs, stubs) goes.
"""
import re
import shutil
import subprocess
import sys
from pathlib import Path

MODULES = ["QtCore", "QtGui", "QtWidgets", "QtSvg"]  # every PySide6 module fillmate imports

PLUGIN_DIRS = [
    "platforms", "platformthemes", "platforminputcontexts", "xcbglintegrations",
    "imageformats", "iconengines", "wayland-decoration-client",
    "wayland-graphics-integration-client", "wayland-shell-integration",
]
# Inside the kept plugin dirs: plugins that pull in modules fillmate doesn't use.
DROP_PLUGINS = ["libqgtk3.so", "libqtvirtualkeyboardplugin.so", "libqpdf.so"]
TOOLS = ["assistant", "designer", "linguist", "lrelease", "lupdate", "qmlformat",
         "qmllint", "qmlls", "svgtoqml", "doc", "include", "glue", "typesystems", "scripts"]


def linked_libs(path):
    out = subprocess.run(["ldd", str(path)], capture_output=True, text=True).stdout
    return {Path(m).resolve() for m in re.findall(r"=> (/\S+)", out)}


def main(site):
    pyside = Path(site) / "PySide6"
    qt = pyside / "Qt"
    plugins = qt / "plugins"
    before = sum(f.stat().st_size for f in pyside.rglob("*") if f.is_file())

    for d in plugins.iterdir():
        if d.name not in PLUGIN_DIRS:
            shutil.rmtree(d)
    for name in DROP_PLUGINS:
        for f in plugins.rglob(name):
            f.unlink()

    keep_modules = {f"{m}.abi3.so" for m in MODULES}
    for f in pyside.glob("Qt*.abi3.so"):
        if f.name not in keep_modules:
            f.unlink()
    for f in pyside.glob("*.pyi"):
        f.unlink()
    for name in TOOLS:
        p = pyside / name
        if p.is_dir():
            shutil.rmtree(p)
        elif p.exists():
            p.unlink()
    for name in ["qml", "libexec", "metatypes", "modules"]:
        shutil.rmtree(qt / name, ignore_errors=True)
    for f in (qt / "translations").glob("*.qm"):
        if not f.name.startswith("qtbase_"):
            f.unlink()

    # Every library the kept modules and plugins reach, followed through Qt/lib.
    qt_lib = (qt / "lib").resolve()
    roots = [f for f in pyside.glob("*.so*") if f.is_file()] + list(plugins.rglob("*.so"))
    needed, todo = set(), list(roots)
    while todo:
        for lib in linked_libs(todo.pop()):
            if lib.parent == qt_lib and lib not in needed:
                needed.add(lib)
                todo.append(lib)
    for f in qt_lib.iterdir():
        if f.resolve() not in needed and (f.suffix == ".so" or ".so." in f.name):
            f.unlink()
    # libpyside6qml is only for QtQml, which is gone.
    for f in pyside.glob("libpyside6qml*"):
        if f.resolve() not in {l.resolve() for r in pyside.glob("Qt*.abi3.so") for l in linked_libs(r)}:
            f.unlink()

    after = sum(f.stat().st_size for f in pyside.rglob("*") if f.is_file())
    print(f"PySide6: {before / 1e6:.0f} MB -> {after / 1e6:.0f} MB")


if __name__ == "__main__":
    main(sys.argv[1])
