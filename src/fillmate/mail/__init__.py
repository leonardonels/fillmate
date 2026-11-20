"""Building, checking and sending emails."""
import copy
import mimetypes
from email.message import EmailMessage
from email.utils import formatdate, getaddresses, make_msgid
from pathlib import Path

from .. import MailError


def addresses(values):
    return [addr.lower() for _, addr in getaddresses(list(values)) if addr]


def check_recipients(to, cc, bcc=(), test_mode=False, allowed=()):
    """Refuse empty or, in test mode, non-allowed recipients. Called right before every send."""
    rcpt = addresses(to) + addresses(cc) + addresses(bcc)
    if not rcpt:
        raise MailError("No recipients.")
    bad = [a for a in rcpt if "@" not in a]
    if bad:
        raise MailError(f"Invalid address: {', '.join(bad)}")
    if test_mode:
        allow = set(addresses(allowed))
        if not allow:
            raise MailError("Test mode is on but no test recipients are set (Settings → General).")
        blocked = [a for a in rcpt if a not in allow]
        if blocked:
            raise MailError(f"Test mode: sending to {', '.join(blocked)} is blocked. "
                            f"Allowed: {', '.join(sorted(allow))}.")
    return rcpt


def build_message(sender, to, cc, bcc, subject, body, attachments):
    msg = EmailMessage()
    if sender:
        msg["From"] = sender
    msg["To"] = ", ".join(to)
    if cc:
        msg["Cc"] = ", ".join(cc)
    if bcc:
        msg["Bcc"] = ", ".join(bcc)
    msg["Subject"] = subject
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid()
    msg.set_content(body)
    for a in attachments:
        a = Path(a)
        ctype = mimetypes.guess_type(a.name)[0] or "application/octet-stream"
        maintype, subtype = ctype.split("/", 1)
        msg.add_attachment(a.read_bytes(), maintype=maintype, subtype=subtype, filename=a.name)
    return msg


def write_draft(msg, path):
    """Save an .eml that mail apps supporting X-Unsent open as an editable draft."""
    draft = copy.deepcopy(msg)
    draft["X-Unsent"] = "1"
    Path(path).write_bytes(bytes(draft))
    return Path(path)


def send(sender, to, cc, bcc, subject, body, attachments, test_mode, allowed):
    """Check recipients, build the message and send it. The only path that sends email."""
    check_recipients(to, cc, bcc, test_mode, allowed)
    msg = build_message(sender.from_address(), to, cc, bcc, subject, body, attachments)
    return sender.send(msg)
