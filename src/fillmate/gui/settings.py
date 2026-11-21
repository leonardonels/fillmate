"""Settings: general options, email sending, profile and form packs."""
import copy
import html
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QFont
from PySide6.QtWidgets import (
    QAbstractButton, QAbstractSpinBox, QButtonGroup, QComboBox, QDialog, QFileDialog, QFormLayout,
    QGroupBox, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
    QPlainTextEdit, QPushButton, QRadioButton, QScrollArea, QSizePolicy, QSpinBox, QTabWidget, QToolButton,
    QVBoxLayout, QWidget,
)

from .. import FillmateError, store
from ..authoring import (
    add_document, create_pack_from_docx, create_pack_from_pdf, remove_document, sync_docx_fields,
)
from ..mail import addresses, smtp
from ..mail.gmail import SETUP_GUIDE, parse_client_secret
from ..pack import export_pack, import_pack, list_packs, remove_pack
from ..paths import default_output_dir, default_packs_dir, examples_dir
from .common import THEMES, apply_theme
from . import frame
from .fields import build_form
from .frame import Frame, is_passive
from . import ink
from .ink import (
    ChoiceRow, CircleChoice, DotGrid, GlassLayer, GlassPanel, GlassSwitch, MarginNote, Sheet, SideButton, T, TickChoice,
    icon, set_role,
)
from .ink import label as text_label

SIDEBAR_W, INSET = 232, 12


def folder_row(line_edit, placeholder, parent):
    row = QWidget()
    lay = QHBoxLayout(row)
    lay.setContentsMargins(0, 0, 0, 0)
    line_edit.setPlaceholderText(placeholder)
    lay.addWidget(line_edit, 1)
    browse = QPushButton("Browse…")

    def pick():
        start = line_edit.text() or placeholder
        folder = QFileDialog.getExistingDirectory(parent, "Choose a folder", start)
        if folder:
            line_edit.setText(folder)

    browse.clicked.connect(pick)
    lay.addWidget(browse)
    return row


class ElidedLabel(QLabel):
    """One line of text cut in the middle when it does not fit, the whole text in the tooltip."""

    def __init__(self, text):
        super().__init__()
        self.full = text
        self.setToolTip(text)
        self.setMinimumWidth(40)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.setText(self.fontMetrics().elidedText(self.full, Qt.ElideMiddle, self.width()))


def scrolled(widget):
    area = QScrollArea()
    area.setWidgetResizable(True)
    area.setFrameShape(QScrollArea.NoFrame)
    area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    area.setWidget(widget)
    area.viewport().setAutoFillBackground(False)
    return area


def sheet(widget):
    """A settings page on a white sheet, top-aligned, at a readable width."""
    outer = QWidget()
    lay = QVBoxLayout(outer)
    lay.setContentsMargins(4, 4, 12, 16)
    card = Sheet()
    inner = QVBoxLayout(card)
    inner.setContentsMargins(20, 16, 20, 18)
    inner.addWidget(widget)
    card.setMaximumWidth(760)
    lay.addWidget(card)
    lay.addStretch()
    return outer


