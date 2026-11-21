# fillmate

Fill in the same paperwork again and again? fillmate turns the forms you keep filling in
(travel requests, internship forms, expense claims, leave requests…) into a small form.
Type in what changes each time, check the preview, and send the filled PDF and Word
documents by email.

- **Form packs**: one folder with your templates and a `pack.yaml` that says what goes
  where. Share a pack as a `.zip` and a colleague with the same paperwork is ready in a
  minute. Your personal details are never part of it.
- **Any PDF**: fillable PDF forms, and flat PDFs too. Text is placed at fixed positions,
  wrapped across lines, and shrunk to fit.
- **Word documents**: `{{ placeholders }}`, or writing into table cells while the
  template's formatting stays as it is.
- **Email**: Gmail (sign in with Google, no password stored), any SMTP server, or a draft
  opened in your own mail app.
- **Safety**: a test mode sends only to your own addresses, and a confirmation before
  each send shows exactly who gets the email.
- **Rules**: required fields, and warnings such as "less than 2 working days' notice"
  that take public holidays into account.

## Install

```bash
pipx install fillmate      # or: pip install fillmate
fillmate                   # opens the app
```

Or download an installer (`.deb`, `.rpm`, `.dmg`, `.msi`) from the
[Releases](https://github.com/leonardonels/fillmate/releases) page. The macOS and Windows
builds aren't signed yet. On macOS, right-click the app and choose **Open** the first
time. On Windows, choose **More info → Run anyway**.

## First steps

1. **Settings → Form packs → Install example** gives you a demo pack. Or import a pack
   someone shared with you.
2. **Settings → Profile**: your name and the other details packs reuse.
3. **Settings → Email**: choose how to send. For Gmail, the tab has a short guide to
   create your own OAuth client (about 5 minutes, done once).
4. Pick the pack, fill in the form, press **Create documents**, check the preview and
   the email, then send.

Turn on **test mode** (Settings → General) while you try things out. Every email then
goes only to the test addresses you list.

## Make a pack for your own forms

In **Settings → Form packs → New pack from PDF…**, pick your PDF:

- If it has fillable fields, they become pack fields right away.
- If it's a flat PDF, draw a box over each blank line and click the boxes that should
  get a check mark.

For everything else (Word templates, rules, email, presets) see
[docs/pack-format.md](https://github.com/leonardonels/fillmate/blob/main/docs/pack-format.md). A pack is a folder like this:

```
my-pack/
  pack.yaml
  request.pdf
  notice.docx
```

The bundled [example pack](https://github.com/leonardonels/fillmate/blob/main/src/fillmate/examples/travel-request/pack.yaml) uses every
feature.

## Command line

```bash
fillmate packs                                  # list installed packs
fillmate fields travel-request                  # fields, profile and presets of a pack
fillmate profile --set full_name="Jane Doe"
fillmate fill travel-request --preset head-office --set dates=2026-12-01,2026-12-02 --no-mail
fillmate fill travel-request --set dates=2026-12-01 --set destination="Leeds" --open-draft
fillmate import shared-pack.zip
fillmate export travel-request travel-request.zip
```

## Where things are stored

| What | Where (Linux; macOS/Windows use their usual folders) |
|---|---|
| Settings, profile, Google sign-in | `~/.config/fillmate/` (files readable only by you) |
| SMTP password | system keyring, otherwise `~/.config/fillmate/` |
| Installed packs, saved presets | `~/.local/share/fillmate/` |
| Filled documents | `~/Documents/Fillmate/<date>_<destination>/` |

`FILLMATE_HOME=/some/folder` keeps everything in a single folder instead.

DOCX previews, and attaching DOCX files as PDF, need
[LibreOffice](https://www.libreoffice.org/).

## Development

```bash
git clone https://github.com/leonardonels/fillmate && cd fillmate
python -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/pytest
.venv/bin/python tools/make_example_pack.py   # regenerate the example templates
.venv/bin/python tools/make_icons.py          # regenerate installer icons from the SVG
.venv/bin/python tools/demo.py                # open the app with made-up data
```

### Installers

Built with [Briefcase](https://briefcase.beeware.org) (config in `pyproject.toml`):

```bash
briefcase create linux system && briefcase build linux system && briefcase package linux system
```

Pushing a `v*` tag runs `.github/workflows/release.yml`. It builds the `.deb`, `.rpm`,
`.dmg` and `.msi` plus the PyPI package, and attaches them to a draft GitHub release.

## License

MIT
