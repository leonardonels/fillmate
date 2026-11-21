#!/bin/sh
# Install the built fillmate .deb and .rpm in clean containers and smoke-test them.
#   packaging/linux/test-linux.sh [image ...]
# The .deb is tried on Ubuntu images, the .rpm on the others. Default images:
#   ubuntu:22.04 ubuntu:24.04 ubuntu:rolling fedora:latest almalinux:9
set -eu

if [ "${1:-}" = "--inside" ]; then
    . /etc/os-release
    echo "== $PRETTY_NAME"
    if command -v apt-get >/dev/null; then
        export DEBIAN_FRONTEND=noninteractive
        apt-get update -qq
        apt-get install -y -qq /pkg/*.deb >/dev/null
        apt-get install -y -qq --no-install-recommends xvfb xauth appstream >/dev/null
        dpkg --verify fillmate && echo "file checksums: ok"
    else
        dnf -q -y install /pkg/*.rpm >/dev/null 2>&1
        dnf -q -y install xorg-x11-server-Xvfb >/dev/null 2>&1
        dnf -q -y install appstream >/dev/null 2>&1 || true
        rpm -V fillmate && echo "file checksums: ok"
        echo "license: $(rpm -q --qf '%{LICENSE}' fillmate)"
    fi
    fillmate --version
    HOME=/tmp/home fillmate install-example >/dev/null
    HOME=/tmp/home fillmate packs | tail -1
    # Every shared library in the bundle has to resolve to something installed.
    missing=$(find /opt/fillmate/python -name '*.so*' -type f \
              -exec ldd {} + 2>/dev/null | grep "not found" | sort -u || true)
    if [ -n "$missing" ]; then echo "missing libraries:"; echo "$missing"; exit 1; fi
    # The metadata software centers read.
    if command -v appstreamcli >/dev/null; then
        appstreamcli validate --no-net /usr/share/metainfo/io.github.leonardonels.fillmate.metainfo.xml
    fi
    # Open the real main window on an X display (xcb, as on a desktop), then quit.
    Xvfb :99 -nolisten tcp >/dev/null 2>&1 &
    sleep 2
    HOME=/tmp/home DISPLAY=:99 QT_QPA_PLATFORM=xcb timeout 60 /opt/fillmate/python/bin/python3 -I - <<'EOF'
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication
import fillmate.gui.app as gui
def report():
    wins = [w for w in QApplication.topLevelWidgets() if w.isVisible()]
    print("main window opened:", ", ".join(type(w).__name__ for w in wins))
    QApplication.quit()
show = gui.MainWindow.show
def show_then_report(self):
    show(self)
    QTimer.singleShot(2000, report)
gui.MainWindow.show = show_then_report
try:
    gui.main()
except SystemExit:
    pass
EOF
    exit 0
fi

images=${*:-ubuntu:22.04 ubuntu:24.04 ubuntu:rolling fedora:latest almalinux:9}
root=$(cd "$(dirname "$0")/../.." && pwd)
here="$root/packaging/linux"
deb=$(ls "$root"/dist/fillmate_*_amd64.deb | tail -1)
rpm=$(ls "$root"/dist/fillmate-*.x86_64.rpm | tail -1)
for image in $images; do
    case $image in
        ubuntu*|debian*) pkg=$deb ;;
        *) pkg=$rpm ;;
    esac
    docker run --rm -v "$pkg:/pkg/$(basename "$pkg"):ro" -v "$here/test-linux.sh:/test-linux.sh:ro" \
        "$image" sh /test-linux.sh --inside
done
