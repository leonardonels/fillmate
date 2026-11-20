# Pack format (`pack.yaml`, version 1)

A pack is a folder with a `pack.yaml` and the templates it names. Every text value in
the file can use [Jinja](https://jinja.palletsprojects.com/templates/) syntax, such as
`{{ full_name }}` or `{% if notes %}…{% endif %}`. Templates run in a sandbox: a pack
cannot run code or read files outside its folder.

```yaml
fillmate: 1                 # required: format version
id: travel-request          # lowercase letters, digits, - and _; also the folder name once installed
name: Business travel request
version: "1.0"
description: One or two sentences shown in the app.
author: Your name
country: GB                 # public holidays for working_days_between() (ISO code; optional)
date_format: "%d/%m/%Y"     # how dates are written by default (strftime)
```

## Profile and fields

`profile` holds the details a person enters once, in Settings → Profile. They are shared
by all packs that use the same `id`, so use common ids (`full_name`, `email`, …) when
it makes sense. `fields` are asked every time the pack is filled in.

```yaml
profile:
  - {id: full_name, label: Full name, required: true}

fields:
  - {id: dates, label: Travel dates, type: dates, required: true}
  - {id: purpose, label: Purpose, type: multiline, default: Client meeting}
  - {id: transport, label: Transport, type: choice, options: [Train, Car, Plane]}
```

| Key | Meaning |
|---|---|
| `id` | Name used in templates. Letters, digits and `_`; it can't start with a digit |
| `label` | Shown in the app |
| `type` | `text` (default), `multiline`, `date`, `dates` (one or more days), `time` (`HH:MM`), `bool`, `choice` |
| `default` | Initial value. `date` fields accept `today` |
| `required` | Refuse to continue while it's empty |
| `placeholder`, `help` | Hint text and tooltip |
| `options` | Values offered by a `choice` field |

In templates, `date` values are dates, `dates` is a sorted list of dates, `bool` is
`true`/`false`, and everything else is text.

## Presets

Named sets of field values, such as places you often go to. Users can also save their
own presets from the app; those are stored outside the pack.

```yaml
presets:
  head-office: {destination: "1 Main Street, Leeds", transport: Train}
```

## Computed values and rules

```yaml
computed:                   # evaluated in order; each can use the ones before
  destination_short: "{{ destination.split(',')[0] }}"
  notice_days: "{{ working_days_between(requested_on, dates[0]) }}"

rules:
  - error_unless: "destination"           # a Jinja expression; stops with the message when false
    message: Enter a destination.
  - warn_if: "notice_days | int < 3"      # shows a warning but goes on
    message: "Only {{ notice_days }} working days' notice."
```

Computed values are always text. Use `| int` to compare numbers.

**Filters**: `date` (`{{ d | date }}` or `{{ d | date('%Y%m%d') }}`), `dates` (a list of
dates joined with `, `, or another separator: `dates(' / ')`), `slug` (safe for file
names), `first_word`, plus [Jinja's built-in
filters](https://jinja.palletsprojects.com/templates/#builtin-filters).
**Functions**: `today`, `working_days_between(a, b)` (working days strictly between
two dates, skipping weekends and the `country`'s holidays), `is_working_day(d)`.

## Documents

```yaml
documents:
  - id: request
    label: Travel request (PDF)            # tab title in the app
    template: travel_request.pdf           # path inside the pack
    output: "Request_{{ full_name | slug }}_{{ dates[0] | date('%Y%m%d') }}"   # file name; extension added
    fill: [...]
```

### PDF: text at a position (flat PDFs)

```yaml
- value: "{{ destination }}"
  page: 0                                  # first page = 0 (default)
  lines: [[140, 315, 545], [48, 340, 545]] # [x_start, baseline_y, x_end] per line, in points from the top-left
  size: 10                                 # starting font size (default 10)
  min_size: 7                              # stop shrinking here and report "too long" (default 7)
  font: helv                               # a PDF base font: helv, tiro, cour, hebo… (default helv)
```

Words flow into the next line when one is full. If the text still doesn't fit, the font
shrinks in 0.5 pt steps. To find coordinates, open the PDF in any viewer that shows
cursor positions in points (1 pt = 1/72 inch; A4 is 595 × 842). A calibration tool in
the app is planned.

### PDF: check marks

```yaml
- {mark: "transport == 'Train'", at: [142, 469], symbol: X, size: 11}
```

`mark` is an expression. The symbol is drawn at `at` (the text's baseline start) when
the expression is true.

### PDF: fillable form fields

```yaml
- {field: "Name", value: "{{ full_name }}"}
- {field: "Agree", value: "{{ agree }}"}   # checkboxes: true/yes/1/on/x ticks the box
```

To list a PDF's field names: `python -c "import pymupdf,sys; [print(w.field_name) for p in pymupdf.open(sys.argv[1]) for w in p.widgets()]" form.pdf`

### DOCX: placeholders

Write `{{ full_name }}`, `{% if notes %}…{% endif %}` and so on directly in the Word
document, then set:

```yaml
- id: notice
  template: notice.docx
  placeholders: true
```

Type each placeholder in one go. If Word splits it with formatting or spell-check
marks, it may not be recognised: retype it, or paste it as plain text. This uses
[docxtpl](https://docxtpl.readthedocs.io/), which also supports loops over table rows.

### DOCX: table cells

For documents you'd rather not edit (official templates), write into table cells and
keep the template's formatting:

```yaml
fill:
  - {cell: [0, 0, 1], value: "{{ full_name }}"}                 # [table, row, column], from 0
  - {cell: [0, 4, 1], run: 1, value: "{{ start_time }}"}        # only the 2nd run of the paragraph
  - {cell: [0, 5, 1], paragraph: 2, value: "{{ purpose }}"}     # 3rd paragraph of the cell
```

Without `run`, the value replaces the paragraph's text. `\n` in a value becomes a line
break. Both modes can be combined in one document.

## Output folder and email

```yaml
output_folder: "{{ dates[0] | date('%Y-%m-%d') }}_{{ destination_short | slug }}"

email:
  to: ["{{ manager_email }}"]
  cc: [hr@example.com]
  bcc: []
  subject: "Travel request – {{ full_name }} – {{ dates | dates }}"
  body: |
    Hello,
    ...
```

Recipients can come from profile values, and an empty result is dropped. The user can
edit everything before sending. In test mode the recipients are replaced by the test
addresses and the real ones are listed at the top of the body.

## Sharing

Settings → Form packs → **Export** zips the pack folder. Profile values, saved presets
and credentials live elsewhere, so they are never included. Check the templates
themselves before sharing: they are whatever files you put in the pack.
