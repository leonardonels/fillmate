# Changelog

## 0.2.1 (2026-11-22)

- **Signatures** (Settings → Profile → Signatures): add them from photos or scans (the paper becomes transparent
  and the margins are trimmed), or cut one out of a PDF. Several images of one signature take turns, row after row.
  They stay on your computer and are never part of a pack or a pack export.
- A `signature` field type (usually in the profile) and a `sign` entry place a signature in a box on a PDF. A
  `signature` table column signs each row: ✓ in the form, toggled by a click or Space, and copied by the fill handle.
- Positions editor: right-click → Add a signature box (drag its corner to resize it), or drag a tall box and
  choose "A signature box". Table columns can be of kind Signature, and "Firma", "Signature" and "Unterschrift"
  headers are guessed as one.
- **Saved forms**: Create documents also writes a `.fillmate` file with what you typed, next to the documents.
  The drawer lists the latest ones. **Open…** loads one to change it or to start the next month from it, and
  **Save as…** writes one anywhere, to keep or hand over. `fillmate fill <pack> --from file.fillmate`.
- **Save a copy…** (toolbar) copies the created documents to a folder you choose.
- Tables: Import and Export CSV from the ⋯ button.
- The example timesheet signs each row and the employee signature line.

## 0.2.0 (2026-11-22)

- **Tables** for timesheets and other ruled grids: a `table` field with typed columns and formula columns
  (e.g. `hours_between(start, end)`), filled row by row into the PDF's grid.
- Rows that don't fit repeat the page. Text on that page is drawn on each copy, and `sheet.rows`, `sheet.number`
  and `sheet.count` give per-page totals ("Tot. ore per pagina") and "Sheet 1 of 2".
- New filters and functions: `total('column')`, `days('column')`, `hours` (4:30 → 4,5), `hhmm`,
  `hours_between()`, `duration()`. Durations print as H:MM and add up.
- Positions editor: press the grid button, drag over a table, or right-click in it, and fillmate snaps to its lines, guesses the
  columns from the header (dates, times, hours as a formula, signature left empty), and offers the totals as
  values. Without ruled lines you get a grid to size: set rows and columns, drag dividers, stretch the edges.
- In the form a table is a small spreadsheet. The fill handle and Ctrl+D continue dates (working days, same rows
  per day), numbers and patterns. Copy and paste work with Excel and Sheets. "Add days…" adds a row for each
  working day in a range, and "Expand" opens a larger window.
- Table rows are kept as a draft until you clear them, so a monthly timesheet can be filled day by day.
- Times can be typed as 9, 930, 9.30 or 9:30.
- Command line: `--set work=@rows.csv` fills a table from a CSV file. Without it, the rows typed in the app are
  used.
- Second example pack: a monthly timesheet.

## 0.1.1 (2026-11-22)

- Dark theme: the drawer button's icon turns light with the rest of the toolbar.
- README screenshots, taken from the demo with made-up data.
- Published on PyPI: `pipx install fillmate`.

## 0.1.0 (2026-11-22)

- First version: form packs (`pack.yaml`), fills flat and fillable PDFs and DOCX (placeholders or table cells).
- Desktop app with previews, presets, rules and warnings, test mode.
- Email via Gmail (Google sign-in), SMTP, or a draft in your mail app.
- Calibration tool: create a pack from a PDF and place values by drawing on the page.
- Command line: `fillmate fill / import / export / fields / profile`.
- New look, "Ink and hand": dot-grid paper, what you typed in ink, fillmate's notes in red pen, and Liquid Glass
  toolbar, sidebar and send check. Yes/no fields are circled, short choices are tick boxes, settings use switches.
- Test mode is on for new users. Settings save as you change them.
- Zoom the preview (Ctrl + wheel, Ctrl +/−, or the zoom controls).
- New pack from a Word document: its {{ placeholders }} become fields, and new ones are picked up after editing.
- Positions editor: no modes (drag or double-click to add a box, right-click for check marks), values picked by
  name, no sample text. Fields made there are only saved with the positions; unused fields can be removed.
- Add or remove documents in a pack (Settings → Form packs); remove a pack from there or by right-clicking it.
- The positions editor has the new look too: dot-grid paper, glass toolbar and values panel, hints in red pen,
  glass right-click menu. A pack's ⋮ menu opens each of its documents directly.
- Questions, notices and name prompts are drawn on the app's own paper instead of the system's message box. Removing a
  pack's only document offers to remove the pack. "Add a line below" is gone from the positions editor.
- No system title bar: round corners, and window buttons per platform (macOS traffic lights inside the sidebar,
  a glass minimize/maximize/close capsule on Windows, round glass buttons on Linux following the desktop's
  button layout). Drag empty toolbar space to move, double-click it to maximize, resize from any edge.
- Buttons are animated: hover light fades in and out, glass buttons brighten and swell when pressed, switch
  knobs slide, and the selection pill glides between options and fades in on the sidebar.
- The output tabs stay between the title and the buttons: long document names are shortened (whole name on
  hover) instead of sliding under the toolbar buttons.
- Dark theme: smoky glass that stays dark over the white page of a preview, fainter rims and selection pills,
  a yellow highlighter with dark text, and visible edges around grouped settings.
- The dot grid is replaced by a print room: the open pack's name in huge faint Archivo Expanded over the previews,
  a rubber stamp for where the request stands (draft, filled, sent), a registration mark, a ruler, a test-mode
  label along the edge and a colour bar (label, ink, red pen). Other windows get plain paper. The dark theme's ground and paper are warmer.
- New app icon in Liquid Glass layers: a tilted frosted sheet filled in by hand under the highlighter, and the
  fountain pen that wrote it, on a paper tile.
- Create documents is a glass capsule floating over the form (it stays in view when a long form scrolls under it).
  Glass lying on the window has a faint contact edge instead of a drop shadow; only popovers cast a soft one.
  The send check's Send is a flat ink button, switches turn ink blue when on, and focus rings show for keyboard
  focus only.
- Two columns: the previews on the left, the form (with its title) on the right, scrolling as one. Packs, presets
  and settings moved to a glass drawer (the top-left button or Ctrl+B; Esc closes it). New previews open with the
  page's top already under the tab bar's glass.
- macOS: the traffic lights sit on the toolbar row, before the drawer button.
- macOS installer: a "drag to install" window drawn in the app's ink, with the standard disk icon. The .dmg is
  no longer signed, so Gatekeeper asks about the app once instead of the disk image as well.
- Requires PySide6 below 6.12: 6.12 releases Python's None once too often on Python 3.10–3.11 and the app aborts.
