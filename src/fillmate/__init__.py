"""fillmate: fill recurring paperwork from reusable form packs and send it by email."""

__version__ = "0.2.1"


class FillmateError(Exception):
    """Base class for errors shown to the user as plain messages."""


class PackError(FillmateError):
    """A form pack is malformed or one of its templates fails."""


class InputError(FillmateError):
    """A value entered by the user is missing or invalid."""


class MailError(FillmateError):
    """Sending or preparing an email failed."""
