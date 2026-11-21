"""Print the RPM `Requires:` for a self-contained bundle.

    python3 system_libs.py <bundle dir>

Reads every ELF file in the bundle with `objdump -p`, collects the shared libraries
they link to, drops the ones the bundle ships itself, and prints one RPM capability
per line (`libxcb-cursor.so.0()(64bit)`), plus the newest glibc symbol version used,
so the package installs on any distro that has those libraries.
"""
import re
import subprocess
import sys
from pathlib import Path


def elf_files(root):
    for path in root.rglob("*"):
        if path.is_file() and not path.is_symlink():
            with path.open("rb") as f:
                if f.read(4) == b"\x7fELF":
                    yield path


def main(root):
    root = Path(root)
    shipped, needed, glibc = set(), set(), set()
    files = list(elf_files(root))
    for path in files:
        shipped.add(path.name)
        out = subprocess.run(["objdump", "-p", str(path)], capture_output=True, text=True).stdout
        shipped.update(re.findall(r"^\s+SONAME\s+(\S+)", out, re.M))
        needed.update(re.findall(r"^\s+NEEDED\s+(\S+)", out, re.M))
        glibc.update(re.findall(r"\bGLIBC_(\d+(?:\.\d+)+)\b", out))
    # A path ($ORIGIN/../lib/...) points inside the bundle, not at a system library.
    for name in sorted(n for n in needed - shipped if "/" not in n):
        print(f"{name}()(64bit)")
    newest = max(glibc, key=lambda v: tuple(map(int, v.split("."))))
    print(f"libc.so.6(GLIBC_{newest})(64bit)")


if __name__ == "__main__":
    main(sys.argv[1])
