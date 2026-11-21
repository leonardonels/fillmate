#!/bin/sh
# Build the self-contained fillmate Linux packages from one bundle:
#   dist/fillmate_<version>_amd64.deb        Ubuntu 22.04 and later, Debian 12 and later
#   dist/fillmate-<version>-1.x86_64.rpm     Fedora 35+, RHEL 9+ and other RPM distros (glibc 2.34+)
#
# Both carry their own Python (python-build-standalone, fetched by uv) under /opt/fillmate,
# so they don't depend on the distro's Python version. The bundle is built on the oldest
# supported release (Ubuntu 22.04), so every compiled wheel it picks also runs on newer ones.
#
# Usage (from the repository root, with Docker and uv installed):
#   packaging/linux/build-linux.sh
#
# Inside the container this script runs itself again with --inside.
set -eu

BASE_IMAGE="ubuntu:22.04"   # the oldest Ubuntu the packages support
PYTHON="3.12"

if [ "${1:-}" != "--inside" ]; then
    root=$(cd "$(dirname "$0")/../.." && pwd)
    uv_bin=$(command -v uv) || { echo "uv is needed: https://docs.astral.sh/uv/" >&2; exit 1; }
    rm -rf "$root/build/linux"
    mkdir -p "$root/build/linux" "$root/dist"
    uv build --wheel --out-dir "$root/build/linux/wheel" "$root"
    exec docker run --rm \
        -v "$root:/src:ro" \
        -v "$root/build/linux:/work" \
        -v "$root/dist:/out" \
        -v "$uv_bin:/usr/local/bin/uv:ro" \
        -e HOST_UID="$(id -u)" -e HOST_GID="$(id -g)" \
        "$BASE_IMAGE" sh /src/packaging/linux/build-linux.sh --inside
fi

# --- inside the container ------------------------------------------------------
# Hand everything back to the host user, even when a step fails.
trap 'chown -R "$HOST_UID:$HOST_GID" /work /out' EXIT
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq --no-install-recommends ca-certificates dpkg-dev rpm binutils >/dev/null

wheel=$(ls /work/wheel/fillmate-*.whl)
version=$(basename "$wheel" | cut -d- -f2)
today=$(date -u +%Y-%m-%d)
root=/work/root            # the files both packages install, at their final paths
app=/opt/fillmate
app_id=io.github.leonardonels.fillmate
summary="Fill recurring paperwork from reusable form packs"

# --- the bundle ------------------------------------------------------------------
# Python, installed at its final path so nothing has to be relocated afterwards.
UV_PYTHON_INSTALL_DIR=/work/python uv python install --quiet "$PYTHON"
python_dir=$(readlink -f "$(UV_PYTHON_INSTALL_DIR=/work/python uv python find "$PYTHON")")
python_dir=$(dirname "$(dirname "$python_dir")")
mkdir -p "$root$app" "$app"
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
mv "$app/python" "$root$app/python"

# Launchers. -I keeps the user's PYTHONPATH, user site-packages and current folder out.
mkdir -p "$root/usr/bin"
cat > "$root/usr/bin/fillmate" <<EOF
#!/bin/sh
exec $app/python/bin/python3 -I -m fillmate "\$@"
EOF
cat > "$root/usr/bin/fillmate-gui" <<EOF
#!/bin/sh
exec $app/python/bin/python3 -I -m fillmate gui "\$@"
EOF
chmod 755 "$root/usr/bin/fillmate" "$root/usr/bin/fillmate-gui"

# Menu entry, AppStream metadata (for software centers) and icons.
# The .desktop name matches APP_ID in gui/app.py.
mkdir -p "$root/usr/share/applications" "$root/usr/share/metainfo"
cat > "$root/usr/share/applications/$app_id.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=fillmate
Comment=$summary
Exec=fillmate-gui
Icon=$app_id
Terminal=false
Categories=Office;
StartupWMClass=fillmate
EOF
sed -e "s/@VERSION@/$version/" -e "s/@DATE@/$today/" \
    "/src/packaging/linux/$app_id.metainfo.xml" > "$root/usr/share/metainfo/$app_id.metainfo.xml"
for size in 16 32 64 128 256 512; do
    dir="$root/usr/share/icons/hicolor/${size}x${size}/apps"
    mkdir -p "$dir"
    cp "/src/packaging/icons/fillmate-$size.png" "$dir/$app_id.png"
