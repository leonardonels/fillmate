# dmgbuild settings for the macOS installer: run by the release workflow as
#   dmgbuild -s packaging/macos/dmg_settings.py -D app=<path to fillmate.app> \
#            -D background=<background.tiff> "fillmate <version>" dist/fillmate-<version>.dmg
#
# Briefcase's own DMG step is not used: it gives the volume the app's icon, opens a bare
# 350×150 window, and ad-hoc signs the .dmg, which makes Gatekeeper ask about the disk image
# as well as the app. This one keeps the standard disk icon, draws the "drag to install"
# window, and leaves the .dmg unsigned (the app inside is still ad-hoc signed).
import os.path

app = defines["app"]  # noqa: F821 (dmgbuild provides `defines`)
app_name = os.path.basename(app)

format = "UDZO"
files = [app]
symlinks = {"Applications": "/Applications"}
hide_extensions = [app_name]

background = defines.get("background", "builtin-arrow")  # noqa: F821
window_rect = ((200, 140), (640, 400))  # matches tools/make_dmg_background.py
default_view = "icon-view"
show_status_bar = False
show_tab_view = False
show_toolbar = False
show_pathbar = False
show_sidebar = False
icon_size = 128
text_size = 13
icon_locations = {app_name: (160, 190), "Applications": (480, 190)}
