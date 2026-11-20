"""Send through the Gmail API after signing in with Google in the browser.

No password is stored: the sign-in happens on Google's page (passkey, 2FA, anything the
account uses) and the saved token only allows sending mail. Each user creates their own
OAuth "Desktop app" client in Google Cloud and pastes its JSON in Settings.
"""
import base64
import json
import os

from .. import MailError
from ..paths import config_dir, write_private

# Google may return scopes in a different order or add "openid".
os.environ.setdefault("OAUTHLIB_RELAX_TOKEN_SCOPE", "1")

SCOPES = ["openid", "https://www.googleapis.com/auth/userinfo.email",
          "https://www.googleapis.com/auth/gmail.send"]
SEND_URL = "https://gmail.googleapis.com/gmail/v1/users/me/messages/send"

SETUP_GUIDE = """\
<ol>
<li>Open the <a href="https://console.cloud.google.com/projectcreate">Google Cloud console</a>
and create a project (any name, e.g. "fillmate").</li>
<li>Enable the <a href="https://console.cloud.google.com/apis/library/gmail.googleapis.com">Gmail API</a>
for that project.</li>
<li>In <a href="https://console.cloud.google.com/auth/branding">Google Auth Platform → Branding</a>
set an app name and your email. Under <b>Audience</b> choose <b>External</b>, keep it in
<b>Testing</b> and add your Google address as a test user.</li>
<li>Under <a href="https://console.cloud.google.com/auth/scopes">Data access</a> add the scope
<code>https://www.googleapis.com/auth/gmail.send</code>.</li>
<li>Under <a href="https://console.cloud.google.com/auth/clients">Clients</a> create a client of type
<b>Desktop app</b> and download its JSON.</li>
<li>Paste the JSON below (or load the file) and press <b>Save</b>, then <b>Sign in with Google</b>.
Google warns that the app is unverified: that is expected for your own client
(<i>Advanced → Go to …</i>).</li>
</ol>
<p>While the client is in "Testing", Google expires the sign-in after 7 days: just sign in again.</p>
"""


def client_secret_path():
    return config_dir() / "google_client_secret.json"


def token_path():
    return config_dir() / "google_token.json"


def parse_client_secret(text):
    """Validate a pasted OAuth client JSON; return it normalised."""
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise MailError(f"That is not valid JSON: {e.msg} (line {e.lineno}).") from e
    if not isinstance(data, dict):
        raise MailError("Unexpected JSON: paste the whole file downloaded from Google Cloud.")
    if "web" in data:
        raise MailError("This is a 'Web application' client. Create one of type 'Desktop app' instead.")
    info = data.get("installed")
    if not isinstance(info, dict) or not info.get("client_id") or not info.get("client_secret"):
        raise MailError("Missing 'installed.client_id' or 'client_secret': paste the Desktop app client JSON.")
    return {"installed": info}


class GoogleAuth:
    def configured(self):
        return client_secret_path().is_file()

    def save_client_secret(self, text):
        data = parse_client_secret(text)
        write_private(client_secret_path(), json.dumps(data, indent=2))
        self.sign_out()  # a token from another client would no longer refresh

    def remove_client_secret(self):
        self.sign_out()
        client_secret_path().unlink(missing_ok=True)

    def client_id(self):
        try:
            return json.loads(client_secret_path().read_text())["installed"]["client_id"]
        except (OSError, ValueError, KeyError):
            return None

    def login(self, timeout=300):
        """Open Google's sign-in page in the browser and wait for it (blocking)."""
        from google_auth_oauthlib.flow import InstalledAppFlow

        if not self.configured():
            raise MailError("Add your Google OAuth client in Settings → Email first.")
        flow = InstalledAppFlow.from_client_secrets_file(str(client_secret_path()), scopes=SCOPES)
        try:
            creds = flow.run_local_server(
                host="127.0.0.1", port=0, open_browser=True, timeout_seconds=timeout,
                access_type="offline", prompt="consent",
                success_message="Signed in. You can close this tab and go back to fillmate.")
        except Exception as e:
            raise MailError(f"Google sign-in failed: {e}") from e
        if creds is None:
            raise MailError("Google sign-in was not completed in time.")
        data = json.loads(creds.to_json())
        data["email"] = self._fetch_email(creds)
        write_private(token_path(), json.dumps(data))
        return data["email"]

    @staticmethod
    def _fetch_email(creds):
        from google.auth.transport.requests import AuthorizedSession

        r = AuthorizedSession(creds).get("https://openidconnect.googleapis.com/v1/userinfo", timeout=30)
        r.raise_for_status()
        return r.json().get("email", "")

    def _stored(self):
        try:
            return json.loads(token_path().read_text())
        except (OSError, ValueError):
            return None

    def email(self):
        data = self._stored()
        return data.get("email") if data else None

    def credentials(self):
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials

        data = self._stored()
        if not data:
            raise MailError("You are not signed in with Google.")
        creds = Credentials.from_authorized_user_info(data, SCOPES)
        if not creds.valid:
            try:
                creds.refresh(Request())
            except Exception as e:
                token_path().unlink(missing_ok=True)
                raise MailError(f"The Google sign-in expired, sign in again ({e}).") from e
            data.update(json.loads(creds.to_json()))
            write_private(token_path(), json.dumps(data))
        return creds

    def sign_out(self):
        data = self._stored()
        if data:
            token = data.get("refresh_token") or data.get("token")
            try:
                import requests
                requests.post("https://oauth2.googleapis.com/revoke", params={"token": token},
                              headers={"Content-Type": "application/x-www-form-urlencoded"}, timeout=15)
            except Exception:
                pass  # the local token is deleted anyway
        token_path().unlink(missing_ok=True)


class GmailSender:
    name = "Gmail"

    def __init__(self, auth=None):
        self.auth = auth or GoogleAuth()

    def from_address(self):
        return self.auth.email()

    def ready(self):
        if not self.auth.configured():
            return False, "Gmail not set up"
        if not self.auth.email():
            return False, "Not signed in with Google"
        return True, self.auth.email()

    def send(self, msg):
        from google.auth.transport.requests import AuthorizedSession

        creds = self.auth.credentials()
        raw = base64.urlsafe_b64encode(bytes(msg)).decode()
        r = AuthorizedSession(creds).post(SEND_URL, json={"raw": raw}, timeout=120)
        if r.status_code >= 400:
            raise MailError(f"Gmail refused the email ({r.status_code}): {r.text[:300]}")
        return r.json().get("id")
