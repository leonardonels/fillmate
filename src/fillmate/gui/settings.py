"""Settings: general options, email sending, profile and form packs."""
import copy
import html
from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices, QFont
from PySide6.QtWidgets import (
    QButtonGroup, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout,
    QGroupBox, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox,
    QPlainTextEdit, QPushButton, QRadioButton, QScrollArea, QSpinBox, QTabWidget, QToolButton,
    QVBoxLayout, QWidget,
)

from .. import FillmateError, store
from ..mail import addresses, smtp
from ..mail.gmail import SETUP_GUIDE, parse_client_secret
from ..pack import export_pack, import_pack, list_packs, remove_pack
from ..paths import default_output_dir, default_packs_dir, examples_dir
from .common import THEMES
from .fields import build_form


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


def scrolled(widget):
    area = QScrollArea()
    area.setWidgetResizable(True)
    area.setFrameShape(QScrollArea.NoFrame)
    area.setWidget(widget)
    return area


class SettingsDialog(QDialog):
    def __init__(self, parent, bridge, auth, tab=0):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.resize(760, 680)
        self.bridge, self.auth = bridge, auth
        self.settings = copy.deepcopy(store.load_settings())
        self.packs_changed = False

        lay = QVBoxLayout(self)
        self.tabs = QTabWidget()
        self.tabs.addTab(scrolled(self._general()), "General")
        self.tabs.addTab(scrolled(self._email()), "Email")
        self.profile_tab = QWidget()
        self.profile_layout = QVBoxLayout(self.profile_tab)
        self.tabs.addTab(scrolled(self.profile_tab), "Profile")
        self.tabs.addTab(self._packs(), "Form packs")
        lay.addWidget(self.tabs)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)
        self.refresh_packs()
        self.refresh_google()
        self.tabs.setCurrentIndex(tab)

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
        self.theme = QComboBox()
        for label, value in THEMES:
            self.theme.addItem(label, value)
        self.theme.setCurrentIndex(max(0, self.theme.findData(s["theme"])))
        form.addRow("Theme", self.theme)

        test = QGroupBox("Test mode")
        tl = QVBoxLayout(test)
        tl.addWidget(QLabel("Emails go only to the test recipients below, whatever the pack says. "
                            "Any other address is blocked right before sending."))
        self.test_mode = QCheckBox("Test mode on")
        self.test_mode.setChecked(bool(s["test_mode"]))
        tl.addWidget(self.test_mode)
        self.test_recipients = QLineEdit(", ".join(s["test_recipients"]))
        self.test_recipients.setPlaceholderText("your own address(es), comma separated")
        tl.addWidget(self.test_recipients)
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
        self.smtp_security = QComboBox()
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
                QMessageBox.warning(self, "Cannot read file", str(e))

    def save_secret(self):
        text = self.secret_text.toPlainText().strip()
        try:
            parse_client_secret(text)
            if self.auth.configured() and QMessageBox.question(
                    self, "Replace client?", "Replace the saved OAuth client? You will need to sign in again."
            ) != QMessageBox.Yes:
                return
            self.auth.save_client_secret(text)
        except FillmateError as e:
            QMessageBox.warning(self, "OAuth client", str(e))
            return
        self.secret_text.clear()
        self.refresh_google()

    def delete_secret(self):
        if QMessageBox.question(self, "Remove client?", "Sign out and remove the saved OAuth client?") == QMessageBox.Yes:
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
        QMessageBox.warning(self, "Google sign-in", message)

    # --- profile ---

    def build_profile(self, packs):
        while self.profile_layout.count():
            w = self.profile_layout.takeAt(0).widget()
            if w:
                w.deleteLater()
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

    # --- packs ---

    def _packs(self):
        box = QWidget()
        lay = QVBoxLayout(box)
        intro = QLabel("A form pack is a folder (or .zip) with the templates and a pack.yaml that says how to "
                       "fill them. Changes here apply immediately.")
        intro.setWordWrap(True)
        lay.addWidget(intro)
        self.pack_list = QListWidget()
        self.pack_list.currentRowChanged.connect(self._pack_selected)
        lay.addWidget(self.pack_list, 1)
        self.pack_info = QLabel()
        self.pack_info.setWordWrap(True)
        self.pack_info.setTextInteractionFlags(Qt.TextSelectableByMouse)
        lay.addWidget(self.pack_info)
        row = QHBoxLayout()
        for text, slot in (("Import .zip…", self.import_zip), ("Import folder…", self.import_folder),
                           ("Install example", self.install_example)):
            b = QPushButton(text)
            b.clicked.connect(slot)
            row.addWidget(b)
        row.addStretch()
        self.export_btn = QPushButton("Export…")
        self.export_btn.clicked.connect(self.export_selected)
        row.addWidget(self.export_btn)
        self.remove_btn = QPushButton("Remove")
        self.remove_btn.clicked.connect(self.remove_selected)
        row.addWidget(self.remove_btn)
        open_btn = QPushButton("Open folder")
        open_btn.clicked.connect(self.open_packs_folder)
        row.addWidget(open_btn)
        lay.addLayout(row)
        return box

    def current_packs_dir(self):
        text = self.packs_dir.text().strip()
        return Path(text).expanduser() if text else default_packs_dir()

    def refresh_packs(self, select=None):
        self.packs, problems = list_packs(self.current_packs_dir())
        self.pack_list.clear()
        for p in self.packs:
            item = QListWidgetItem(f"{p.name}   ·   {p.id} v{p.version}")
            item.setData(Qt.UserRole, p.id)
            self.pack_list.addItem(item)
        for folder, error in problems:
            item = QListWidgetItem(f"⚠ {folder.name}: {error}")
            item.setData(Qt.UserRole, None)
            item.setToolTip(error)
            self.pack_list.addItem(item)
        if not self.packs and not problems:
            self.pack_list.addItem("No packs yet: import one, or install the example to try fillmate.")
        ids = [p.id for p in self.packs]
        self.pack_list.setCurrentRow(ids.index(select) if select in ids else (0 if ids else -1))
        self._pack_selected(self.pack_list.currentRow())
        self.build_profile(self.packs)

    def selected_pack(self):
        item = self.pack_list.currentItem()
        pid = item.data(Qt.UserRole) if item else None
        return next((p for p in self.packs if p.id == pid), None)

    def _pack_selected(self, _row):
        p = self.selected_pack()
        self.export_btn.setEnabled(p is not None)
        self.remove_btn.setEnabled(p is not None)
        if not p:
            self.pack_info.setText("")
            return
        e = html.escape  # pack text comes from whoever made the pack
        docs = ", ".join(e(d.label) for d in p.documents)
        by = f" · by {e(p.author)}" if p.author else ""
        self.pack_info.setText(f"<b>{e(p.name)}</b>{by}<br>{e(p.description)}<br>Documents: {docs}<br>"
                               f"<small>{e(str(p.root))}</small>")

    def _import(self, src):
        dest = self.current_packs_dir()
        try:
            pack = import_pack(src, dest)
        except FillmateError as e:
            if "already installed" not in str(e):
                QMessageBox.warning(self, "Import failed", str(e))
                return
            if QMessageBox.question(self, "Replace pack?", f"{e}\nReplace it with the imported one?") != QMessageBox.Yes:
                return
            try:
                pack = import_pack(src, dest, replace=True)
            except FillmateError as e2:
                QMessageBox.warning(self, "Import failed", str(e2))
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

    def export_selected(self):
        p = self.selected_pack()
        if not p:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Export form pack", str(Path.home() / f"{p.id}.zip"),
                                              "Form pack (*.zip)")
        if path:
            try:
                out = export_pack(p, path)
            except (FillmateError, OSError) as e:
                QMessageBox.warning(self, "Export failed", str(e))
                return
            QMessageBox.information(self, "Exported", f"Saved {out}.\nIt contains no profile data or credentials.")

    def remove_selected(self):
        p = self.selected_pack()
        if p and QMessageBox.question(self, "Remove pack?",
                                      f"Delete the pack '{p.name}' and its templates from\n{p.root}?") == QMessageBox.Yes:
            try:
                remove_pack(p, self.current_packs_dir())
            except (FillmateError, OSError) as e:
                QMessageBox.warning(self, "Remove failed", str(e))
                return
            self.packs_changed = True
            self.refresh_packs()

    def open_packs_folder(self):
        folder = self.current_packs_dir()
        folder.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    # --- save ---

    def save(self):
        s = self.settings
        recipients = [a.strip() for a in self.test_recipients.text().split(",") if a.strip()]
        if self.test_mode.isChecked() and not addresses(recipients):
            QMessageBox.warning(self, "Test mode", "Add at least one test recipient, or turn test mode off.")
            self.tabs.setCurrentIndex(0)
            return
        s.update(packs_dir=self.packs_dir.text().strip(), output_dir=self.output_dir.text().strip(),
                 theme=self.theme.currentData(), test_mode=self.test_mode.isChecked(),
                 test_recipients=recipients, sender=self.sender_key())
        s["smtp"] = {"host": self.smtp_host.text().strip(), "port": self.smtp_port.value(),
                     "security": self.smtp_security.currentData(), "username": self.smtp_user.text().strip(),
                     "from": self.smtp_from.text().strip()}
        if s["sender"] == "smtp" and (not s["smtp"]["host"] or not s["smtp"]["username"]):
            QMessageBox.warning(self, "SMTP", "Enter the SMTP server and username.")
            self.tabs.setCurrentIndex(1)
            return
        if self.smtp_password.text():
            if not s["smtp"]["username"]:
                QMessageBox.warning(self, "SMTP", "Enter the username the password belongs to.")
                return
            where = smtp.save_password(s["smtp"]["username"], self.smtp_password.text())
            if where == "file":
                QMessageBox.information(self, "SMTP password", "No system keyring found: the password is saved in "
                                        "a file only your user can read.")
        store.save_settings(s)
        profile = store.load_profile()
        for fid, fi in getattr(self, "profile_inputs", {}).items():
            value = fi.get()
            profile[fid] = value.isoformat() if hasattr(value, "isoformat") else value
        store.save_profile(profile)
        self.accept()
