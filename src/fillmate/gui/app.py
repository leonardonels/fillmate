"""fillmate desktop app."""
import datetime as dt
import shutil
import sys
from pathlib import Path

from PySide6.QtCore import QEventLoop, QPoint, QProcess, QRect, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QFont, QFontMetrics, QIcon, QKeySequence, QRegion, QShortcut
from PySide6.QtWidgets import (
    QApplication, QButtonGroup, QComboBox, QHBoxLayout, QLabel, QLineEdit, QMainWindow,
    QMessageBox, QPlainTextEdit, QPushButton, QScrollArea, QStackedWidget, QVBoxLayout, QWidget,
)

from .. import FillmateError, engine, mail, store
from ..authoring import sync_docx_fields
from ..fill_docx import docx_to_pdf, find_libreoffice
from ..fill_pdf import render_pages
from ..mail.gmail import GoogleAuth
from ..mail.mailapp import open_draft
from ..mail.senders import make_sender
from ..pack import list_packs, remove_pack
from ..paths import cache_dir
from . import ink
from .common import GNOME_SCHEME, Bridge, Preview, apply_theme, gnome_available
from . import frame
from .fields import build_form
from .frame import Frame, is_passive
from .ink import (
    DotGrid, GlassButton, GlassGroup, GlassMenu, GlassLayer, GlassPanel, GlassSegmented, GlassSwitch, MarginNote, Sheet,
    PackRow, SideButton, T, highlight, label, set_role,
)

APP_ID = "io.github.leonardonels.fillmate"  # matches the installed .desktop file
PLACEHOLDER = "Fill in the form and press “Create documents” to see a preview."
SIDEBAR_W, INSET, FORM_W = 272, 12, 392
PAD = 24  # side margins of the content


def icon():
    path = Path(__file__).resolve().parent.parent / "resources" / "fillmate.svg"
    return QIcon(str(path)) if path.is_file() else QIcon.fromTheme("mail-send")


def scroll(widget, top=0):
    area = QScrollArea()
    area.setWidgetResizable(True)
    area.setFrameShape(QScrollArea.NoFrame)
    area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    area.setWidget(widget)
    area.viewport().setAutoFillBackground(False)
    widget.setAutoFillBackground(False)
    return area


class OutputStack(QStackedWidget):
    """The previews, one per document, followed by the email. Offers the bits of
    QTabWidget's API the window used before (clear, addTab, widget)."""

    labels_changed = Signal(list)

    def __init__(self, email_page):
        super().__init__()
        self.email_page = email_page
        self.addWidget(email_page)
        self.doc_labels = []

    def clear(self):
        while self.count() > 1:
            w = self.widget(0)
            self.removeWidget(w)
            ink.discard(w)
        self.doc_labels = []
        self.labels_changed.emit(self.labels())

    def addTab(self, widget, text):
        self.insertWidget(len(self.doc_labels), widget)
        self.doc_labels.append(text)
        self.labels_changed.emit(self.labels())

    def labels(self):
        return [*self.doc_labels, "Email"]


