#!/bin/sh
# Build one self-contained fillmate .deb that installs on Ubuntu 22.04 and every later release.
#
# The package carries its own Python (python-build-standalone, fetched by uv) under
# /opt/fillmate, so it doesn't depend on the distro's Python version. It is built on the
# oldest supported release, so every compiled wheel it picks also runs on the newer ones.
#
# Usage (from the repository root, with Docker and uv installed):
#   packaging/linux/build-deb.sh            # writes dist/fillmate_<version>_amd64.deb
#
# Inside the container this script runs itself again with --inside.
set -eu

BASE_IMAGE="ubuntu:22.04"   # the oldest Ubuntu the package supports
PYTHON="3.12"

if [ "${1:-}" != "--inside" ]; then
    root=$(cd "$(dirname "$0")/../.." && pwd)
    uv_bin=$(command -v uv) || { echo "uv is needed: https://docs.astral.sh/uv/" >&2; exit 1; }
    rm -rf "$root/build/deb"
    mkdir -p "$root/build/deb" "$root/dist"
    uv build --wheel --out-dir "$root/build/deb/wheel" "$root"
    exec docker run --rm \
        -v "$root:/src:ro" \
        -v "$root/build/deb:/work" \
        -v "$root/dist:/out" \
        -v "$uv_bin:/usr/local/bin/uv:ro" \
        -e HOST_UID="$(id -u)" -e HOST_GID="$(id -g)" \
        "$BASE_IMAGE" sh /src/packaging/linux/build-deb.sh --inside
fi

# --- inside the container ------------------------------------------------------
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq --no-install-recommends ca-certificates dpkg-dev >/dev/null

wheel=$(ls /work/wheel/fillmate-*.whl)
version=$(basename "$wheel" | cut -d- -f2)
pkg=/work/pkg
app=/opt/fillmate

# Python, installed at its final path so nothing has to be relocated afterwards.
UV_PYTHON_INSTALL_DIR=/work/python uv python install --quiet "$PYTHON"
python_dir=$(readlink -f "$(UV_PYTHON_INSTALL_DIR=/work/python uv python find "$PYTHON")")
python_dir=$(dirname "$(dirname "$python_dir")")
mkdir -p "$pkg$app" "$app"
cp -a "$python_dir" "$app/python"
rm -f "$app"/python/lib/python*/EXTERNALLY-MANAGED
uv pip install --quiet --python "$app/python/bin/python3" --compile-bytecode "$wheel"
# Drop what an installed app never uses.
rm -rf "$app"/python/lib/python*/test "$app"/python/lib/python*/idlelib \
       "$app"/python/lib/python*/tkinter "$app"/python/lib/python*/turtledemo \
       "$app"/python/include "$app"/python/share \
       "$app"/python/lib/python*/lib-dynload/_tkinter* "$app"/python/lib/libtcl* \
       "$app"/python/lib/libtk* "$app"/python/lib/tcl* "$app"/python/lib/tk* \
       "$app"/python/lib/itcl* "$app"/python/lib/thread*
"$app/python/bin/python3" -I /src/packaging/linux/prune_qt.py "$app"/python/lib/python3*/site-packages
mv "$app/python" "$pkg$app/python"

# Launchers. -I keeps the user's PYTHONPATH, user site-packages and current folder out.
mkdir -p "$pkg/usr/bin"
cat > "$pkg/usr/bin/fillmate" <<EOF
#!/bin/sh
exec $app/python/bin/python3 -I -m fillmate "\$@"
EOF
cat > "$pkg/usr/bin/fillmate-gui" <<EOF
#!/bin/sh
exec $app/python/bin/python3 -I -m fillmate gui "\$@"
EOF
chmod 755 "$pkg/usr/bin/fillmate" "$pkg/usr/bin/fillmate-gui"

# Menu entry and icons. The .desktop name matches APP_ID in gui/app.py.
app_id=io.github.leonardonels.fillmate
mkdir -p "$pkg/usr/share/applications"
cat > "$pkg/usr/share/applications/$app_id.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=fillmate
Comment=Fill recurring paperwork from reusable form packs
Exec=fillmate-gui
Icon=$app_id
Terminal=false
Categories=Office;
StartupWMClass=fillmate
EOF
for size in 16 32 64 128 256 512; do
    dir="$pkg/usr/share/icons/hicolor/${size}x${size}/apps"
    mkdir -p "$dir"
    cp "/src/packaging/icons/fillmate-$size.png" "$dir/$app_id.png"
done
mkdir -p "$pkg/usr/share/icons/hicolor/scalable/apps" "$pkg/usr/share/doc/fillmate"
cp /src/src/fillmate/resources/fillmate.svg "$pkg/usr/share/icons/hicolor/scalable/apps/$app_id.svg"
cp /src/LICENSE "$pkg/usr/share/doc/fillmate/copyright"

# Qt's own libraries come with PySide6; these are the system ones it loads at run time.
# The package names are the same on Ubuntu 22.04, 24.04 and later.
depends="libc6 (>= 2.35), libgl1, libegl1, libfontconfig1, libfreetype6, libdbus-1-3, libgssapi-krb5-2,\
 libglib2.0-0, libx11-6, libx11-xcb1, libxkbcommon0, libxkbcommon-x11-0, libxcb1,\
 libxcb-cursor0, libxcb-icccm4, libxcb-image0, libxcb-keysyms1, libxcb-randr0,\
 libxcb-render-util0, libxcb-shape0, libxcb-shm0, libxcb-sync1, libxcb-xfixes0,\
 libxcb-xinerama0, libxcb-xkb1, libwayland-client0, libwayland-cursor0, libwayland-egl1"

mkdir -p "$pkg/DEBIAN"
cat > "$pkg/DEBIAN/control" <<EOF
Package: fillmate
Version: $version
Architecture: amd64
Maintainer: Leonardo Nels <81677769+leonardonels@users.noreply.github.com>
Installed-Size: $(du -sk "$pkg" | cut -f1)
Depends: $depends
Suggests: libreoffice-writer
Section: utils
Priority: optional
Homepage: https://github.com/leonardonels/fillmate
Description: Fill recurring paperwork from reusable form packs
 fillmate turns the forms you fill in again and again into a small form:
 type what changes, check the preview, and email the filled PDF and Word
 documents. Needs Ubuntu 22.04 or later. LibreOffice is only needed to turn
 Word documents into PDF.
EOF

out="/out/fillmate_${version}_amd64.deb"
dpkg-deb --root-owner-group --build -Zxz "$pkg" "$out" >/dev/null
chown "$HOST_UID:$HOST_GID" "$out"
chown -R "$HOST_UID:$HOST_GID" /work
echo "built dist/$(basename "$out") ($(du -h "$out" | cut -f1))"
