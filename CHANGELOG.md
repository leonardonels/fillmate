# Changelog

## 0.1.0 (unreleased)

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
- Requires PySide6 below 6.12: 6.12 releases Python's None once too often on Python 3.10–3.11 and the app aborts.