class SendCheck(GlassPanel):
    """The last check before sending, opened from the Send button."""

    def __init__(self, parent):
        super().__init__(parent, radius=28, bezel=30, blur=6, regular=True, lift=True)
        self.setFixedWidth(360)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 18, 20, 16)
        lay.setSpacing(10)
        lay.addWidget(set_role(label("Send this email?"), "h1"))
        self.rows = QLabel()
        self.rows.setTextFormat(Qt.RichText)
        self.rows.setWordWrap(True)
        lay.addWidget(self.rows)
        self.note = MarginNote(px=20)
        lay.addWidget(self.note)
        lay.addStretch()
        # Send is the same glass capsule as the toolbar's Send that opened this. No Cancel button:
        # clicking anywhere outside the check, or Esc, already cancels.
        buttons = QHBoxLayout()
        buttons.addStretch()
        self.ok = GlassButton("Send", "send", self, tint=T["ink"], filled=True)
        self.ok.clicked.connect(lambda: self._done(True))
        buttons.addWidget(self.ok)
        buttons.addStretch()
        lay.addLayout(buttons)
        self.loop, self.result = None, False
        self.catcher = QWidget(parent)  # blocks the window behind while the check is open
        self.catcher.hide()
        self.catcher.mousePressEvent = lambda _e: self._done(False)
        QShortcut(QKeySequence(Qt.Key_Escape), self, activated=lambda: self._done(False))

    def ask(self, anchor, rows_html, note, ok_text):
        self.rows.setText(rows_html)
        self.note.setText(note)
        self.note.setVisible(bool(note))
        self.ok.setText(ok_text)
        self.ok.set_tint(T["ink"])  # the theme may have changed since the check was built
        self.ok.setFixedWidth(QFontMetrics(ink.font("ui", 15, QFont.DemiBold)).horizontalAdvance(ok_text) + 22 + 44)
        # Height from the wrapped text at this width, so long subjects never push rows under the note.
        lay = self.layout()
        lay.activate()
        height = lay.totalHeightForWidth(self.width()) if lay.hasHeightForWidth() else lay.totalSizeHint().height()
        self.setFixedHeight(max(height, lay.totalMinimumSize().height()))
        parent = self.parentWidget()
        self.catcher.setGeometry(parent.rect())
        self.catcher.show()
        self.catcher.raise_()
        x = min(anchor.right() - self.width(), parent.width() - self.width() - INSET)
        self.move(max(INSET, x), anchor.bottom() + 10)
        self.show()
        self.raise_()
        self.ok.setFocus()
        if self.layer:
            self.layer.refresh()
        self.loop = QEventLoop()
        self.loop.exec()
        return self.result

    def _done(self, ok):
        self.result = ok
        self.hide()
        self.catcher.hide()
        if self.loop:
            self.loop.quit()


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("fillmate")
        self.resize(1440, 900)
        self.setMinimumSize(1100, 680)
        self.bridge = Bridge()
        self.auth = GoogleAuth()
        self.settings = store.load_settings()
        self.packs, self.pack = [], None
        self.inputs = {}
        self.outputs, self.attachments, self.out = None, [], None
        self.dark = False
        self.sent_at = None

        # The pack and preset lists live in the sidebar; these combos are their models.
        self.pack_combo = QComboBox(self)
        self.pack_combo.hide()
        self.pack_combo.currentIndexChanged.connect(self.pack_changed)
        self.preset_combo = QComboBox(self)
        self.preset_combo.hide()
        self.preset_combo.currentIndexChanged.connect(self.apply_preset)

        self.canvas = QWidget()
        self.canvas.resizeEvent = lambda e: self._place()
        self.setCentralWidget(self.canvas)
        self.content = DotGrid(self.canvas)  # everything under the glass: the print room
        self.content.room = True
        self.content.left = PAD
        self.content.type_left = 0  # the big type sits over the previews, left of the form
        self.content.right_pad = FORM_W + PAD + 28 + 50
        self.glass = GlassLayer(self.content)
        self.frame = Frame(self, self._drag_zone)
        # On a Mac the traffic lights sit on the toolbar row, before the drawer toggle.
        self.frame.mac_lights = (INSET + 22, INSET + 14)
        if frame.PLATFORM == "linux":
            self.setStyleSheet("QMainWindow { background: transparent; }")
        self.content.radius = self.frame.radius
        self._content()
        self._toolbar()
        self._sidebar()
        self.win_left = self.frame.make_buttons(self.canvas, "left")
        self.win_right = self.frame.make_buttons(self.canvas, "right")
        self.frame.adopt(self.glass, self.win_left, self.win_right)
        self.edge = self.frame.overlay(self.canvas)
        self.check = self.glass.add(SendCheck(self.canvas))
        self.check.hide()
        self.pack_menu = self.glass.add(GlassMenu(self.canvas, width=300))

        QApplication.instance().styleHints().colorSchemeChanged.connect(lambda _: self.apply_theme())
        self.scheme_monitor = None
        if gnome_available():  # follow GNOME's light/dark switch while open
            self.scheme_monitor = QProcess(self)
            self.scheme_monitor.readyReadStandardOutput.connect(self._scheme_changed)
            self.scheme_monitor.start("gsettings", ["monitor", *GNOME_SCHEME])
        self.reload(first=True)

    # --- layout ---

    def _content(self):
        row = QHBoxLayout(self.content)
        row.setContentsMargins(PAD, 0, PAD, 0)
        row.setSpacing(28)

        form = QWidget()
        v = QVBoxLayout(form)
        v.setContentsMargins(0, 76, 4, 24)
        v.setSpacing(14)
        # The title is part of the column: it scrolls up with the fields (under the toolbar's glass).
        self.title = set_role(label("New request"), "h1")
        self.subtitle = label("", "muted")
        heading = QVBoxLayout()
        heading.setContentsMargins(6, 0, 0, 0)
        heading.setSpacing(0)
        heading.addWidget(self.title)
        heading.addWidget(self.subtitle)
        v.addLayout(heading)
        self.warning = MarginNote(arrow=False)  # rule warnings, in fillmate's red pen
        self.warning.hide()
        v.addWidget(self.warning)
        self.form_holder = QVBoxLayout()
        self.form_holder.setSpacing(12)
        v.addLayout(self.form_holder)
        self.docx_row = QWidget()
        h = QHBoxLayout(self.docx_row)
        h.setContentsMargins(6, 4, 6, 0)
        text = QVBoxLayout()
        text.setSpacing(1)
        text.addWidget(label("Also attach Word files as PDF"))
        text.addWidget(label("Needs LibreOffice", "lbl"))
        h.addLayout(text, 1)
        self.docx_pdf = GlassSwitch(checked=bool(self.settings.get("attach_docx_as_pdf")))
        self.docx_pdf.setAccessibleName("Also attach Word files as PDF")
        self.docx_pdf.toggled.connect(self._remember_docx_pdf)
        h.addWidget(self.docx_pdf)
        v.addWidget(self.docx_row)
        # Create documents is glass, so it can't sit inside the form (the content it would bend): it lies
        # on the glass layer exactly over this slot and scrolls with it, cut off below the toolbar.
        self.gen_slot = QWidget()
        self.gen_slot.setFixedHeight(44)
        self.gen_slot.moveEvent = lambda e: self._place_gen()
        v.addWidget(self.gen_slot)
        text = "Create documents"
        width = QFontMetrics(ink.font("ui", 15, QFont.DemiBold)).horizontalAdvance(text) + 22 + 44
        # The arrow points at the previews it makes, on the left.
        self.gen_btn = self.glass.add(GlassButton(text, "arrow_left", self.canvas, width=width))
        self.gen_btn.clicked.connect(self.generate)
        self.status = label("", "muted", wrap=True)
        v.addWidget(self.status)
        v.addStretch()
        self.form_scroll = scroll(form)
        self.form_scroll.setFixedWidth(FORM_W)
        self.form_scroll.verticalScrollBar().valueChanged.connect(self.glass.schedule)
        self.form_scroll.verticalScrollBar().valueChanged.connect(self._place_gen)

        self.tabs = OutputStack(self._mail())
        self.tabs.currentChanged.connect(self._page_shown)
        self.tabs.labels_changed.connect(self._labels_changed)
        row.addWidget(self.tabs, 1)
        row.addWidget(self.form_scroll)

    def _mail(self):
        page = QWidget()
        v = QVBoxLayout(page)
        v.setContentsMargins(0, 76, 0, 24)
        sheet = Sheet()
        s = QVBoxLayout(sheet)
        s.setContentsMargins(28, 14, 28, 22)
        s.setSpacing(0)
        self.mail_to, self.mail_cc, self.mail_bcc, self.mail_subject = QLineEdit(), QLineEdit(), QLineEdit(), QLineEdit()
        for name, w, hint in (("To", self.mail_to, ""), ("Cc", self.mail_cc, "Add people"),
                              ("Bcc", self.mail_bcc, "Add people"), ("Subject", self.mail_subject, "")):
            r = QHBoxLayout()
            r.setContentsMargins(0, 6, 0, 0)
            n = label(name, "lbl")
            n.setFixedWidth(64)
            n.setBuddy(w)
            r.addWidget(n)
            w.setPlaceholderText(hint)
            r.addWidget(w, 1)
            s.addLayout(r)
        self.mail_subject.setStyleSheet("font-weight: 600;")
        self.mail_body = QPlainTextEdit()
        self.mail_body.setMinimumHeight(260)
        self.mail_body.setAccessibleName("Message")
        self.mail_body.setStyleSheet("border-bottom: 0; line-height: 150%;")
        s.addSpacing(14)
        s.addWidget(self.mail_body, 1)
        s.addSpacing(10)
        s.addWidget(label("Attachments", "lbl"))
        self.attach_label = label("—", wrap=True)
        s.addWidget(self.attach_label)
        v.addWidget(sheet)
        v.addStretch()
        wrap = QWidget()
        h = QHBoxLayout(wrap)
        h.setContentsMargins(0, 0, 0, 0)
        h.addStretch()
        page.setMaximumWidth(760)
        h.addWidget(page, 100)
        h.addStretch()
        area = scroll(wrap)
        area.verticalScrollBar().valueChanged.connect(self.glass.schedule)
        return area

    def _toolbar(self):
        # Packs, presets and settings live in a drawer that slides over the previews when asked for.
        self.side_toggle = self.glass.add(GlassGroup(self.canvas))
        self.side_btn = self.side_toggle.add("sidebar", "Packs and settings (Ctrl+B)",
                                             lambda: self.show_sidebar(not self.sidebar.isVisible()))
        QShortcut(QKeySequence("Ctrl+B"), self, activated=lambda: self.show_sidebar(not self.sidebar.isVisible()))
        QShortcut(QKeySequence(Qt.Key_Escape), self, activated=lambda: self.show_sidebar(False))
        self.segments = self.glass.add(GlassSegmented(self.canvas, ["Email"]))
        self.segments.changed.connect(self.tabs.setCurrentIndex)
        self.tools = self.glass.add(GlassGroup(self.canvas))
        self.folder_btn = self.tools.add("folder", "Show in folder",
                                         lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.out))))
        self.draft_btn = self.tools.add("mail", "Open in your mail app", self.open_in_mail_app)
        self.send_btn = self.glass.add(GlassButton("Send", "send", self.canvas, tint=T["ink"]))
        self.send_btn.clicked.connect(self.send)
        # Zoom for the preview: a small glass capsule floating over the bottom of the pages.
        self.zoom_bar = self.glass.add(GlassGroup(self.canvas))
        self.zoom_bar.add("minus", "Zoom out (Ctrl −)", lambda: self._zoom("out"))
        self.zoom_bar.add("fit", "Fit the page (Ctrl 0)", lambda: self._zoom("fit"))
        self.zoom_bar.add("plus", "Zoom in (Ctrl +)", lambda: self._zoom("in"))
        for keys, how in (((QKeySequence.ZoomIn, "Ctrl+="), "in"), ((QKeySequence.ZoomOut,), "out"),
                          (("Ctrl+0",), "fit")):
            for k in keys:
                QShortcut(QKeySequence(k), self, activated=lambda how=how: self._zoom(how))

    def _preview(self):
        w = self.tabs.currentWidget()
        return w if isinstance(w, Preview) else None

    def _zoom(self, how):
        p = self._preview()
        if p is None:
            return
        if how == "in":
            p.zoom_in()
        elif how == "out":
            p.zoom_out()
        else:
            p.set_zoom(1.0)

    def _update_zoom_bar(self):
        p = self._preview()
        self.zoom_bar.setVisible(p is not None and p.has_pages())

    def _sidebar(self):
        # Frosted like the send check: it lies over documents, and its lists must stay readable on them.
        self.sidebar = self.glass.add(GlassPanel(self.canvas, radius=24, bezel=30, blur=6, regular=True,
                                                 lift=True))
        self.sidebar.hide()
        v = QVBoxLayout(self.sidebar)
        top = 44 + 14  # room for the toggle row above the lists
        v.setContentsMargins(12, top, 12, 12)
        v.setSpacing(2)
        v.addWidget(set_role(label("Form packs"), "sec"))
        self.pack_list = QVBoxLayout()
        self.pack_list.setSpacing(2)
        v.addLayout(self.pack_list)
        self.pack_group = QButtonGroup(self)
        self.pack_group.setExclusive(True)
        new_pack = SideButton("New pack from a document…", "plus", muted=True)
        new_pack.setCheckable(False)
        new_pack.clicked.connect(lambda: self.open_settings(tab=3))
        v.addWidget(new_pack)
        v.addSpacing(14)
        v.addWidget(set_role(label("Presets"), "sec"))
        self.preset_list = QVBoxLayout()
        self.preset_list.setSpacing(2)
        v.addLayout(self.preset_list)
        self.preset_group = QButtonGroup(self)
        self.preset_group.setExclusive(True)
        self.save_preset_btn = SideButton("Save as preset…", None, muted=True)
        self.save_preset_btn.setCheckable(False)
        self.save_preset_btn.clicked.connect(self.save_preset)
        v.addWidget(self.save_preset_btn)
        self.delete_preset_btn = SideButton("Delete this preset", None, muted=True)
        self.delete_preset_btn.setCheckable(False)
        self.delete_preset_btn.clicked.connect(self.delete_preset)
        v.addWidget(self.delete_preset_btn)
        v.addStretch()
        self.test_note = QWidget()
        tn = QVBoxLayout(self.test_note)
        tn.setContentsMargins(12, 4, 8, 6)
        tn.setSpacing(0)
        self.test_head = label("Test mode")
        tn.addWidget(self.test_head)
        self.test_banner = MarginNote(arrow=False, px=19)
        tn.addWidget(self.test_banner)
        v.addWidget(self.test_note)
        line = QWidget()
        line.setFixedHeight(1)
        line.setStyleSheet("background: rgba(120,120,128,0.25);")
        v.addSpacing(6)
        v.addWidget(line)
        v.addSpacing(6)
        acct = QHBoxLayout()
        acct.setContentsMargins(12, 0, 4, 0)
        self.account_label = label("", "muted", wrap=True)
        acct.addWidget(self.account_label, 1)
        self.login_btn = QPushButton()
        self.login_btn.clicked.connect(self.toggle_login)
        acct.addWidget(self.login_btn)
        v.addLayout(acct)
        settings = SideButton("Settings", "gear")
        settings.setCheckable(False)
        settings.clicked.connect(lambda: self.open_settings())
        v.addWidget(settings)

    def _place(self):
        w, h = self.canvas.width(), self.canvas.height()
        self.content.setGeometry(0, 0, w, h)
        self.sidebar.setGeometry(INSET, INSET, SIDEBAR_W, h - 2 * INSET)
        x = INSET + (72 if self.frame.mac_top() else 0)  # after the traffic lights (mac_lights) on a Mac
        if self.win_left is not None:  # window buttons on the left: the toggle goes after them
            self.win_left.move(INSET, INSET)
            x = self.win_left.x() + self.win_left.width() + 12
        self.side_toggle.move(x, INSET)
        right = w - INSET
        if self.win_right is not None:
            self.win_right.move(right - self.win_right.width(), INSET)
            right = self.win_right.x() - 12
        self.send_btn.move(right - self.send_btn.width(), INSET)
        self.tools.move(self.send_btn.x() - 12 - self.tools.width(), INSET)
        # The output tabs sit over the previews: centred over them, shortened when there is no room,
        # never under the toggle or the buttons on the right.
        docs_left, docs_right = PAD, w - PAD - FORM_W - 28
        room_left = self.side_toggle.x() + self.side_toggle.width() + 12
        if self.sidebar.isVisible():  # the tabs move aside rather than lie under the drawer's glass
            room_left = max(room_left, self.sidebar.geometry().right() + 12)
        room_right = min(self.tools.x() - 12, docs_right)
        self.segments.set_max_width(max(GlassSegmented.MIN_TAB + 8, room_right - room_left))
        x = (docs_left + docs_right - self.segments.width()) // 2
        self.segments.move(max(room_left, min(x, room_right - self.segments.width())), INSET)
        self.zoom_bar.move((docs_left + docs_right - self.zoom_bar.width()) // 2,
                           h - INSET - 8 - self.zoom_bar.height())
        self._update_zoom_bar()
        self._place_gen()
        for wdg in (self.segments, self.tools, self.send_btn, self.zoom_bar, self.gen_btn, self.sidebar,
                    self.side_toggle, self.win_left, self.win_right):
            if wdg is not None:
                wdg.raise_()
        self.edge.setGeometry(self.canvas.rect())
        self.edge.raise_()
        self.glass.schedule()

    def show_sidebar(self, on):
        """Open or close the packs-and-settings drawer: glass over the previews."""
        if on == self.sidebar.isVisible():
            return
        self.sidebar.setVisible(on)
        self.side_toggle.set_bare(on)  # on the drawer the toggle is just its icon: no glass on glass
        self._place()  # moves the tabs aside (or back) and raises the drawer and its toggle
        self.glass.schedule()

    def _place_gen(self, *_args):
        """Create documents exactly over its slot in the form, cut off where it slides under the toolbar."""
        if not hasattr(self, "gen_btn"):
            return  # the slot moves while the form is first laid out, before the button exists
        btn = self.gen_btn
        slot = self.gen_slot.mapTo(self.canvas, QPoint(0, 0))
        btn.move(slot.x() + 6, slot.y())
        view = self.form_scroll.viewport()
        top = max(INSET + 44 + 8, view.mapTo(self.canvas, QPoint(0, 0)).y())
        bottom = view.mapTo(self.canvas, QPoint(0, view.height())).y()
        shown = QRect(0, top - slot.y(), btn.width(), bottom - top).intersected(btn.rect())
        btn.setVisible(not shown.isEmpty())
        if shown == btn.rect():
            btn.clearMask()
        elif not shown.isEmpty():
            btn.setMask(QRegion(shown))
        if btn.shadow is not None:
            btn.shadow.setVisible(btn.isVisible() and shown == btn.rect())

    def _labels_changed(self, labels):
        self.segments.set_labels(labels, min(self.tabs.currentIndex(), len(labels) - 1))
        self._place()

    def _page_shown(self, index):
        self.segments.set_current(index, emit=False)
        self._update_zoom_bar()
        self.glass.schedule()

    # --- theme ---

    def apply_theme(self):
        self.dark = apply_theme(self.settings.get("theme", "auto"))
        self.send_btn.set_tint(T["ink"] if not self.sent_at else None)
        self.tools.restyle()
        self.zoom_bar.restyle()
        for note in (self.warning, self.test_banner):
            note.restyle()
        self.test_head.setStyleSheet(ink.hand_style(25, underline=True))
        self.canvas.update()
        self.glass.schedule()

    def _scheme_changed(self):
        self.scheme_monitor.readAllStandardOutput()
        if self.settings.get("theme") == "auto":
            self.apply_theme()

    def _drag_zone(self, widget, pos):
        """Empty toolbar space and the top of the sidebar move the window."""
        return pos.y() < INSET + 52 and is_passive(widget)

    def showEvent(self, event):
        super().showEvent(event)
        self.frame.after_show()

    def closeEvent(self, event):
        if self.scheme_monitor:
            self.scheme_monitor.kill()
            self.scheme_monitor.waitForFinished(1000)
        super().closeEvent(event)

    # --- sidebar lists ---

    def _fill_list(self, layout, group, combo, on_pick):
        for b in group.buttons():
            group.removeButton(b)
        while layout.count():
            w = layout.takeAt(0).widget()
            if w:
                ink.discard(w)
        for i in range(combo.count()):
            if combo is self.pack_combo:
                b = PackRow(combo.itemText(i))
                b.more.connect(lambda where, i=i: self._pack_menu(i, where))
            else:
                b = SideButton(combo.itemText(i))
            b.setChecked(i == combo.currentIndex())
            b.clicked.connect(lambda _c=False, i=i: on_pick(i))
            group.addButton(b, i)
            layout.addWidget(b)

    def _pack_menu(self, index, where):
        pid = self.pack_combo.itemData(index)
        pack = next((p for p in self.packs if p.id == pid), None)
        if pack is None:
            return
        m = self.pack_menu
        m.clear()
        for d in pack.documents:  # each document opens straight in its editor
            word = d.kind == "docx"
            m.add(f"{'Edit in Word' if word else 'Edit positions'}: {d.label}", "doc",
                  lambda d=d: self.edit_document(pack, d))
        m.add("Pack settings…", "gear", lambda: self.open_settings(tab=3, select=pack.id))
        m.add("Show in folder", "folder", lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(pack.root))))
        m.add_separator()
        m.add("Remove pack…", "trash", lambda: self.remove_pack(pack), danger=True)
        m.popup(where)

    def edit_document(self, pack, d):
        if d.kind == "docx":  # new {{ placeholders }} are picked up when the packs reload
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(d.template)))
            return
        from .calibrate import CalibrateDialog

        if CalibrateDialog(self, pack, d.id).exec():
            self.reload()

    def remove_pack(self, pack):
        if frame.ask(self, "Remove pack?",
                                f"Delete the pack “{pack.name}” and its documents from\n{pack.root}?\n\n"
                                "Documents you already created are not touched.") != QMessageBox.Yes:
            return
        try:
            remove_pack(pack, store.packs_dir(self.settings))
        except (FillmateError, OSError) as e:
            frame.tell(self, "Remove failed", str(e))
            return
        if self.pack and self.pack.id == pack.id:
            self.pack = None
        self.reload()

    def rebuild_packs(self):
        self._fill_list(self.pack_list, self.pack_group, self.pack_combo, self.pack_combo.setCurrentIndex)

    def rebuild_presets(self):
        self._fill_list(self.preset_list, self.preset_group, self.preset_combo, self.preset_combo.setCurrentIndex)

    # --- state ---

    def reload(self, first=False):
        """Re-read settings and packs (at start and after Settings)."""
        self.settings = store.load_settings()
        self.apply_theme()
        self._show_test_mode()
        current = self.pack.id if self.pack else self.settings.get("last_pack")
        self.packs, problems = list_packs(store.packs_dir(self.settings))
        for i, p in enumerate(self.packs):  # new {{ placeholders }} typed into a Word document become fields
            try:
                self.packs[i] = sync_docx_fields(p.root) or p
            except FillmateError:
                pass
        self.pack_combo.blockSignals(True)
        self.pack_combo.clear()
        for p in self.packs:
            self.pack_combo.addItem(p.name, p.id)
        ids = [p.id for p in self.packs]
        self.pack_combo.setCurrentIndex(ids.index(current) if current in ids else (0 if ids else -1))
        self.pack_combo.blockSignals(False)
        self.pack_changed()
        self.refresh_account()
        if problems and first:
            frame.tell(self, "Some packs could not be loaded",
                                "\n\n".join(f"{folder.name}: {err}" for folder, err in problems))

    def _show_test_mode(self):
        self.test_note.setVisible(self.settings["test_mode"])
        who = ", ".join(self.settings["test_recipients"])
        self.test_banner.setText(f"emails only go to {who}" if who else
                                 "add your own address in Settings to try sending")
        self._update_room()

    def _update_room(self):
        """The print room behind everything names the open pack and says where emails go."""
        pack = getattr(self, "pack", None)
        who = ", ".join(self.settings.get("test_recipients") or [])
        edge = (f"Test mode · emails only go to {who}" if who else "Test mode · no test address yet") \
            if self.settings.get("test_mode") else "Live · emails go to the real recipients"
        self.content.set_room(heading=pack.name if pack else "fillmate",
                              side=f"fillmate · form pack · {pack.id}" if pack else "fillmate",
                              edge=edge,
                              foot=f"{pack.id if pack else 'fillmate'} · {dt.date.today():%d %b %Y}",
                              stamp=self._stamp() if pack else None)
        self.glass.schedule()

    def _stamp(self):
        """Where this request stands, as a rubber stamp on the print room: draft, filled, sent."""
        if getattr(self, "sent_at", None):
            test = " · test" if self.settings.get("test_mode") else ""
            return "SENT", f"{self.sent_at:%d %b %H:%M}{test}"
        if getattr(self, "outputs", None) is not None and getattr(self, "filled_at", None):
            return "FILLED", f"{self.filled_at:%d %b %H:%M}"
        return "DRAFT", f"{dt.date.today():%d %b %Y}"

    def pack_changed(self):
        pid = self.pack_combo.currentData()
        self.pack = next((p for p in self.packs if p.id == pid), None)
        self.rebuild_packs()
        while self.form_holder.count():
            w = self.form_holder.takeAt(0).widget()
            if w:
                ink.discard(w)
        self.inputs = {}
        self.clear_result()
        has_pack = self.pack is not None
        for w in (self.save_preset_btn, self.gen_btn):
            w.setEnabled(has_pack)
        self.subtitle.setText(self.pack.name if has_pack else "")
        self._update_room()
        if not has_pack:
            empty = label("No form pack installed yet.\n\nOpen Settings → Form packs to import one, "
                          "or install the example to try fillmate.", "muted", wrap=True)
            self.form_holder.addWidget(empty)
            btn = QPushButton("Open form packs")
            btn.clicked.connect(lambda: self.open_settings(tab=3))
            self.form_holder.addWidget(btn)
            self.docx_row.hide()
            self.reload_presets()
            return
        if self.settings.get("last_pack") != self.pack.id:
            self.settings["last_pack"] = self.pack.id
            store.save_settings(self.settings)
        if self.pack.description:
            desc = label(self.pack.description, "muted", wrap=True)
            desc.setContentsMargins(6, 0, 6, 0)
            self.form_holder.addWidget(desc)
        box, self.inputs = build_form(self.pack.fields, self.pack.date_format)
        if self.inputs:  # a pack with nothing to ask gets no empty sheet
            sheet = Sheet()
            QVBoxLayout(sheet).addWidget(box)
            sheet.layout().setContentsMargins(0, 4, 0, 4)
            self.form_holder.addWidget(sheet)
        else:
            ink.discard(box)
        self.docx_row.setVisible(any(d.kind == "docx" for d in self.pack.documents))
        self.reload_presets(first=True)

    def all_presets(self):
        if not self.pack:
            return {}
        return {**self.pack.presets, **store.load_user_presets(self.pack.id)}

    def reload_presets(self, first=False, select=None):
        self.preset_combo.blockSignals(True)
        self.preset_combo.clear()
        self.preset_combo.addItem("No preset", None)
        for name in self.all_presets():
            self.preset_combo.addItem(name, name)
        if first and self.preset_combo.count() > 1:
            select = self.preset_combo.itemData(1)
        self.preset_combo.setCurrentIndex(max(0, self.preset_combo.findData(select)))
        self.preset_combo.blockSignals(False)
        self.apply_preset()

    def apply_preset(self):
        self.rebuild_presets()
        name = self.preset_combo.currentData()
        user = store.load_user_presets(self.pack.id) if self.pack else {}
        self.delete_preset_btn.setVisible(name in user)
        if not self.pack:
            return
        values = self.all_presets().get(name, {})
        for f in self.pack.fields:
            if f.id in values:
                self.inputs[f.id].set(values[f.id])
            elif name is not None and f.type not in ("dates", "date"):
                self.inputs[f.id].set(f.default)

    def save_preset(self):
        name, ok = frame.get_text(self, "Save preset", "Preset name (e.g. the destination):", ok="Save",
                                        text=self.preset_combo.currentData() or "")
        name = name.strip()
        if not ok or not name:
            return
        values = {}
        for fid, fi in self.inputs.items():
            if fi.field.type in ("date", "dates"):
                continue  # presets describe where/what, not when
            values[fid] = fi.get()
        presets = store.load_user_presets(self.pack.id)
        presets[name] = values
        store.save_user_presets(self.pack.id, presets)
        self.reload_presets(select=name)

    def delete_preset(self):
        name = self.preset_combo.currentData()
        presets = store.load_user_presets(self.pack.id)
        if name in presets and frame.ask(self, "Delete preset?", f"Delete the preset '{name}'?") == QMessageBox.Yes:
            del presets[name]
            store.save_user_presets(self.pack.id, presets)
            self.reload_presets()

    def _remember_docx_pdf(self, on):
        self.settings["attach_docx_as_pdf"] = on
        store.save_settings(self.settings)

    def sender(self):
        return make_sender(self.settings, self.auth)

    def refresh_account(self):
        s = self.sender()
        self.login_btn.hide()
        if s is None:
            self.account_label.setText("Emails open in your mail app")
        elif self.settings["sender"] == "gmail":
            if not self.auth.configured():
                self.account_label.setText("Gmail not set up")
                self.login_btn.setText("Set up…")
                self.login_btn.show()
            elif email := self.auth.email():
                self.account_label.setText(f"{email}\nGmail")
                self.login_btn.setText("Sign out")
                self.login_btn.show()
            else:
                self.account_label.setText("Not signed in")
                self.login_btn.setText("Sign in")
                self.login_btn.show()
        else:
            ok, text = s.ready()
            self.account_label.setText(f"SMTP: {text}")
            if not ok:
                self.login_btn.setText("Set up…")
                self.login_btn.show()
        self.login_btn.setEnabled(True)
        self.update_buttons()

    def update_buttons(self):
        ready = self.outputs is not None
        s = self.sender()
        can_send, why = (False, "Sending is set to your mail app") if s is None else s.ready()
        self.send_btn.setVisible(s is not None)
        if self.sent_at:
            self.send_btn.setText("Sent")
            self.send_btn.icon_name = "check"
            self.send_btn.set_tint(None)
            self.send_btn.setEnabled(False)
            self.send_btn.setToolTip(f"Sent at {self.sent_at:%H:%M}")
        else:
            self.send_btn.setText("Send")
            self.send_btn.icon_name = "send"
            self.send_btn.set_tint(T["ink"])
            self.send_btn.setEnabled(ready and can_send)
            self.send_btn.setToolTip("" if can_send else why)
        self.draft_btn.setEnabled(ready)
        self.folder_btn.setEnabled(ready)
        self._update_room()  # the stamp follows the state

    def clear_result(self):
        self.outputs, self.attachments, self.out, self.sent_at = None, [], None, None
        self.tabs.clear()
        if self.pack:
            for d in self.pack.documents:
                preview = Preview(PLACEHOLDER)
                preview.verticalScrollBar().valueChanged.connect(self.glass.schedule)
                preview.changed.connect(self.glass.schedule)
                preview.changed.connect(self._update_zoom_bar)
                preview.horizontalScrollBar().valueChanged.connect(self.glass.schedule)
                self.tabs.addTab(preview, d.label)
        self.tabs.setCurrentIndex(0)
        for w in (self.mail_to, self.mail_cc, self.mail_bcc, self.mail_subject):
            w.clear()
        self.mail_body.clear()
        self.attach_label.setText("—")
        self.warning.hide()
        self.status.setText("")
        self.update_buttons()

    # --- actions ---

    def generate(self):
        pack, settings = self.pack, dict(self.settings)
        values = {fid: fi.get() for fid, fi in self.inputs.items()}
        name = self.preset_combo.currentData()
        preset = self.all_presets().get(name) if name else None
        profile = store.load_profile()
        docx_pdf = self.docx_pdf.isChecked() and self.docx_row.isVisible()

        def work():
            ctx = engine.build_context(pack, profile, values, preset)
            out = engine.output_folder(ctx, store.output_dir(settings))
            outputs = engine.generate(ctx, out)
            attachments = [o.path for o in outputs]
            previews, preview_dir = {}, cache_dir() / "preview"
            for o in outputs:
                try:
                    if o.kind == "pdf":
                        previews[o.document_id] = render_pages(o.path, dpi=200)
                    elif find_libreoffice():
                        converted = docx_to_pdf(o.path, preview_dir)
                        previews[o.document_id] = render_pages(converted, dpi=200)
                        if docx_pdf:
                            attachments.append(Path(shutil.copy(converted, out / converted.name)))
                    else:
                        previews[o.document_id] = "Install LibreOffice to preview DOCX documents."
                except FillmateError as e:
                    previews[o.document_id] = str(e)
            if docx_pdf and not find_libreoffice():
                raise FillmateError("LibreOffice is needed to attach DOCX documents as PDF.")
            draft = engine.draft_email(ctx, settings["test_mode"], settings["test_recipients"])
            return {"ctx": ctx, "out": out, "outputs": outputs, "attachments": attachments,
                    "previews": previews, "draft": draft}

        self.gen_btn.setEnabled(False)
        self.status.setText("Creating documents…")
        self.bridge.run(work, self.generated, self.task_failed)

    def generated(self, r):
        self.gen_btn.setEnabled(True)
        self.sent_at = None
        self.filled_at = dt.datetime.now()
        self.status.setText(f"Saved in {r['out']}")
        self.out, self.outputs, self.attachments = r["out"], r["outputs"], r["attachments"]
        for i, d in enumerate(self.pack.documents):
            preview = self.tabs.widget(i)
            content = r["previews"].get(d.id)
            if isinstance(content, list):
                preview.set_pages(content)
            elif content:
                preview.message(content)
        w = r["ctx"].warnings
        self.warning.setVisible(bool(w))
        self.warning.setText("\n".join(w))
        d = r["draft"]
        self.mail_to.setText(", ".join(d["to"]))
        self.mail_cc.setText(", ".join(d["cc"]))
        self.mail_bcc.setText(", ".join(d["bcc"]))
        self.mail_subject.setText(d["subject"])
        self.mail_body.setPlainText(d["body"])
        self.attach_label.setText(", ".join(p.name for p in self.attachments))
        self.update_buttons()

    def task_failed(self, message):
        self.gen_btn.setEnabled(self.pack is not None)
        self.status.setText("")
        self.refresh_account()
        frame.tell(self, "fillmate", message)

    def draft_fields(self):
        split = lambda s: engine.split_addresses([s])
        return (split(self.mail_to.text()), split(self.mail_cc.text()), split(self.mail_bcc.text()),
                self.mail_subject.text(), self.mail_body.toPlainText())

    def open_in_mail_app(self):
        to, cc, bcc, subject, body = self.draft_fields()
        s = self.sender()
        msg = mail.build_message(s.from_address() if s else None, to, cc, bcc, subject, body, self.attachments)
        eml = mail.write_draft(msg, self.out / "email.eml")
        open_draft(eml, {"to": to, "cc": cc, "bcc": bcc, "subject": subject, "body": body}, self.attachments)
        self.status.setText(f"Draft opened in your mail app ({eml.name}).")

    def send(self):
        to, cc, bcc, subject, body = self.draft_fields()
        test_mode, allowed = self.settings["test_mode"], list(self.settings["test_recipients"])
        files = list(self.attachments)
        try:
            mail.check_recipients(to, cc, bcc, test_mode, allowed)
        except FillmateError as e:
            frame.tell(self, "Sending blocked", str(e))
            return
        s = self.sender()
        import html
        e = html.escape
        row = lambda k, v: (f'<tr><td style="color:{T["label2"]}; padding:5px 12px 5px 0;">{k}</td>'
                            f'<td style="padding:5px 0;">{v}</td></tr>')
        rows = row("From", e(s.from_address())) + row("To", highlight(", ".join(to)))
        if cc:
            rows += row("Cc", e(", ".join(cc)))
        if bcc:
            rows += row("Bcc", e(", ".join(bcc)))
        rows += row("Subject", e(subject)) + row("Files", f"{len(files)} attachment{'s' if len(files) != 1 else ''}")
        note = ("Test mode: it only goes to your test addresses." if test_mode
                else "Test mode is off: this goes to the real recipients.")
        ok = self.check.ask(self.send_btn.geometry(), f"<table>{rows}</table>", note,
                            "Send to me" if test_mode else "Send")
        if not ok:
            return
        self.send_btn.setEnabled(False)
        self.status.setText("Sending…")
        self.bridge.run(lambda: mail.send(s, to, cc, bcc, subject, body, files, test_mode, allowed),
                        lambda _: self.sent(to + cc + bcc), self.task_failed)

    def sent(self, rcpt):
        self.sent_at = dt.datetime.now()
        self.status.setText(f"Sent to {', '.join(rcpt)} at {self.sent_at:%H:%M}.")
        self.update_buttons()

    def toggle_login(self):
        if self.settings["sender"] != "gmail" or not self.auth.configured():
            self.open_settings(tab=1)
            return
        if self.auth.email():
            self.auth.sign_out()
            self.refresh_account()
            return
        self.login_btn.setEnabled(False)
        self.account_label.setText("Complete the sign-in in your browser…")
        self.bridge.run(self.auth.login, lambda _: self.refresh_account(), self.login_failed)

    def login_failed(self, message):
        self.refresh_account()
        frame.tell(self, "Google sign-in", message)

    def open_settings(self, tab=0, select=None):
        from .settings import SettingsDialog

        dialog = SettingsDialog(self, self.bridge, self.auth, tab=tab)
        if select:
            dialog.refresh_packs(select=select)
        self._packs_dirty = False
        dialog.changed.connect(self._settings_changed)  # every change shows here at once
        dialog.exec()
        if self._packs_dirty:
            self.reload()

    def _settings_changed(self, packs):
        """A setting changed in Settings: apply it now. Pack changes reload the packs (and the form)."""
        if packs:
            self._packs_dirty = True
            self.reload()
            return
        old = self.settings
        self.settings = store.load_settings()
        if (old.get("packs_dir"), old.get("output_dir")) != (self.settings.get("packs_dir"),
                                                             self.settings.get("output_dir")):
            self.reload()
            return
        self.apply_theme()
        self._show_test_mode()
        self.refresh_account()


def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")  # follows the light/dark palette consistently
    app.setApplicationName("fillmate")
    app.setDesktopFileName(APP_ID)
    app.setWindowIcon(icon())
    ink.load_fonts()
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
