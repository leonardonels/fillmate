"""fillmate desktop app."""
import datetime as dt
import shutil
import sys
from pathlib import Path

from PySide6.QtCore import QProcess, Qt, QUrl
from PySide6.QtGui import QDesktopServices, QIcon
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QFormLayout, QFrame, QHBoxLayout, QInputDialog, QLabel,
    QLineEdit, QMainWindow, QMessageBox, QPlainTextEdit, QPushButton, QScrollArea, QSplitter,
    QTabWidget, QVBoxLayout, QWidget,
)

from .. import FillmateError, engine, mail, store
from ..fill_docx import docx_to_pdf, find_libreoffice
from ..fill_pdf import render_pages
from ..mail.gmail import GoogleAuth
from ..mail.mailapp import open_draft
from ..mail.senders import make_sender
from ..pack import list_packs
from ..paths import cache_dir
from .common import GNOME_SCHEME, Bridge, Preview, apply_theme, banner_style, gnome_available
from .fields import build_form

APP_ID = "io.github.fillmate"
PLACEHOLDER = "Fill in the form and press “Create documents” to see a preview."


def icon():
    path = Path(__file__).resolve().parent.parent / "resources" / "fillmate.svg"
    return QIcon(str(path)) if path.is_file() else QIcon.fromTheme("mail-send")


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("fillmate")
        self.resize(1400, 900)
        self.bridge = Bridge()
        self.auth = GoogleAuth()
        self.settings = store.load_settings()
        self.packs, self.pack = [], None
        self.inputs = {}
        self.outputs, self.attachments, self.out = None, [], None
        self.dark = False

        root = QWidget()
        outer = QVBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(self._header())
        self.test_banner = QLabel()
        self.test_banner.setWordWrap(True)
        outer.addWidget(self.test_banner)

        self.split = QSplitter(Qt.Horizontal)
        self.split.addWidget(self._left())
        right = QSplitter(Qt.Vertical)
        self.tabs = QTabWidget()
        right.addWidget(self.tabs)
        right.addWidget(self._mail())
        right.setSizes([520, 380])
        self.split.addWidget(right)
        self.split.setSizes([440, 960])
        outer.addWidget(self.split, 1)
        self.setCentralWidget(root)

        QApplication.instance().styleHints().colorSchemeChanged.connect(lambda _: self.apply_theme())
        self.scheme_monitor = None
        if gnome_available():  # follow GNOME's light/dark switch while open
            self.scheme_monitor = QProcess(self)
            self.scheme_monitor.readyReadStandardOutput.connect(self._scheme_changed)
            self.scheme_monitor.start("gsettings", ["monitor", *GNOME_SCHEME])
        self.reload(first=True)

    # --- layout ---

    def _header(self):
        bar = QFrame()
        bar.setFrameShape(QFrame.StyledPanel)
        lay = QHBoxLayout(bar)
        title = QLabel("fillmate")
        title.setStyleSheet("font-size:16px; font-weight:600;")
        lay.addWidget(title)
        lay.addSpacing(16)
        self.pack_combo = QComboBox()
        self.pack_combo.setMinimumWidth(320)
        self.pack_combo.currentIndexChanged.connect(self.pack_changed)
        lay.addWidget(self.pack_combo)
        lay.addStretch()
        self.account_label = QLabel()
        lay.addWidget(self.account_label)
        self.login_btn = QPushButton()
        self.login_btn.clicked.connect(self.toggle_login)
        lay.addWidget(self.login_btn)
        settings = QPushButton("Settings")
        settings.clicked.connect(lambda: self.open_settings())
        lay.addWidget(settings)
        return bar

    def _left(self):
        self.left = QWidget()
        v = QVBoxLayout(self.left)
        presets = QHBoxLayout()
        presets.addWidget(QLabel("Preset"))
        self.preset_combo = QComboBox()
        self.preset_combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.preset_combo.currentIndexChanged.connect(self.apply_preset)
        presets.addWidget(self.preset_combo, 1)
        self.save_preset_btn = QPushButton("Save…")
        self.save_preset_btn.setToolTip("Save the current values as a preset")
        self.save_preset_btn.clicked.connect(self.save_preset)
        presets.addWidget(self.save_preset_btn)
        self.delete_preset_btn = QPushButton("Delete")
        self.delete_preset_btn.clicked.connect(self.delete_preset)
        presets.addWidget(self.delete_preset_btn)
        v.addLayout(presets)
        self.form_holder = QVBoxLayout()
        v.addLayout(self.form_holder)
        self.docx_pdf = QCheckBox("Also attach DOCX documents as PDF")
        self.docx_pdf.setChecked(bool(self.settings.get("attach_docx_as_pdf")))
        self.docx_pdf.toggled.connect(self._remember_docx_pdf)
        v.addWidget(self.docx_pdf)
        self.gen_btn = QPushButton("Create documents")
        self.gen_btn.setDefault(True)
        self.gen_btn.clicked.connect(self.generate)
        v.addWidget(self.gen_btn)
        v.addStretch()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.left)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)  # the form follows the panel width
        scroll.setMinimumWidth(400)
        return scroll

    def _mail(self):
        box = QWidget()
        lay = QVBoxLayout(box)
        self.warning = QLabel()
        self.warning.setWordWrap(True)
        self.warning.hide()
        lay.addWidget(self.warning)
        form = QFormLayout()
        self.mail_to, self.mail_cc, self.mail_bcc, self.mail_subject = QLineEdit(), QLineEdit(), QLineEdit(), QLineEdit()
        form.addRow("To", self.mail_to)
        form.addRow("Cc", self.mail_cc)
        form.addRow("Bcc", self.mail_bcc)
        form.addRow("Subject", self.mail_subject)
        self.mail_body = QPlainTextEdit()
        form.addRow("Message", self.mail_body)
        self.attach_label = QLabel("—")
        self.attach_label.setWordWrap(True)
        form.addRow("Attachments", self.attach_label)
        lay.addLayout(form, 1)
        buttons = QHBoxLayout()
        self.status = QLabel()
        self.status.setWordWrap(True)
        buttons.addWidget(self.status, 1)
        self.folder_btn = QPushButton("Open folder")
        self.folder_btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.out))))
        buttons.addWidget(self.folder_btn)
        self.draft_btn = QPushButton("Open in mail app")
        self.draft_btn.clicked.connect(self.open_in_mail_app)
        buttons.addWidget(self.draft_btn)
        self.send_btn = QPushButton("Send email")
        self.send_btn.clicked.connect(self.send)
        buttons.addWidget(self.send_btn)
        lay.addLayout(buttons)
        return box

    # --- theme ---

    def apply_theme(self):
        self.dark = apply_theme(self.settings.get("theme", "auto"))
        self.test_banner.setStyleSheet(banner_style("test", self.dark, bold=True))
        self.warning.setStyleSheet(banner_style("warn", self.dark, radius=6))

    def _scheme_changed(self):
        self.scheme_monitor.readAllStandardOutput()
        if self.settings.get("theme") == "auto":
            self.apply_theme()

    def closeEvent(self, event):
        if self.scheme_monitor:
            self.scheme_monitor.kill()
            self.scheme_monitor.waitForFinished(1000)
        super().closeEvent(event)

    # --- state ---

    def reload(self, first=False):
        """Re-read settings and packs (at start and after Settings)."""
        self.settings = store.load_settings()
        self.apply_theme()
        test = self.settings["test_mode"]
        self.test_banner.setVisible(test)
        self.test_banner.setText(f"TEST MODE: emails go only to {', '.join(self.settings['test_recipients'])}. "
                                 "Turn it off in Settings → General.")
        current = self.pack.id if self.pack else self.settings.get("last_pack")
        self.packs, problems = list_packs(store.packs_dir(self.settings))
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
            QMessageBox.warning(self, "Some packs could not be loaded",
                                "\n\n".join(f"{folder.name}: {err}" for folder, err in problems))

    def pack_changed(self):
        pid = self.pack_combo.currentData()
        self.pack = next((p for p in self.packs if p.id == pid), None)
        while self.form_holder.count():
            w = self.form_holder.takeAt(0).widget()
            if w:
                w.deleteLater()
        self.inputs = {}
        self.clear_result()
        has_pack = self.pack is not None
        for w in (self.preset_combo, self.save_preset_btn, self.gen_btn):
            w.setEnabled(has_pack)
        if not has_pack:
            empty = QLabel("No form pack installed yet.\n\nOpen Settings → Form packs to import one, "
                           "or install the example to try fillmate.")
            empty.setWordWrap(True)
            self.form_holder.addWidget(empty)
            btn = QPushButton("Open form packs")
            btn.clicked.connect(lambda: self.open_settings(tab=3))
            self.form_holder.addWidget(btn)
            self.docx_pdf.hide()
            self.reload_presets()
            return
        if self.settings.get("last_pack") != self.pack.id:
            self.settings["last_pack"] = self.pack.id
            store.save_settings(self.settings)
        if self.pack.description:
            desc = QLabel(self.pack.description)
            desc.setTextFormat(Qt.PlainText)
            desc.setWordWrap(True)
            desc.setStyleSheet("color: palette(placeholder-text);")
            self.form_holder.addWidget(desc)
        box, self.inputs = build_form(self.pack.fields, self.pack.date_format)
        self.form_holder.addWidget(box)
        self.docx_pdf.setVisible(any(d.kind == "docx" for d in self.pack.documents))
        self.reload_presets(first=True)

    def all_presets(self):
        if not self.pack:
            return {}
        return {**self.pack.presets, **store.load_user_presets(self.pack.id)}

    def reload_presets(self, first=False, select=None):
        self.preset_combo.blockSignals(True)
        self.preset_combo.clear()
        self.preset_combo.addItem("— none —", None)
        for name in self.all_presets():
            self.preset_combo.addItem(name, name)
        if first and self.preset_combo.count() > 1:
            select = self.preset_combo.itemData(1)
        self.preset_combo.setCurrentIndex(max(0, self.preset_combo.findData(select)))
        self.preset_combo.blockSignals(False)
        self.apply_preset()

    def apply_preset(self):
        name = self.preset_combo.currentData()
        user = store.load_user_presets(self.pack.id) if self.pack else {}
        self.delete_preset_btn.setEnabled(name in user)
        if not self.pack:
            return
        values = self.all_presets().get(name, {})
        for f in self.pack.fields:
            if f.id in values:
                self.inputs[f.id].set(values[f.id])
            elif name is not None and f.type not in ("dates", "date"):
                self.inputs[f.id].set(f.default)

    def save_preset(self):
        name, ok = QInputDialog.getText(self, "Save preset", "Preset name (e.g. the destination):",
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
        if name in presets and QMessageBox.question(self, "Delete preset?", f"Delete the preset '{name}'?") == QMessageBox.Yes:
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
                self.account_label.setText(email)
                self.login_btn.setText("Sign out")
                self.login_btn.show()
            else:
                self.account_label.setText("Not signed in")
                self.login_btn.setText("Sign in with Google")
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
        self.send_btn.setEnabled(ready and can_send)
        self.send_btn.setToolTip("" if can_send else why)
        self.draft_btn.setEnabled(ready)
        self.folder_btn.setEnabled(ready)

    def clear_result(self):
        self.outputs, self.attachments, self.out = None, [], None
        self.tabs.clear()
        if self.pack:
            for d in self.pack.documents:
                self.tabs.addTab(Preview(PLACEHOLDER), d.label)
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
        docx_pdf = self.docx_pdf.isChecked() and self.docx_pdf.isVisible()

        def work():
            ctx = engine.build_context(pack, profile, values, preset)
            out = engine.output_folder(ctx, store.output_dir(settings))
            outputs = engine.generate(ctx, out)
            attachments = [o.path for o in outputs]
            previews, preview_dir = {}, cache_dir() / "preview"
            for o in outputs:
                try:
                    if o.kind == "pdf":
                        previews[o.document_id] = render_pages(o.path)
                    elif find_libreoffice():
                        converted = docx_to_pdf(o.path, preview_dir)
                        previews[o.document_id] = render_pages(converted)
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
        self.warning.setText("\n".join(f"Warning: {x}" for x in w))
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
        QMessageBox.warning(self, "fillmate", message)

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
            QMessageBox.warning(self, "Sending blocked", str(e))
            return
        s = self.sender()
        text = (f"From: {s.from_address()}\nTo: {', '.join(to)}"
                + (f"\nCc: {', '.join(cc)}" if cc else "") + (f"\nBcc: {', '.join(bcc)}" if bcc else "")
                + f"\n\nSubject: {subject}\nAttachments: {', '.join(p.name for p in files)}")
        if not test_mode:
            text += "\n\nTest mode is off: the email goes to the real recipients."
        if QMessageBox.question(self, "Send this email?", text) != QMessageBox.Yes:
            return
        self.send_btn.setEnabled(False)
        self.status.setText("Sending…")
        self.bridge.run(lambda: mail.send(s, to, cc, bcc, subject, body, files, test_mode, allowed),
                        lambda _: self.sent(to + cc + bcc), self.task_failed)

    def sent(self, rcpt):
        self.status.setText(f"Sent to {', '.join(rcpt)} at {dt.datetime.now():%H:%M}.")
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
        QMessageBox.warning(self, "Google sign-in", message)

    def open_settings(self, tab=0):
        from .settings import SettingsDialog

        dialog = SettingsDialog(self, self.bridge, self.auth, tab=tab)
        dialog.exec()
        self.reload()


def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")  # follows the light/dark palette consistently
    app.setApplicationName("fillmate")
    app.setDesktopFileName(APP_ID)
    app.setWindowIcon(icon())
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
