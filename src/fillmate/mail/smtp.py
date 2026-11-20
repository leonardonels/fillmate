"""Send through any SMTP server (Outlook, university mail, Gmail with an app password, ...).

The password goes to the system keyring when one is available, otherwise to a file
readable only by the current user.
"""
import json
import smtplib
import ssl

from .. import MailError
from ..paths import config_dir, write_private

KEYRING_SERVICE = "fillmate-smtp"


def _fallback_path():
    return config_dir() / "smtp_password.json"


def _keyring():
    try:
        import keyring
        from keyring.backends import fail

        if isinstance(keyring.get_keyring(), fail.Keyring):
            return None
        return keyring
    except Exception:
        return None


def save_password(username, password):
    kr = _keyring()
    if kr:
        try:
            kr.set_password(KEYRING_SERVICE, username, password)
            _fallback_path().unlink(missing_ok=True)
            return "keyring"
        except Exception:
            pass
    write_private(_fallback_path(), json.dumps({username: password}))
    return "file"


def load_password(username):
    kr = _keyring()
    if kr:
        try:
            value = kr.get_password(KEYRING_SERVICE, username)
            if value:
                return value
        except Exception:
            pass
    try:
        return json.loads(_fallback_path().read_text()).get(username)
    except (OSError, ValueError):
        return None


def delete_password(username):
    kr = _keyring()
    if kr:
        try:
            kr.delete_password(KEYRING_SERVICE, username)
        except Exception:
            pass
    _fallback_path().unlink(missing_ok=True)


class SmtpSender:
    name = "SMTP"

    def __init__(self, cfg):
        self.cfg = cfg

    def from_address(self):
        return self.cfg.get("from") or self.cfg.get("username")

    def ready(self):
        if not self.cfg.get("host") or not self.cfg.get("username"):
            return False, "SMTP not set up"
        if not load_password(self.cfg["username"]):
            return False, "SMTP password missing"
        return True, self.from_address()

    def send(self, msg):
        host, port = self.cfg["host"], int(self.cfg.get("port") or 587)
        security, user = self.cfg.get("security", "starttls"), self.cfg["username"]
        password = load_password(user)
        if not password:
            raise MailError("The SMTP password is missing: set it in Settings → Email.")
        context = ssl.create_default_context()
        try:
            if security == "ssl":
                server = smtplib.SMTP_SSL(host, port, context=context, timeout=60)
            else:
                server = smtplib.SMTP(host, port, timeout=60)
            with server:
                if security == "starttls":
                    server.starttls(context=context)
                server.login(user, password)
                server.send_message(msg)
        except smtplib.SMTPAuthenticationError as e:
            raise MailError(f"The SMTP server rejected the username or password ({e.smtp_code}).") from e
        except (smtplib.SMTPException, OSError) as e:
            raise MailError(f"Sending through {host} failed: {e}") from e
