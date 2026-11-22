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
| `type` | `text` (default), `multiline`, `date`, `dates` (one or more days), `time` (`HH:MM`), `bool`, `choice`, `table` (see [Tables](#tables)) |
| `default` | Initial value. `date` fields accept `today` |
| `required` | Refuse to continue while it's empty |
| `placeholder`, `help` | Hint text and tooltip |
| `options` | Values offered by a `choice` field |

In templates, `date` values are dates, `dates` is a sorted list of dates, `bool` is
`true`/`false`, a `table` is a list of rows, and everything else is text.

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
names), `first_word`, the table filters `total`, `days`, `hours` and `hhmm` (see
[Tables](#tables)), plus [Jinja's built-in
filters](https://jinja.palletsprojects.com/templates/#builtin-filters).
**Functions**: `today`, `working_days_between(a, b)` (working days strictly between
two dates, skipping weekends and the `country`'s holidays), `is_working_day(d)`,
`hours_between(start, end)` and `duration(value)`.

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
shrinks in 0.5 pt steps. Coordinates are in points (1 pt = 1/72 inch; A4 is
595 × 842).

You don't need to measure them yourself. In **Settings → Form packs**, use **Edit
positions…** (or **New pack from PDF…** for a new form). You draw a box over each blank
line, choose the value that goes there, and click boxes that should get a check mark.
Sample text shows how it will look. Saving updates `pack.yaml` and keeps your comments.

### PDF: tables

A ruled grid filled row by row from a [table field](#tables):

```yaml
- table: work                                 # a table field
  page: 0
  xs: [40, 110, 160, 210, 260, 470, 555]      # column edges, left to right
  ys: [214, 236, 258, 280, 302, 324]          # edges of the rows to fill, top to bottom (not the header)
  cols: [day, start, end, hours, activity, ~] # the column written in each grid column; ~ leaves one empty
  size: 9                                     # starting font size (default 9); min_size: default 6
  align: [center, center, center, center, left, left]   # optional; default: left for text, centred otherwise
  overflow: repeat_page                       # default; or "error" to refuse more rows than the grid has
```

Each cell works like a text box: the text is centred vertically, wraps onto a second line
when the row is tall enough, and shrinks to fit. Dates use the column's `format`, or the
pack's `date_format`.

With more rows than the grid has, the page is **repeated**. Every entry on that page is
drawn on each copy, and gets `sheet`: `sheet.rows` (the rows on that copy), `sheet.number`
and `sheet.count`. Per-page totals are ordinary text boxes:

```yaml
- {value: "{{ sheet.rows | days('day') }}", lines: [[142, 682, 200]]}      # days on this sheet
- {value: "{{ sheet.rows | total('hours') }}", lines: [[337, 682, 395]]}   # hours on this sheet
- {value: "{{ sheet.number }} of {{ sheet.count }}", lines: [[467, 682, 555]]}
```

On other pages, `sheet.rows` is every row. Only one table per document can repeat its page.

You don't need to measure a grid. In **Edit positions…**, press the grid button in the
toolbar, or drag over the table (a box drawn inside one of its rows works too), or
right-click inside it. fillmate snaps to its ruled lines and guesses the columns from the
header: dates, times, an hours column as `hours_between(start, end)`, and a signature column
left empty. Without ruled lines (or when the page's table is placed already), the grid
button gives you a grid to size: set the rows and columns, drag the
dividers, and drag an outer edge to stretch the whole grid. The totals appear in the list of
values for text boxes.

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

## Tables

A `table` field is a list of rows, such as the days of a timesheet. Its `columns` are
typed in the form, or computed by a `formula`:

```yaml
fields:
  - id: work
    label: Hours worked
    type: table
    required: true                     # at least one row
    columns:
      - {id: day, label: Date, type: date, format: "%d/%m"}
      - {id: start, label: From, type: time, default: "09:00"}
      - {id: end, label: To, type: time, default: "13:00"}
      - {id: hours, label: Hours, formula: "hours_between(start, end)"}
      - {id: activity, label: Activity}
```

| Column key | Meaning |
|---|---|
| `id`, `label` | As for fields. A formula refers to other columns by `id` |
| `type` | `text` (default), `date`, `time`, `number` (accepts `4,5`), `choice` (with `options`) |
| `formula` | A Jinja expression over the row's columns (and the form's other values). The column is computed, not typed |
| `format` | For dates: how they are written in the PDF (default: the pack's `date_format`) |
| `default` | The value a new row starts with |
| `required` | Every row needs it |

Formulas keep their type: `hours_between('09:00', '13:30')` is a **duration**, written
`4:30`, and durations add up. A formula that fails on a half-filled row leaves the cell
blank. On a complete row it is an error.

In templates, a table is a list of rows (dicts). These filters and functions help:

| | |
|---|---|
| `rows \| total('hours')` | Sum of a column, skipping blank cells. Durations stay durations (`106:00`) |
| `rows \| days('day')` | How many different days a date column covers (two rows on one day count once) |
| `d \| hours` | A duration as decimal hours: `4,5`. `hours(1, '.')` for one decimal and a point |
| `d \| hhmm` | A duration (or number of hours) as `4:30` |
| `hours_between(start, end)` | Time from start to end; an end before the start is on the next day |
| `duration(value)` | A duration from `4:30`, `4,5` or `4.5` |

For example, `computed: {total_hours: "{{ work | total('hours') }}"}` or a rule
`warn_if: "work | days('day') > 22"`.

In the app, a table is a small spreadsheet:
- Formula columns are computed as you type.
- Drag the fill handle (the square at the corner of the selection), or press Ctrl+D, to continue dates (working days, the same number of rows per day), numbers and patterns such as `09:00`, `14:00`.
- Paste cells from Excel or Sheets.
- **Add days…** adds a row for each working day in a range.

The rows are kept as a draft, outside the pack, until you press **Clear**. On the command
line, `--set work=@rows.csv` reads a CSV whose header names the columns (by id or label);
without it, the rows typed in the app are used.

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
