# fillmate privacy policy

_Last updated: 23 November 2026_

fillmate is a desktop app that fills in PDF and Word forms and sends them by email. It
runs entirely on your computer. There is no fillmate server and no fillmate account.

## What fillmate collects

Nothing. fillmate has no analytics, no telemetry, no crash reporting and no advertising.
The developer never receives any of your data.

## What stays on your computer

Everything you put into the app is stored only on your own device, in your user folders:

- your profile details (name and the other fields packs reuse),
- the form packs you install or create,
- the forms you fill in and the documents fillmate produces,
- your signature images,
- your email settings.

Credentials are kept locally too:

- **SMTP password**: saved in your system keyring (Windows Credential Manager, macOS
  Keychain, or the Linux secret service). If no keyring is available it is saved in a file
  readable only by your user account.
- **Gmail**: no password is stored. You sign in on Google's own page, and fillmate saves
  the resulting token in a file readable only by your user account.

You can delete all of this at any time by removing it in Settings or uninstalling the app
and deleting its data folder.

## When fillmate connects to the internet

Only when you ask it to send an email:

- **Gmail**: fillmate talks to Google's servers to sign you in, to read the email address
  of the account you signed in with (to use as the sender), and to send the messages you
  confirm. The only permissions requested are `openid`, `userinfo.email` and `gmail.send`:
  fillmate cannot read, delete or change the mail in your account. Signing out revokes the
  token with Google. Google's handling of this data is covered by the
  [Google Privacy Policy](https://policies.google.com/privacy). fillmate's use of
  information received from Google APIs adheres to the
  [Google API Services User Data Policy](https://developers.google.com/terms/api-services-user-data-policy),
  including the Limited Use requirements.
- **SMTP**: fillmate connects to the mail server you configure and sends the messages you
  confirm.
- **Mail app draft**: fillmate hands the message to your own mail program; it sends
  nothing itself.

The email, its recipients and its attachments go only to the mail service you chose and
the recipients you confirmed before sending.

## Sharing form packs

When you export a pack to share it, your profile details, saved forms and signatures are
never included.

## Children

fillmate is not directed at children and collects no data from anyone.

## Changes

If this policy changes, the new version will be published at this address with a new
date at the top.

## Contact

Questions about this policy: open an issue at
<https://github.com/leonardonels/fillmate/issues>.
