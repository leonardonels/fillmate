from .gmail import GmailSender, GoogleAuth
from .smtp import SmtpSender


def make_sender(settings, auth=None):
    """The configured sender, or None when the user hands emails to their mail app."""
    kind = settings.get("sender", "gmail")
    if kind == "gmail":
        return GmailSender(auth or GoogleAuth())
    if kind == "smtp":
        return SmtpSender(settings.get("smtp", {}))
    return None
