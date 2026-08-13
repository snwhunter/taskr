# taskr desktop app

Taskr is a small Tk desktop client for an existing Google Sheet. Python never
receives Google credentials: it calls a spreadsheet-bound Google Apps Script web
app over HTTPS. A local SQLite cache makes reads and edits immediate, while a
background sync keeps the Sheet authoritative and preserves queued edits during
temporary network failures.

The window title includes a launch version in `YYMMDDHHmmSS` format, making the
running instance straightforward to identify in screenshots and support notes.

## Existing architecture audit

Before this implementation the repository had **no Apps Script code, web-app
route, or HTTP client**. It directly accessed a worksheet using `gspread` and a
service account/Google ADC. Its table was called `db`, permitted reordered
headers, and put `Tags` at column 5, so it did not match the required existing
Sheet contract. The only reusable conventions were timestamp UUIDv7 IDs and the
status values blank/unstarted (previously serialized as `None`), `InProgress`,
`Blocked`, and `Complete`.

The minimal API added in `apps-script/Code.gs` has one diagnostic `GET` route and
one JSON `POST` route with these actions:

* `list` — returns every populated task row;
* `create` — validates and appends an exact 11-field row;
* `update` — replaces an identified row (an omitted `Tags` value is preserved);
* `complete` — changes only `Status` to `Complete`.

The Sheet remains the source of truth. Tasks are separated into three mode sheets:
`category0` is displayed as **ac** (work), `category1` as **vehicles**, and
`category2` as **home**. Each sheet must have these exact headers in row 1:

`ID, Parent, Task, Details, Required, Assigned, Priority, Status, Notes, Tags`

## Deploy the Apps Script

1. Open the spreadsheet and choose **Extensions → Apps Script**.
2. Copy `apps-script/Code.gs` into the bound script project and save it.
3. Choose **Deploy → New deployment → Web app**. Execute as the script owner and
   choose the access setting appropriate for the users of this desktop app.
4. Copy the deployment `/exec` URL. Deploy a new version after every server-code
   change; merely saving Apps Script does not update an existing deployment.

Apps Script validates the existing header rather than creating, renaming, or
reordering columns. `Tags` must contain a JSON object. Updates sent by this app
round-trip the existing dictionary, preserving all keys.

## Configure and run

Python 3.11+ and Tk are required. Install and run:

```bash
python -m pip install -e .
export TASKR_API_URL='https://script.google.com/macros/s/.../exec'
export TASKR_USER='your-name'
# Optional: override ~/.local/share/taskr/tasks.sqlite3
export TASKR_CACHE='/path/to/tasks.sqlite3'
python -m taskr
```

Alternatively create `~/.config/taskr/config.json`:

```json
{
  "api_url": "https://script.google.com/macros/s/.../exec",
  "user": "your-name",
  "assigned": []
}
```

Use **Add Tasks** to open the task-entry popup. There is no separate submit
button: selecting EOD, EOW, EOM, a future date, or no date creates the task.
Assigned history is saved to the config file for the editable autocomplete box.
Each view remembers its mode. The optional Parent pull-down stores the chosen
task's ID in the `Parent` column, creating a durable relationship. `Required` is
the task's required date. A new task initializes `Priority` as
`<target date>.<priority to parent>.<priority to view>`, with its target date
copied from `Required`. The two dates are independent after creation, so editing
`Required` does not overwrite an explicitly changed priority target. Status and
Notes start blank; provenance remains in Tags.

Each mode owns an independent collection that starts with five task views. The
**+ View**, **− View**, and **Edit View…** controls are grouped across the top;
the arrow buttons reorder the active view within its mode. The edit popup changes
the view name, date range, status, and visible columns together. Click the dropdown marker in any table
heading to search and select that column's visible values; filters from multiple
columns are combined. Use **Columns…** to show or hide fields independently in
each view. Each tab's name, visible columns, and filter selections are remembered
in `config.json`. Select multiple rows and use **Edit selected…** to apply the
same field edit to all selected tasks. Notes editing offers separate **Replace note** and
**Append edit** actions; appended text is prefixed with the user and timestamp.
Select a row and use
**Complete task**. **Set parent…** stores the selected parent ID in the child's
`Parent` field. A parent can be chosen from its row or the pull-down, and visible
children are nested below their parent. Task, Details, and Notes remain
left-aligned while the other columns are centered.

The toolbar shows whether synchronization is running, when it last succeeded,
or whether the app is offline and has pending changes. Refresh and every local
edit trigger a background sync; the UI continues to use the SQLite cache while
the Apps Script request is in flight. When synchronization fails, the status
includes the safe error summary. Use **Logs** to open a diagnostic panel with
copy and clear actions. Diagnostics are also retained in a rotating `taskr.log`
beside the SQLite cache; common credential values are redacted.