class SettingsDialog(QDialog):
    changed = Signal(bool)  # a setting was saved; True when packs changed too
    def __init__(self, parent, bridge, auth, tab=0):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.resize(1060, 740)
        self.bridge, self.auth = bridge, auth
        self.settings = copy.deepcopy(store.load_settings())
        self._theme = self.settings.get("theme", "auto")
        self.packs_changed = False

        # Dot-grid paper, a glass sidebar with the sections, each section's page on a sheet.
        self.paper = DotGrid(self)
        self.glass = GlassLayer(self.paper)
        self.frame = Frame(self, lambda w, pos: pos.y() < 64 and is_passive(w), dialog=True)
        if frame.PLATFORM == "linux":
            self.setStyleSheet("QDialog { background: transparent; }")
        self.paper.radius = self.frame.radius
        page_area = QVBoxLayout(self.paper)
        page_area.setContentsMargins(SIDEBAR_W + 2 * INSET + 16, 18, 24, 18)
        page_area.setSpacing(12)
        top = QHBoxLayout()
        self.heading = set_role(text_label("General"), "h2")
        top.addWidget(self.heading, 1)
        self.saved_note = text_label("Changes apply as you make them", "muted")
        top.addWidget(self.saved_note)
        top.addSpacing(150 if self.frame.right else 0)  # room for the window buttons
        page_area.addLayout(top)
        self.tabs = QTabWidget()
        self.tabs.tabBar().hide()
        self.tabs.setDocumentMode(True)
        self.tabs.addTab(scrolled(sheet(self._general())), "General")
        self.tabs.addTab(scrolled(sheet(self._email())), "Email")
        self.profile_tab = QWidget()
        self.profile_layout = QVBoxLayout(self.profile_tab)
        self.tabs.addTab(scrolled(sheet(self.profile_tab)), "Profile")
        self.tabs.addTab(self._packs(), "Form packs")
        page_area.addWidget(self.tabs, 1)
        self.save_timer = QTimer(self, singleShot=True, interval=400)
        self.save_timer.timeout.connect(self.autosave)
        self.sidebar = self.glass.add(GlassPanel(self, radius=24, bezel=30, blur=0.5, regular=True))
        side = QVBoxLayout(self.sidebar)
        side.setContentsMargins(12, 20 + self.frame.mac_top() + (40 if self.frame.left else 0), 12, 12)
        side.setSpacing(2)
        self.sections = QButtonGroup(self)
        for i, (name, icon_name) in enumerate((("General", "list"), ("Email", "mail"), ("Profile", "user"),
                                                ("Form packs", "doc"))):
            b = SideButton(name, icon_name)
            self.sections.addButton(b, i)
            side.addWidget(b)
        side.addStretch()
        self.sections.idClicked.connect(self.tabs.setCurrentIndex)
        self.win_left = self.frame.make_buttons(self, "left")
        self.win_right = self.frame.make_buttons(self, "right")
        self.frame.adopt(self.glass, self.win_left, self.win_right)
        self.edge = self.frame.overlay(self)
        self.tabs.currentChanged.connect(self._section_shown)
        self.refresh_packs()
        self.refresh_google()
        self.tabs.setCurrentIndex(tab)
        self._section_shown(tab)
        self.watch(self.tabs.widget(0), self.tabs.widget(1))
        self.smtp_password.editingFinished.connect(self.save_password)

    # --- saving: every change is saved on its own, a moment after it is made ---

    def watch(self, *roots):
        """Save whenever an input inside `roots` changes."""
        later = lambda *_a: self.save_timer.start()
        for root in roots:
            for w in root.findChildren(QLineEdit):
                if w is not self.smtp_password and not isinstance(w.parent(), (QComboBox, QAbstractSpinBox)):
                    w.textEdited.connect(later)
            for w in root.findChildren(QComboBox):
                w.currentIndexChanged.connect(later)
                if w.isEditable():
                    w.editTextChanged.connect(later)
            for w in root.findChildren(QAbstractButton):
                if w.isCheckable():
                    w.toggled.connect(later)
            for w in root.findChildren(QAbstractSpinBox):
                w.editingFinished.connect(later)
            for w in root.findChildren(CircleChoice):
                w.toggled.connect(later)
            for w in root.findChildren(TickChoice):
                w.currentTextChanged.connect(later)
            for w in root.findChildren(ChoiceRow):
                w.currentIndexChanged.connect(later)

    def autosave(self):
        self.save_timer.stop()
        s = self.settings
        recipients = [a.strip() for a in self.test_recipients.text().split(",") if a.strip()]
        s.update(packs_dir=self.packs_dir.text().strip(), output_dir=self.output_dir.text().strip(),
                 theme=self.theme.currentData(), test_mode=self.test_mode.isChecked(),
                 test_recipients=recipients, sender=self.sender_key())
        s["smtp"] = {"host": self.smtp_host.text().strip(), "port": self.smtp_port.value(),
                     "security": self.smtp_security.currentData(), "username": self.smtp_user.text().strip(),
                     "from": self.smtp_from.text().strip()}
        store.save_settings(s)
        profile = store.load_profile()
        for fid, fi in getattr(self, "profile_inputs", {}).items():
            value = fi.get()
            profile[fid] = value.isoformat() if hasattr(value, "isoformat") else value
        store.save_profile(profile)
        if s["theme"] != self._theme:
            self._theme = s["theme"]
            apply_theme(s["theme"])
            for paper in self.findChildren(DotGrid):
                paper.update()
            self.glass.schedule()
        self.changed.emit(False)
        no_address = self.test_mode.isChecked() and not addresses(recipients)
        self.test_note.setText("Add at least one test address: until then nothing can be sent."
                               if no_address else
                               "On: nothing reaches your real recipients until you turn this off.")

    def save_password(self):
        if not self.smtp_password.text():
            return
        user = self.smtp_user.text().strip()
        if not user:
            frame.tell(self, "SMTP", "Enter the username the password belongs to.")
            return
        self.autosave()
        where = smtp.save_password(user, self.smtp_password.text())
        if where == "file":
            frame.tell(self, "SMTP password", "No system keyring found: the password is saved in "
                                    "a file only your user can read.")

    def showEvent(self, event):
        super().showEvent(event)
        self.frame.after_show()

    def done(self, result):
        if self.save_timer.isActive():
            self.autosave()
        super().done(result)

    def _section_shown(self, index):
        self.heading.setText(self.tabs.tabText(index))
        if self.sections.button(index):
            self.sections.button(index).setChecked(True)
        self.glass.schedule()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.paper.setGeometry(self.rect())
        self.sidebar.setGeometry(INSET, INSET, SIDEBAR_W, self.height() - 2 * INSET)
        if self.win_right is not None:
            self.win_right.move(self.width() - INSET - self.win_right.width(), 14)
        if self.win_left is not None:
            self.win_left.move(INSET + 14, INSET + 4)
        for w in (self.sidebar, self.win_left, self.win_right):
            if w is not None:
                w.raise_()
        self.edge.setGeometry(self.rect())
        self.edge.raise_()
        self.glass.schedule()

    # --- general ---

    def _general(self):
        box = QWidget()
        form = QFormLayout(box)
        s = self.settings
        self.packs_dir = QLineEdit(s["packs_dir"])
        self.packs_dir.editingFinished.connect(self.refresh_packs)
        form.addRow("Form packs folder", folder_row(self.packs_dir, str(default_packs_dir()), self))
        self.output_dir = QLineEdit(s["output_dir"])
        form.addRow("Output folder", folder_row(self.output_dir, str(default_output_dir()), self))
        self.theme = ChoiceRow()
        for label, value in THEMES:
            self.theme.addItem({"auto": "Automatic", "light": "Light", "dark": "Dark"}.get(value, label), value)
        self.theme.setCurrentIndex(max(0, self.theme.findData(s["theme"])))
        form.addRow("Theme", self.theme)

        test = QGroupBox("Test mode")
        tl = QVBoxLayout(test)
        tl.addWidget(text_label("Emails go only to the test recipients below, whatever the pack says. "
                                "Any other address is blocked right before sending.", "muted", wrap=True))
        row = QHBoxLayout()
        row.addWidget(text_label("Send only to test addresses"), 1)
        self.test_mode = GlassSwitch(checked=bool(s["test_mode"]))  # a setting: glass, not ink
        self.test_mode.setAccessibleName("Test mode")
        row.addWidget(self.test_mode)
        tl.addLayout(row)
        self.test_note = MarginNote("On: nothing reaches your real recipients until you turn this off.")
        self.test_note.setVisible(self.test_mode.isChecked())
        self.test_mode.toggled.connect(self.test_note.setVisible)
        self.test_recipients = QLineEdit(", ".join(s["test_recipients"]))
        self.test_recipients.setPlaceholderText("your own address(es), comma separated")
        tl.addWidget(self.test_recipients)
        tl.addWidget(self.test_note)
        form.addRow(test)
        return box

    # --- email ---

    def _email(self):
        box = QWidget()
        lay = QVBoxLayout(box)
        lay.addWidget(QLabel("How should fillmate send emails?"))
        self.sender_group = QButtonGroup(self)
        options = (("gmail", "Gmail – sign in with Google (recommended for Gmail accounts)"),
                   ("smtp", "SMTP – any provider, with a password or app password"),
                   ("mail_app", "My mail app – open a draft and send it myself"))
        for i, (key, text) in enumerate(options):
            rb = QRadioButton(text)
            rb.setProperty("key", key)
            self.sender_group.addButton(rb, i)
            lay.addWidget(rb)
            if key == self.settings["sender"]:
                rb.setChecked(True)
        self.sender_group.buttonToggled.connect(lambda *_: self._show_sender())

        # Gmail
        self.gmail_box = QGroupBox("Gmail")
        gl = QVBoxLayout(self.gmail_box)
        self.google_status = QLabel()
        self.google_status.setWordWrap(True)
        gl.addWidget(self.google_status)
        guide_toggle = QToolButton(text="How to get the OAuth client (5 minutes, once)")
        guide_toggle.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        guide_toggle.setArrowType(Qt.RightArrow)
        guide_toggle.setCheckable(True)
        guide_toggle.setStyleSheet("QToolButton { border: none; font-weight: 600; }")
        guide = QLabel(SETUP_GUIDE)
        guide.setWordWrap(True)
        guide.setOpenExternalLinks(True)
        guide.setTextInteractionFlags(Qt.TextBrowserInteraction)
        guide.setVisible(not self.auth.configured())
        guide_toggle.setChecked(guide.isVisible())
        guide_toggle.setArrowType(Qt.DownArrow if guide.isVisible() else Qt.RightArrow)

        def toggle(on):
            guide.setVisible(on)
            guide_toggle.setArrowType(Qt.DownArrow if on else Qt.RightArrow)

        guide_toggle.toggled.connect(toggle)
        gl.addWidget(guide_toggle)
        gl.addWidget(guide)
        self.secret_text = QPlainTextEdit()
        self.secret_text.setPlaceholderText('Paste the client JSON here: {"installed": {"client_id": …}}')
        self.secret_text.setFont(QFont("monospace"))
        self.secret_text.setFixedHeight(90)
        gl.addWidget(self.secret_text)
        row = QHBoxLayout()
        load = QPushButton("Load file…")
        load.clicked.connect(self.load_secret_file)
        row.addWidget(load)
        save = QPushButton("Save client")
        save.clicked.connect(self.save_secret)
        row.addWidget(save)
        self.remove_secret = QPushButton("Remove client")
        self.remove_secret.clicked.connect(self.delete_secret)
        row.addWidget(self.remove_secret)
        row.addStretch()
        self.google_btn = QPushButton()
        self.google_btn.clicked.connect(self.toggle_google)
        row.addWidget(self.google_btn)
        gl.addLayout(row)
        lay.addWidget(self.gmail_box)

        # SMTP
        self.smtp_box = QGroupBox("SMTP")
        sf = QFormLayout(self.smtp_box)
        c = self.settings["smtp"]
        self.smtp_host = QLineEdit(c.get("host", ""))
        self.smtp_host.setPlaceholderText("e.g. smtp.gmail.com, smtp.office365.com, smtp.youruniversity.it")
        sf.addRow("Server", self.smtp_host)
        self.smtp_port = QSpinBox()
        self.smtp_port.setRange(1, 65535)
        self.smtp_port.setValue(int(c.get("port") or 587))
        self.smtp_security = ChoiceRow()
        for label, value in (("STARTTLS (port 587)", "starttls"), ("SSL/TLS (port 465)", "ssl"),
                             ("None (not recommended)", "none")):
            self.smtp_security.addItem(label, value)
        self.smtp_security.setCurrentIndex(max(0, self.smtp_security.findData(c.get("security", "starttls"))))
        self.smtp_security.currentIndexChanged.connect(
            lambda: self.smtp_port.setValue(465 if self.smtp_security.currentData() == "ssl" else 587))
        port_row = QWidget()
        pr = QHBoxLayout(port_row)
        pr.setContentsMargins(0, 0, 0, 0)
        pr.addWidget(self.smtp_security, 1)
        pr.addWidget(QLabel("Port"))
        pr.addWidget(self.smtp_port)
        sf.addRow("Security", port_row)
        self.smtp_user = QLineEdit(c.get("username", ""))
        sf.addRow("Username", self.smtp_user)
        self.smtp_password = QLineEdit()
        self.smtp_password.setEchoMode(QLineEdit.Password)
        has = bool(c.get("username")) and bool(smtp.load_password(c["username"]))
        self.smtp_password.setPlaceholderText("saved – type to replace" if has else "password or app password")
        sf.addRow("Password", self.smtp_password)
        self.smtp_from = QLineEdit(c.get("from", ""))
        self.smtp_from.setPlaceholderText("defaults to the username, e.g. Jane Doe <jane@example.com>")
        sf.addRow("From", self.smtp_from)
        note = QLabel("The password is kept in the system keyring when available. Gmail and Outlook accounts "
                      "with 2-step verification need an app password here.")
        note.setWordWrap(True)
        sf.addRow(note)
        lay.addWidget(self.smtp_box)

        self.mailapp_box = QLabel("fillmate saves an email draft (.eml) with the documents attached and opens it in "
                                  "your default mail app, where you check it and press Send.")
        self.mailapp_box.setWordWrap(True)
        lay.addWidget(self.mailapp_box)
        lay.addStretch()
        self._show_sender()
        return box

    def sender_key(self):
        b = self.sender_group.checkedButton()
        return b.property("key") if b else "gmail"

    def _show_sender(self):
        key = self.sender_key()
        self.gmail_box.setVisible(key == "gmail")
        self.smtp_box.setVisible(key == "smtp")
        self.mailapp_box.setVisible(key == "mail_app")

    def refresh_google(self):
        if not self.auth.configured():
            self.google_status.setText("No OAuth client yet. Follow the guide, then paste the JSON below.")
            self.google_btn.setEnabled(False)
            self.google_btn.setText("Sign in with Google")
            self.remove_secret.setEnabled(False)
            return
        self.remove_secret.setEnabled(True)
        self.google_btn.setEnabled(True)
        email = self.auth.email()
        client = self.auth.client_id() or ""
        short = client.split("-")[0] if client else "?"
        if email:
            self.google_status.setText(f"Signed in as <b>{email}</b> (client {short}…).")
            self.google_btn.setText("Sign out")
        else:
            self.google_status.setText(f"OAuth client saved ({short}…). Sign in to send emails.")
            self.google_btn.setText("Sign in with Google")

    def load_secret_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "OAuth client JSON", str(Path.home() / "Downloads"),
                                              "JSON (*.json)")
        if path:
            try:
                self.secret_text.setPlainText(Path(path).read_text(encoding="utf-8"))
            except OSError as e:
                frame.tell(self, "Cannot read file", str(e))

    def save_secret(self):
        text = self.secret_text.toPlainText().strip()
        try:
            parse_client_secret(text)
            if self.auth.configured() and frame.ask(
                    self, "Replace client?", "Replace the saved OAuth client? You will need to sign in again."
            ) != QMessageBox.Yes:
                return
            self.auth.save_client_secret(text)
        except FillmateError as e:
            frame.tell(self, "OAuth client", str(e))
            return
        self.secret_text.clear()
        self.refresh_google()

    def delete_secret(self):
        if frame.ask(self, "Remove client?", "Sign out and remove the saved OAuth client?") == QMessageBox.Yes:
            self.auth.remove_client_secret()
            self.refresh_google()

    def toggle_google(self):
        if self.auth.email():
            self.auth.sign_out()
            self.refresh_google()
            return
        self.google_btn.setEnabled(False)
        self.google_status.setText("Complete the sign-in in your browser…")
        self.bridge.run(self.auth.login, lambda _: self.refresh_google(), self._login_failed)

    def _login_failed(self, message):
        self.refresh_google()
        frame.tell(self, "Google sign-in", message)

    # --- profile ---

    def build_profile(self, packs):
        while self.profile_layout.count():
            w = self.profile_layout.takeAt(0).widget()
            if w:
                ink.discard(w)
        intro = QLabel("Details you enter once and every pack reuses. They stay on this computer and are "
                       "never included when you export a pack.")
        intro.setWordWrap(True)
        self.profile_layout.addWidget(intro)
        fields, seen = [], set()
        for p in packs:
            for f in p.profile:
                if f.id not in seen:
                    seen.add(f.id)
                    fields.append(f)
        if not fields:
            self.profile_layout.addWidget(QLabel("No installed pack asks for profile details."))
            self.profile_inputs = {}
        else:
            box, self.profile_inputs = build_form(fields, "%Y-%m-%d")
            profile = store.load_profile()
            for fid, fi in self.profile_inputs.items():
                if fid in profile:
                    fi.set(profile[fid])
            self.profile_layout.addWidget(box)
        self.profile_layout.addStretch()
        self.watch(self.profile_tab)

    # --- packs ---

    def _packs(self):
        box = QWidget()
        lay = QVBoxLayout(box)
        lay.setContentsMargins(4, 4, 12, 16)
        lay.setSpacing(14)
        lay.addWidget(text_label("A form pack is a folder (or .zip) with the documents to fill and a pack.yaml that "
                                 "says how. Changes here apply immediately.", "muted", wrap=True))
        row = QHBoxLayout()
        new = set_role(QPushButton("New pack from a document…"), "ink")
        new.clicked.connect(self.new_from_document)
        row.addWidget(new)
        for text, slot in (("Import .zip…", self.import_zip), ("Import folder…", self.import_folder),
                           ("Install example", self.install_example)):
            b = QPushButton(text)
            b.clicked.connect(slot)
            row.addWidget(b)
        row.addStretch()
        lay.addLayout(row)
        self.pack_cards = QVBoxLayout()  # one sheet per pack
        self.pack_cards.setSpacing(16)
        lay.addLayout(self.pack_cards)
        lay.addStretch()
        self.packs_scroll = scrolled(box)
        self._focus = None  # id of the pack the last action was about
        return self.packs_scroll

    def _pack_card(self, p):
        """A sheet for one pack: what it is, its documents (each editable and removable), and pack actions."""
        e = html.escape  # pack text comes from whoever made the pack
        card = Sheet()
        c = QVBoxLayout(card)
        c.setContentsMargins(20, 16, 20, 16)
        c.setSpacing(8)
        head = QHBoxLayout()
        title = QLabel(f"<b>{e(p.name)}</b>")
        title.setStyleSheet("font-size: 16px;")
        head.addWidget(title)
        meta = f"v{e(str(p.version))}" + (f" · by {e(p.author)}" if p.author else "")
        head.addWidget(text_label(meta, "muted"))
        head.addStretch()
        c.addLayout(head)
        if p.description:
            c.addWidget(text_label(p.description, "muted", wrap=True))
        c.addSpacing(4)
        c.addWidget(set_role(text_label("Documents"), "sec"))
        last = len(p.documents) == 1
        for d in p.documents:
            r = QHBoxLayout()
            r.setContentsMargins(6, 0, 0, 0)
            kind = text_label("Word" if d.kind == "docx" else "PDF", "lbl")
            kind.setFixedWidth(38)
            r.addWidget(kind)
            r.addWidget(text_label(d.label), 1)
            edit = QPushButton("Edit in Word…" if d.kind == "docx" else "Edit positions…")
            edit.setToolTip("Open it in your word processor. Type {{ name }} where a value goes; fillmate adds a "
                            "field for each new one." if d.kind == "docx" else "Place where each value goes on this PDF")
            edit.clicked.connect(lambda _c=False, p=p, d=d: self.edit_document(p, d))
            r.addWidget(edit)
            rm = QToolButton()
            rm.setIcon(icon("trash", T["red"], 16))
            rm.setToolTip("Remove this pack (it has no other document)" if last
                          else "Remove this document from the pack")
            rm.setAccessibleName(f"Remove {d.label}")
            rm.clicked.connect(lambda _c=False, p=p, d=d: self.remove_doc(p, d))
            r.addWidget(rm)
            c.addLayout(r)
        add = set_role(QPushButton("+ Add a document…"), "add")
        add.clicked.connect(lambda _c=False, p=p: self.add_doc(p))
        c.addWidget(add, 0, Qt.AlignLeft)
        line = QWidget()
        line.setFixedHeight(1)
        line.setStyleSheet(f"background: {T['rule']};")
        c.addSpacing(4)
        c.addWidget(line)
        foot = QHBoxLayout()
        path = ElidedLabel(str(p.root))
        path.setStyleSheet(f"font-size: 11px; color: {T['label2']};")
        foot.addWidget(path, 1)
        for text, slot, role in (("Show in folder", self.show_pack_folder, None), ("Export…", self.export_selected, None),
                                 ("Remove pack", self.remove_selected, "danger")):
            b = QPushButton(text)
            if role:
                set_role(b, role)
            b.clicked.connect(lambda _c=False, p=p, slot=slot: slot(p))
            foot.addWidget(b)
        c.addLayout(foot)
        card.setProperty("pack_id", p.id)
        return card

    def current_packs_dir(self):
        text = self.packs_dir.text().strip()
        return Path(text).expanduser() if text else default_packs_dir()

    def refresh_packs(self, select=None):
        self.packs, problems = list_packs(self.current_packs_dir())
        for i, p in enumerate(self.packs):  # pick up {{ placeholders }} typed into Word documents
            try:
                if (fresh := sync_docx_fields(p.root)) is not None:
                    self.packs[i] = fresh
                    self.packs_changed = True
            except FillmateError:
                pass
        while self.pack_cards.count():
            w = self.pack_cards.takeAt(0).widget()
            if w:
                ink.discard(w)
        focus = None
        for p in self.packs:
            card = self._pack_card(p)
            self.pack_cards.addWidget(card)
            if p.id == select:
                focus = card
        for folder, error in problems:
            self.pack_cards.addWidget(MarginNote(f"{folder.name} could not be loaded: {error}"))
        if not self.packs and not problems:
            self.pack_cards.addWidget(text_label("No packs yet: make one from a document, import one, or install "
                                                 "the example to try fillmate.", "muted", wrap=True))
        if focus is not None:
            QTimer.singleShot(0, lambda: self.packs_scroll.ensureWidgetVisible(focus, 0, 40))
        self.build_profile(self.packs)
        self.changed.emit(True)

    def edit_document(self, p, d):
        if d.kind == "docx":
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(d.template)))
        else:
            self.edit_positions(d.id, p)

    def add_doc(self, p):
        path, _ = QFileDialog.getOpenFileName(self, "Document to add", str(Path.home()),
                                              "PDF or Word document (*.pdf *.docx)")
        if not path:
            return
        try:
            add_document(p.root, path)
        except (FillmateError, OSError) as e:
            frame.tell(self, "Add a document", str(e))
            return
        self.packs_changed = True
        self.refresh_packs(select=p.id)

    def remove_doc(self, p, d):
        if len(p.documents) == 1:  # a pack without documents makes no sense: offer to remove the pack
            self.remove_selected(p, f"“{d.label}” is the only document of “{p.name}”, so removing it removes "
                                    f"the whole pack from\n{p.root}.\n\nDocuments you already created are not touched.")
            return
        if frame.ask(self, "Remove document?",
                                f"Remove “{d.label}” from the pack “{p.name}”? Its file is deleted from the pack "
                                "folder; documents you already created are not touched.") != QMessageBox.Yes:
            return
        try:
            remove_document(p.root, d.id)
        except (FillmateError, OSError) as e:
            frame.tell(self, "Remove document", str(e))
            return
        self.packs_changed = True
        self.refresh_packs(select=p.id)

    def show_pack_folder(self, p):
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(p.root)))

    def _import(self, src):
        dest = self.current_packs_dir()
        try:
            pack = import_pack(src, dest)
        except FillmateError as e:
            if "already installed" not in str(e):
                frame.tell(self, "Import failed", str(e))
                return
            if frame.ask(self, "Replace pack?", f"{e}\nReplace it with the imported one?") != QMessageBox.Yes:
                return
            try:
                pack = import_pack(src, dest, replace=True)
            except FillmateError as e2:
                frame.tell(self, "Import failed", str(e2))
                return
        self.packs_changed = True
        self.refresh_packs(select=pack.id)

    def import_zip(self):
        path, _ = QFileDialog.getOpenFileName(self, "Import form pack", str(Path.home()), "Form pack (*.zip)")
        if path:
            self._import(Path(path))

    def import_folder(self):
        path = QFileDialog.getExistingDirectory(self, "Import form pack folder", str(Path.home()))
        if path:
            self._import(Path(path))

    def install_example(self):
        for folder in sorted(examples_dir().iterdir()):
            if (folder / "pack.yaml").is_file():
                self._import(folder)

    def new_from_document(self):
        path, _ = QFileDialog.getOpenFileName(self, "Document to fill", str(Path.home()),
                                              "PDF or Word document (*.pdf *.docx)")
        if not path:
            return
        name, ok = frame.get_text(self, "New pack", "Name of the new pack:", text=Path(path).stem, ok="Create")
        if not ok or not name.strip():
            return
        word = path.lower().endswith(".docx")
        try:
            make = create_pack_from_docx if word else create_pack_from_pdf
            pack = make(path, self.current_packs_dir(), name.strip())
        except (FillmateError, OSError, RuntimeError) as e:
            frame.tell(self, "New pack", str(e))
            return
        self.packs_changed = True
        found = len(pack.fields)
        if word:
            text = (f"Found {found} placeholder{'s' if found != 1 else ''} in the document: each is now a field. "
                    if found else
                    "The document has no placeholders yet. Open it with “Edit in Word…”, type {{ name }} where a "
                    "value goes (for example {{ destination }}) and save it: fillmate adds a field for each. ")
        else:
            text = (f"The PDF has {found} fillable fields: they are already set up as pack fields. "
                    if found else "The PDF has no fillable fields: draw where each value goes in the next window. ")
        frame.tell(self, "New pack", text + f"\n\nThe pack is in {pack.root}.")
        self.refresh_packs(select=pack.id)
        if not word and not found:
            self.edit_positions(None, pack)

    def edit_positions(self, doc_id, p):
        from .calibrate import CalibrateDialog

        docs = [d for d in p.documents if d.kind == "pdf"] if p else []
        if doc_id:
            docs = [d for d in docs if d.id == doc_id]
        if not docs:
            return
        doc = docs[0]
        if len(docs) > 1:
            label, ok = frame.get_item(self, "Edit positions", "Which document?", [d.label for d in docs], ok="Edit")
            if not ok:
                return
            doc = next(d for d in docs if d.label == label)
        if CalibrateDialog(self, p, doc.id).exec():
            self.packs_changed = True
        self.refresh_packs(select=p.id)

    def export_selected(self, p):
        path, _ = QFileDialog.getSaveFileName(self, "Export form pack", str(Path.home() / f"{p.id}.zip"),
                                              "Form pack (*.zip)")
        if path:
            try:
                out = export_pack(p, path)
            except (FillmateError, OSError) as e:
                frame.tell(self, "Export failed", str(e))
                return
            frame.tell(self, "Exported", f"Saved {out}.\nIt contains no profile data or credentials.")

    def remove_selected(self, p, text=None):
        text = text or (f"Delete the pack “{p.name}” and its documents from\n{p.root}?\n\n"
                        "Documents you already created are not touched.")
        if frame.ask(self, "Remove pack?", text) == QMessageBox.Yes:
            try:
                remove_pack(p, self.current_packs_dir())
            except (FillmateError, OSError) as e:
                frame.tell(self, "Remove failed", str(e))
                return
            self.packs_changed = True
            self.refresh_packs()

    def open_packs_folder(self):
        folder = self.current_packs_dir()
        folder.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))
