#!/bin/sh
# Install a built fillmate .deb in clean Ubuntu containers and smoke-test it.
#   packaging/linux/test-deb.sh [dist/fillmate_<version>_amd64.deb] [image ...]
# Default images: ubuntu:22.04 ubuntu:24.04 ubuntu:rolling
set -eu

if [ "${1:-}" = "--inside" ]; then
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -qq
    apt-get install -y -qq /pkg.deb >/dev/null
    . /etc/os-release
    echo "== $PRETTY_NAME"
    fillmate --version
    HOME=/tmp/home fillmate install-example >/dev/null
    HOME=/tmp/home fillmate packs | tail -1
    # Every shared library Qt might load has to resolve to something installed.
    missing=$(find /opt/fillmate/python -name '*.so*' -type f \
              -exec ldd {} + 2>/dev/null | grep "not found" | sort -u || true)
    if [ -n "$missing" ]; then echo "missing libraries:"; echo "$missing"; exit 1; fi
    # Open the real main window on an X display (xcb, as on a desktop), then quit.
    apt-get install -y -qq --no-install-recommends xvfb xauth >/dev/null
    HOME=/tmp/home QT_QPA_PLATFORM=xcb timeout 60 xvfb-run -a /opt/fillmate/python/bin/python3 -I - <<'EOF'
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

deb=${1:-$(ls dist/fillmate_*_amd64.deb | tail -1)}
[ $# -gt 0 ] && shift
images=${*:-ubuntu:22.04 ubuntu:24.04 ubuntu:rolling}
deb=$(cd "$(dirname "$deb")" && pwd)/$(basename "$deb")
here=$(cd "$(dirname "$0")" && pwd)
for image in $images; do
    docker run --rm -v "$deb:/pkg.deb:ro" -v "$here/test-deb.sh:/test-deb.sh:ro" "$image" sh /test-deb.sh --inside
done
