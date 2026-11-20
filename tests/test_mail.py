import json

import pytest

from fillmate import MailError, engine, mail
from fillmate.mail.gmail import GoogleAuth, parse_client_secret


@pytest.fixture
def ctx(example, profile):
    return engine.build_context(example, profile, {"dates": ["2030-01-07"]}, example.presets["head-office"])


def test_draft_uses_pack_email(ctx):
    d = engine.draft_email(ctx)
    assert d["to"] == ["boss@example.com"]
    assert d["subject"] == "Travel request – Jane Doe – 07/01/2030"
    assert "Head office, 1 Main Street, Leeds" in d["body"] and d["body"].endswith("\n")


def test_test_mode_replaces_recipients(ctx):
    d = engine.draft_email(ctx, test_mode=True, test_recipients=["me@example.com"])
    assert d["to"] == ["me@example.com"] and d["cc"] == []
    assert d["subject"].startswith("[TEST]") and "boss@example.com" in d["body"]


def test_test_mode_blocks_other_addresses(fake_sender, tmp_path):
    with pytest.raises(MailError, match="blocked"):
        mail.send(fake_sender, ["boss@example.com"], [], [], "s", "b", [], True, ["me@example.com"])
    with pytest.raises(MailError, match="no test recipients"):
        mail.send(fake_sender, ["me@example.com"], [], [], "s", "b", [], True, [])
    with pytest.raises(MailError, match="blocked"):  # a hidden Bcc counts too
        mail.send(fake_sender, ["me@example.com"], [], ["boss@example.com"], "s", "b", [], True, ["me@example.com"])
    assert fake_sender.sent == []


def test_send_builds_message_with_attachments(fake_sender, tmp_path):
    att = tmp_path / "a.pdf"
    att.write_bytes(b"%PDF-1.4 test")
    mail.send(fake_sender, ["Me <ME@example.com>"], [], [], "Hello", "Body\n", [att], True, ["me@example.com"])
    (msg,) = fake_sender.sent
    assert msg["From"] == "me@example.com" and msg["Subject"] == "Hello"
    assert [p.get_filename() for p in msg.iter_attachments()] == ["a.pdf"]


def test_draft_file_is_marked_unsent(tmp_path):
    msg = mail.build_message("me@example.com", ["you@example.com"], [], [], "s", "b", [])
    path = mail.write_draft(msg, tmp_path / "email.eml")
    assert b"X-Unsent: 1" in path.read_bytes() and "X-Unsent" not in msg


def test_client_secret_validation(isolated_home):
    good = json.dumps({"installed": {"client_id": "123-abc.apps.googleusercontent.com", "client_secret": "s"}})
    with pytest.raises(MailError, match="not valid JSON"):
        parse_client_secret("{nope")
    with pytest.raises(MailError, match="Desktop app"):
        parse_client_secret(json.dumps({"web": {}}))
    with pytest.raises(MailError, match="client_id"):
        parse_client_secret(json.dumps({"installed": {}}))
    auth = GoogleAuth()
    assert not auth.configured()
    auth.save_client_secret(good)
    assert auth.configured() and auth.client_id().startswith("123-")
    path = isolated_home / "config" / "google_client_secret.json"
    assert oct(path.stat().st_mode & 0o777) == "0o600"
    auth.remove_client_secret()
    assert not auth.configured()