done
mkdir -p "$root/usr/share/icons/hicolor/scalable/apps"
cp /src/src/fillmate/resources/fillmate.svg "$root/usr/share/icons/hicolor/scalable/apps/$app_id.svg"

description="fillmate turns the forms you fill in again and again into a small form:
type what changes, check the preview, and email the filled PDF and Word
documents. LibreOffice is only needed to turn Word documents into PDF."

# --- .deb ------------------------------------------------------------------------
deb=/work/deb
cp -a "$root" "$deb"
mkdir -p "$deb/usr/share/doc/fillmate" "$deb/DEBIAN"
cp /src/LICENSE "$deb/usr/share/doc/fillmate/copyright"

# Qt's own libraries come with PySide6; these are the system ones it loads at run time.
# The package names are the same on Ubuntu 22.04, 24.04 and later.
depends="libc6 (>= 2.35), libgl1, libegl1, libfontconfig1, libfreetype6, libdbus-1-3, libgssapi-krb5-2,\
 libglib2.0-0, libx11-6, libx11-xcb1, libxkbcommon0, libxkbcommon-x11-0, libxcb1,\
 libxcb-cursor0, libxcb-icccm4, libxcb-image0, libxcb-keysyms1, libxcb-randr0,\
 libxcb-render-util0, libxcb-shape0, libxcb-shm0, libxcb-sync1, libxcb-xfixes0,\
 libxcb-xinerama0, libxcb-xkb1, libwayland-client0, libwayland-cursor0, libwayland-egl1"

cat > "$deb/DEBIAN/control" <<EOF
Package: fillmate
Version: $version
Architecture: amd64
Maintainer: Leonardo Nels <81677769+leonardonels@users.noreply.github.com>
Installed-Size: $(du -sk "$deb" | cut -f1)
Depends: $depends
Suggests: libreoffice-writer
Section: utils
Priority: optional
Homepage: https://github.com/leonardonels/fillmate
Description: $summary
$(echo "$description" | sed 's/^/ /')
EOF
(cd "$deb" && find . -path ./DEBIAN -prune -o -type f -printf '%P\0' | xargs -0 md5sum) \
    > "$deb/DEBIAN/md5sums"
deb_out="/out/fillmate_${version}_amd64.deb"
dpkg-deb --root-owner-group --build -Zxz "$deb" "$deb_out" >/dev/null

# --- .rpm ------------------------------------------------------------------------
# Requires are worked out from the bundle: every system library it links to, as
# library names (not package names), so they resolve on any RPM distro.
rpm_root=/work/rpm-root
cp -a "$root" "$rpm_root"
mkdir -p "$rpm_root/usr/share/licenses/fillmate"
cp /src/LICENSE "$rpm_root/usr/share/licenses/fillmate/LICENSE"
requires=$("$rpm_root$app/python/bin/python3" -I /src/packaging/linux/system_libs.py "$rpm_root$app" \
           | sed 's/^/Requires:       /')

mkdir -p /work/rpmbuild/SPECS
cat > /work/rpmbuild/SPECS/fillmate.spec <<EOF
# The bundle is already built: package it as is, without stripping or dependency scans.
%define __os_install_post %{nil}
%define _build_id_links none
%define debug_package %{nil}
%define _binary_payload w19.xzdio

Name:           fillmate
Version:        $version
Release:        1
Summary:        $summary
License:        MIT
URL:            https://github.com/leonardonels/fillmate
BuildArch:      x86_64
AutoReqProv:    no
$requires
# LibreOffice is only for DOCX -> PDF; Recommends would make dnf install it.
Suggests:       libreoffice-writer

%description
$description

%install
cp -a $rpm_root/. %{buildroot}/

%files
%license /usr/share/licenses/fillmate/LICENSE
$app
/usr/bin/fillmate
/usr/bin/fillmate-gui
/usr/share/applications/$app_id.desktop
/usr/share/metainfo/$app_id.metainfo.xml
/usr/share/icons/hicolor/*/apps/$app_id.*
EOF
rpmbuild --quiet --define "_topdir /work/rpmbuild" -bb /work/rpmbuild/SPECS/fillmate.spec
rpm_out="/out/fillmate-${version}-1.x86_64.rpm"
cp /work/rpmbuild/RPMS/x86_64/fillmate-"$version"-1.x86_64.rpm "$rpm_out"

for f in "$deb_out" "$rpm_out"; do echo "built dist/$(basename "$f") ($(du -h "$f" | cut -f1))"; done
