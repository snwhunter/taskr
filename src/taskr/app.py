"""Tk desktop UI for adding and viewing Tasks."""

from __future__ import annotations

import calendar
from dataclasses import replace
from datetime import date, datetime, timedelta
import logging
from logging.handlers import RotatingFileHandler
import queue
import re
import threading
import tkinter as tk
from tkinter import messagebox, simpledialog, ttk

from taskr.models.task import Status, Task, VISIBLE_COLUMNS, creation_timestamp_id
from taskr.storage.apps_script import AppsScriptTaskStore
from taskr.storage.config import AppConfig, ViewConfig, default_cache_path
from taskr.storage.sqlite import SQLiteTaskStore


# A launch/build-style version keeps the lightweight desktop client free from a
# separate release-numbering system while still making a running instance easy
# to identify in screenshots and support requests.
APP_VERSION = creation_timestamp_id()
TABLE_COLUMNS = tuple(column for column in VISIBLE_COLUMNS if column != "Task")
LOGGER = logging.getLogger("taskr")


def safe_error(error: BaseException) -> str:
    """Return a useful error description without leaking common credentials."""
    message = f"{type(error).__name__}: {error}".strip()
    patterns = (
        (r"(?i)(authorization\s*[:=]\s*)(?:bearer\s+)?[^\s,;]+", r"\1[redacted]"),
        (r"(?i)(api[_-]?key|token|password|secret)(\s*[:=]\s*)[^\s,;&]+", r"\1\2[redacted]"),
        (r"([?&](?:key|token|password|secret)=)[^&\s]+", r"\1[redacted]"),
    )
    for pattern, replacement in patterns:
        message = re.sub(pattern, replacement, message)
    return message


def configure_logging(path) -> None:
    """Persist diagnostics across launches without allowing unbounded growth."""
    if LOGGER.handlers: return
    path.parent.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(path, maxBytes=512_000, backupCount=2, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    LOGGER.addHandler(handler)
    LOGGER.setLevel(logging.INFO)
    LOGGER.propagate = False


def window_title(version: str = APP_VERSION) -> str:
    return f"taskr - version: {version}"


def target_date(kind: str, today: date | None = None) -> date | None:
    today = today or date.today()
    if kind == "EOD": return today
    if kind == "EOW": return today + timedelta(days=6 - today.weekday())
    if kind == "EOM": return date(today.year, today.month, calendar.monthrange(today.year, today.month)[1])
    return None


def task_matches(task: Task, view: ViewConfig) -> bool:
    """Return whether a task belongs in a configured view."""
    try:
        start = date.fromisoformat(view.date_from) if view.date_from else None
        end = date.fromisoformat(view.date_to) if view.date_to else None
    except ValueError as error:
        raise ValueError("Dates must use YYYY-MM-DD.") from error
    if (
        task.mode != view.mode
        or (view.status and task.status.value != view.status)
        or (task.required and ((start and task.required < start) or (end and task.required > end)))
    ):
        return False
    record = task.to_record()
    return all(record.get(column, "") in selected
               for column, selected in view.column_filters.items())


def appended_note(existing: str, addition: str, user: str, now: datetime | None = None) -> str:
    """Append an attributed, locally timestamped entry to a Notes value."""
    stamp = (now or datetime.now()).strftime("%Y-%m-%d %H:%M")
    entry = f"[{user or 'Unknown'} {stamp}] {addition}"
    return f"{existing.rstrip()}\n{entry}" if existing.strip() else entry


class NotesEditDialog(tk.Toplevel):
    """Offer explicit replace and attributed-append actions for Notes."""

    def __init__(self, parent: tk.Misc, initial: str, user: str) -> None:
        super().__init__(parent)
        self.result: tuple[str, str] | None = None
        self.initial, self.user = initial, user
        self.title("Edit notes"); self.transient(parent.winfo_toplevel()); self.grab_set()
        body = ttk.Frame(self, padding=12); body.pack(fill="both", expand=True)
        ttk.Label(body, text="Notes").pack(anchor="w")
        self.editor = tk.Text(body, width=64, height=10, wrap="word")
        self.editor.pack(fill="both", expand=True, pady=(4, 10)); self.editor.insert("1.0", initial)
        actions = ttk.Frame(body); actions.pack(fill="x")
        ttk.Button(actions, text="Cancel", command=self.destroy).pack(side="right")
        ttk.Button(actions, text="Append edit", command=self.append).pack(side="right", padx=6)
        ttk.Button(actions, text="Replace note", command=self.replace).pack(side="right")
        self.editor.focus_set()

    def replace(self) -> None:
        self.result = ("replace", self.editor.get("1.0", "end-1c"))
        self.destroy()

    def append(self) -> None:
        addition = self.editor.get("1.0", "end-1c")
        # When the original text is still present, treat only newly entered
        # text as the append payload. Selecting all and typing also works.
        if addition.startswith(self.initial): addition = addition[len(self.initial):].lstrip("\n")
        if not addition.strip():
            messagebox.showinfo("Append notes", "Enter text to append.", parent=self); return
        self.result = ("append", addition)
        self.destroy()

    @classmethod
    def choose(cls, parent: tk.Misc, initial: str, user: str) -> tuple[str, str] | None:
        dialog = cls(parent, initial, user); parent.wait_window(dialog); return dialog.result


class ColumnVisibilityDialog(tk.Toplevel):
    """Select the columns displayed by one view."""

    def __init__(self, parent: ViewPane) -> None:
        super().__init__(parent)
        self.parent = parent
        self.title("Show / hide columns"); self.transient(parent.winfo_toplevel()); self.grab_set()
        body = ttk.Frame(self, padding=12); body.pack(fill="both", expand=True)
        ttk.Label(body, text="Columns shown in this view").pack(anchor="w", pady=(0, 6))
        self.values: dict[str, tk.BooleanVar] = {}
        visible = set(parent.settings.visible_columns)
        for column in VISIBLE_COLUMNS:
            value = tk.BooleanVar(value=column in visible); self.values[column] = value
            ttk.Checkbutton(body, text=column, variable=value).pack(anchor="w")
        actions = ttk.Frame(body); actions.pack(fill="x", pady=(10, 0))
        ttk.Button(actions, text="Cancel", command=self.destroy).pack(side="right")
        ttk.Button(actions, text="Apply", command=self.accept).pack(side="right", padx=6)

    def accept(self) -> None:
        selected = [name for name in VISIBLE_COLUMNS if self.values[name].get()]
        if not selected:
            messagebox.showinfo("Columns", "Show at least one column.", parent=self); return
        self.parent.settings.visible_columns = selected
        self.parent.apply_visible_columns(); self.parent.app.save_views(); self.destroy()


class ColumnFilterDialog(tk.Toplevel):
    """Spreadsheet-style, searchable checkbox filter for one table column."""

    def __init__(self, parent: ViewPane, column: str, values: list[str], selected: list[str] | None) -> None:
        super().__init__(parent)
        self.parent, self.column, self.values = parent, column, values
        self.title(f"Filter {column}"); self.transient(parent.winfo_toplevel()); self.grab_set()
        self.resizable(False, True)
        body = ttk.Frame(self, padding=12); body.pack(fill="both", expand=True)
        self.search = tk.StringVar(); search = ttk.Entry(body, textvariable=self.search, width=34)
        search.pack(fill="x", pady=(0, 8)); search.bind("<KeyRelease>", self._show)
        self.listbox = tk.Listbox(body, selectmode="multiple", exportselection=False, height=12, width=38)
        self.listbox.pack(fill="both", expand=True)
        self.initial = set(values if selected is None else selected)
        actions = ttk.Frame(body); actions.pack(fill="x", pady=(10, 0))
        ttk.Button(actions, text="Cancel", command=self.destroy).pack(side="right")
        ttk.Button(actions, text="OK", command=self.accept).pack(side="right", padx=(0, 8))
        ttk.Button(actions, text="Select all", command=lambda: self.listbox.selection_set(0, "end")).pack(side="left")
        ttk.Button(actions, text="Clear", command=lambda: self.listbox.selection_clear(0, "end")).pack(side="left", padx=4)
        self._show(); search.focus_set()

    def _show(self, _event: tk.Event | None = None) -> None:
        # Retain selections while narrowing or widening the search.
        if self.listbox.size():
            displayed = list(self.listbox.get(0, "end"))
            for index in self.listbox.curselection(): self.initial.add(displayed[index])
            for index, value in enumerate(displayed):
                if index not in self.listbox.curselection(): self.initial.discard(value)
        needle = self.search.get().casefold()
        self.listbox.delete(0, "end")
        for value in (item for item in self.values if needle in item.casefold()):
            self.listbox.insert("end", value)
            if value in self.initial: self.listbox.selection_set("end")

    def accept(self) -> None:
        displayed = list(self.listbox.get(0, "end"))
        for index, value in enumerate(displayed):
            if index in self.listbox.curselection(): self.initial.add(value)
            else: self.initial.discard(value)
        self.parent.set_column_filter(self.column, sorted(self.initial, key=str.casefold))
        self.destroy()


class ViewPane(ttk.Frame):
    def __init__(self, app: TaskrApp, notebook: ttk.Notebook, settings: ViewConfig) -> None:
        super().__init__(notebook, padding=8)
        self.app, self.settings = app, settings
        self.table = ttk.Treeview(self, columns=TABLE_COLUMNS, show="tree headings", selectmode="extended")
        self.table.heading("#0", text="Task", command=lambda: self.open_filter("Task"))
        self.table.column("#0", width=240, anchor="w")
        for name in TABLE_COLUMNS:
            self.table.heading(name, text=name, command=lambda column=name: self.open_filter(column))
            self.table.column(name, width=100, anchor="w" if name in ("Details", "Notes") else "center")
        self.table.pack(fill="both", expand=True, pady=8)
        self.apply_visible_columns()
        self.table.bind("<Double-1>", self.edit_cell)
        buttons = ttk.Frame(self); buttons.pack(fill="x")
        ttk.Button(buttons, text="Refresh", command=app.refresh).pack(side="left")
        ttk.Button(buttons, text="Columnsâ€¦", command=lambda: ColumnVisibilityDialog(self)).pack(side="left", padx=6)
        ttk.Button(buttons, text="Edit selectedâ€¦", command=self.edit_selected).pack(side="left")
        ttk.Button(buttons, text="Set parentâ€¦", command=self.set_parent).pack(side="right")
        ttk.Button(buttons, text="Complete task", command=self.complete).pack(side="right", padx=6)

    def apply_visible_columns(self) -> None:
        valid = [name for name in VISIBLE_COLUMNS if name in self.settings.visible_columns]
        if not valid:
            valid = ["Task"]
            self.settings.visible_columns = valid
        visible = set(valid)
        self.table.configure(displaycolumns=[name for name in TABLE_COLUMNS if name in visible])
        if "Task" in visible:
            self.table.configure(show="tree headings"); self.table.column("#0", width=240, stretch=True)
        else:
            self.table.configure(show="headings"); self.table.column("#0", width=0, stretch=False)

    def open_filter(self, column: str) -> None:
        values = sorted({task.to_record()[column] for task in self.app.tasks}, key=str.casefold)
        selected = self.settings.column_filters.get(column)
        ColumnFilterDialog(self, column, values, selected)

    def set_column_filter(self, column: str, selected: list[str]) -> None:
        all_values = {task.to_record()[column] for task in self.app.tasks}
        if set(selected) == all_values:
            self.settings.column_filters.pop(column, None)
        else:
            self.settings.column_filters[column] = selected
        self.app.save_views(); self.render(); self.update_headings()

    def update_headings(self) -> None:
        for name in VISIBLE_COLUMNS:
            marker = " â–¼" if name in self.settings.column_filters else " â–¾"
            self.table.heading("#0" if name == "Task" else name, text=name + marker)

    def rename(self) -> None:
        value = simpledialog.askstring("Rename view", "View name:", initialvalue=self.settings.name, parent=self)
        if value and value.strip():
            self.settings.name = value.strip(); self.app.tabs.tab(self, text=self.settings.name); self.app.save_views()

    def render(self) -> None:
        self.update_headings()
        self.table.delete(*self.table.get_children())
        try: visible = [task for task in self.app.tasks if task_matches(task, self.settings)]
        except ValueError as error: messagebox.showerror("Invalid filter", str(error)); return
        ids = {task.id for task in visible}
        pending = list(visible)
        # Insert each generation after its parent. Broken/cyclic references safely
        # fall back to the root rather than hiding a task.
        ordered: list[tuple[Task, str]] = []
        inserted: set[str] = set()
        while pending:
            ready = [task for task in pending
                     if not task.parent or task.parent not in ids or task.parent in inserted]
            if not ready: ready = pending[:]
            for task in ready:
                parent = task.parent
                ordered.append((task, parent if parent in inserted else ""))
                inserted.add(task.id); pending.remove(task)
        for task, tree_parent in ordered:
            record = task.to_record()
            self.table.insert(tree_parent, "end", iid=task.id,
                              text=record["Task"],
                              values=[record[name] for name in TABLE_COLUMNS])
            if tree_parent: self.table.item(tree_parent, open=True)

    def selected_task(self) -> Task | None:
        selected = self.table.selection()
        return next((task for task in self.app.tasks if selected and task.id == selected[0]), None)

    def selected_tasks(self) -> list[Task]:
        selected = set(self.table.selection())
        return [task for task in self.app.tasks if task.id in selected]

    def edit_cell(self, event: tk.Event) -> None:
        task_id, column = self.table.identify_row(event.y), self.table.identify_column(event.x)
        if not task_id or not column: return
        displayed = [name for name in TABLE_COLUMNS if name in self.settings.visible_columns]
        name = "Task" if column == "#0" else displayed[int(column[1:]) - 1]
        if name == "ID": return
        task = next(item for item in self.app.tasks if item.id == task_id)
        tasks = self.selected_tasks() if task_id in self.table.selection() else [task]
        self._edit_tasks(tasks, name, task.to_record()[name])

    def edit_selected(self) -> None:
        tasks = self.selected_tasks()
        if not tasks:
            messagebox.showinfo("Edit tasks", "Select one or more tasks first."); return
        choices = ", ".join(name for name in VISIBLE_COLUMNS if name != "ID")
        name = simpledialog.askstring("Edit selected tasks", f"Column to edit:\n{choices}", parent=self)
        if not name: return
        name = next((item for item in VISIBLE_COLUMNS if item.casefold() == name.strip().casefold()), "")
        if not name or name == "ID":
            messagebox.showerror("Edit tasks", "Enter one of the listed column names."); return
        self._edit_tasks(tasks, name, tasks[0].to_record()[name])

    def _edit_tasks(self, tasks: list[Task], name: str, initial: str) -> None:
        note_edit = NotesEditDialog.choose(self, initial, self.app.config.user) if name == "Notes" else None
        value = (None if name == "Notes" else
                 simpledialog.askstring("Edit tasks" if len(tasks) > 1 else "Edit task",
                                        name, initialvalue=initial, parent=self))
        if (name == "Notes" and note_edit is None) or (name != "Notes" and value is None): return
        converted: object = note_edit[1] if note_edit else value
        try:
            if name == "Required": converted = date.fromisoformat(value) if value else None
            if name == "Status": converted = Status(value)
            for selected_task in tasks:
                selected_value = converted
                if name == "Notes" and note_edit and note_edit[0] == "append":
                    selected_value = appended_note(selected_task.notes, note_edit[1], self.app.config.user)
                self.app.store.update(replace(selected_task, **{name.lower(): selected_value}))
            self.app.refresh()
        except Exception as error: messagebox.showerror("Update failed", str(error))

    def complete(self) -> None:
        task = self.selected_task()
        if not task: messagebox.showinfo("Complete", "Select a task first."); return
        try: self.app.store.complete(task.id); self.app.refresh()
        except Exception as error: messagebox.showerror("Complete failed", str(error))

    def set_parent(self) -> None:
        task = self.selected_task()
        if not task: messagebox.showinfo("Set parent", "Select a child task first."); return
        candidates = [item for item in self.app.tasks if item.id != task.id]
        parent_id = ParentTaskDialog.choose(self, candidates, task.parent)
        if parent_id is None: return
        try: self.app.store.update(replace(task, parent=parent_id)); self.app.refresh()
        except Exception as error: messagebox.showerror("Update failed", str(error))


class ParentTaskDialog(tk.Toplevel):
    """Choose a task from either a compact pull-down or a clickable row list."""

    def __init__(self, parent: tk.Misc, tasks: list[Task], initial: str = "") -> None:
        super().__init__(parent)
        self.result: str | None = None
        self.tasks = tasks
        self.title("Set parent"); self.transient(parent.winfo_toplevel()); self.grab_set()
        body = ttk.Frame(self, padding=12); body.pack(fill="both", expand=True)
        ttk.Label(body, text="Parent task (blank removes parent)").pack(anchor="w")
        self.labels = [""] + [f"{task.task} â€” {task.id}" for task in tasks]
        self.ids = [""] + [task.id for task in tasks]
        self.choice = ttk.Combobox(body, values=self.labels, state="readonly", width=64)
        self.choice.pack(fill="x", pady=(4, 10)); self.choice.current(self.ids.index(initial) if initial in self.ids else 0)
        table = ttk.Treeview(body, columns=("Task", "ID"), show="headings", height=min(10, max(3, len(tasks))))
        table.heading("Task", text="Task"); table.heading("ID", text="ID")
        table.column("Task", width=340, anchor="w"); table.column("ID", width=130, anchor="center")
        for task in tasks: table.insert("", "end", iid=task.id, values=(task.task, task.id))
        table.pack(fill="both", expand=True)
        table.bind("<<TreeviewSelect>>", lambda _event: self.choice.current(self.ids.index(table.selection()[0])))
        table.bind("<Double-1>", lambda _event: self.accept())
        actions = ttk.Frame(body); actions.pack(fill="x", pady=(10, 0))
        ttk.Button(actions, text="Cancel", command=self.destroy).pack(side="right")
        ttk.Button(actions, text="Set parent", command=self.accept).pack(side="right", padx=6)

    def accept(self) -> None:
        self.result = self.ids[self.choice.current()]
        self.destroy()

    @classmethod
    def choose(cls, parent: tk.Misc, tasks: list[Task], initial: str = "") -> str | None:
        dialog = cls(parent, tasks, initial)
        parent.wait_window(dialog)
        return dialog.result


class ViewConfigDialog(tk.Toplevel):
    """Edit the configuration of the active view in one popup."""

    def __init__(self, parent: ViewPane) -> None:
        super().__init__(parent)
        self.pane = parent; settings = parent.settings
        self.title("Edit view"); self.transient(parent.winfo_toplevel()); self.grab_set()
        body = ttk.Frame(self, padding=12); body.pack(fill="both", expand=True)
        self.name = tk.StringVar(value=settings.name)
        self.date_from = tk.StringVar(value=settings.date_from)
        self.date_to = tk.StringVar(value=settings.date_to)
        self.status = tk.StringVar(value=settings.status)
        for row, (label, variable) in enumerate((("Name", self.name), ("Date from", self.date_from),
                                                  ("Date to", self.date_to), ("Status", self.status))):
            ttk.Label(body, text=label).grid(row=row, column=0, sticky="w", pady=3)
            if label == "Status":
                widget = ttk.Combobox(body, textvariable=variable, state="readonly",
                                      values=("", "InProgress", "Blocked", "Complete"))
            else: widget = ttk.Entry(body, textvariable=variable, width=32)
            widget.grid(row=row, column=1, sticky="ew", pady=3)
        ttk.Label(body, text="Dates use YYYY-MM-DD; blank means no limit.").grid(
            row=4, column=0, columnspan=2, sticky="w", pady=(0, 8))
        ttk.Label(body, text="Visible columns").grid(row=5, column=0, columnspan=2, sticky="w")
        self.columns: dict[str, tk.BooleanVar] = {}
        visible = set(settings.visible_columns)
        columns = ttk.Frame(body); columns.grid(row=6, column=0, columnspan=2, sticky="w")
        for index, column in enumerate(VISIBLE_COLUMNS):
            value = tk.BooleanVar(value=column in visible); self.columns[column] = value
            ttk.Checkbutton(columns, text=column, variable=value).grid(
                row=index // 3, column=index % 3, sticky="w", padx=(0, 12))
        actions = ttk.Frame(body); actions.grid(row=7, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        ttk.Button(actions, text="Cancel", command=self.destroy).pack(side="right")
        ttk.Button(actions, text="Apply", command=self.accept).pack(side="right", padx=6)
        body.columnconfigure(1, weight=1)

    def accept(self) -> None:
        name = self.name.get().strip()
        selected = [column for column in VISIBLE_COLUMNS if self.columns[column].get()]
        if not name or not selected:
            messagebox.showinfo("Edit view", "Enter a name and show at least one column.", parent=self); return
        try:
            for value in (self.date_from.get().strip(), self.date_to.get().strip()):
                if value: date.fromisoformat(value)
        except ValueError:
            messagebox.showerror("Edit view", "Dates must use YYYY-MM-DD.", parent=self); return
        settings = self.pane.settings
        settings.name, settings.date_from, settings.date_to = name, self.date_from.get().strip(), self.date_to.get().strip()
        settings.status, settings.visible_columns = self.status.get(), selected
        self.pane.app.tabs.tab(self.pane, text=name)
        self.pane.apply_visible_columns(); self.pane.render(); self.pane.app.save_views(); self.destroy()


class TaskrApp(ttk.Frame):
    def __init__(self, master: tk.Tk, config: AppConfig, store: SQLiteTaskStore) -> None:
        super().__init__(master, padding=10); self.pack(fill="both", expand=True)
        self.config, self.store, self.tasks = config, store, []
        configure_logging(store.path.with_name("taskr.log"))
        master.title(window_title()); master.geometry("1180x650")
        toolbar = ttk.Frame(self); toolbar.pack(fill="x", pady=(0, 8))
        ttk.Button(toolbar, text="Add Tasks", command=self.open_add_tasks).pack(side="left")
        ttk.Label(toolbar, text="Mode").pack(side="left", padx=(12, 4))
        mode_names = {"category0": "ac", "category1": "vehicles", "category2": "home"}
        current_mode = "category0"
        self.mode = tk.StringVar(value=mode_names.get(current_mode, "ac"))
        mode_box = ttk.Combobox(toolbar, textvariable=self.mode, state="readonly", width=12,
                                values=("ac", "vehicles", "home"))
        mode_box.pack(side="left"); mode_box.bind("<<ComboboxSelected>>", self.change_mode)
        ttk.Button(toolbar, text="+ View", command=self.add_view).pack(side="left", padx=6)
        ttk.Button(toolbar, text="âˆ’ View", command=self.remove_view).pack(side="left")
        ttk.Button(toolbar, text="Edit Viewâ€¦", command=self.edit_view).pack(side="left", padx=6)
        ttk.Button(toolbar, text="â—€", command=lambda: self.reorder_view(-1)).pack(side="left")
        ttk.Button(toolbar, text="â–¶", command=lambda: self.reorder_view(1)).pack(side="left", padx=(2, 6))
        self.sync_text = tk.StringVar(value="Sync: checkingâ€¦")
        ttk.Label(toolbar, textvariable=self.sync_text).pack(side="right")
        ttk.Button(toolbar, text="Logs", command=self.toggle_logs).pack(side="right", padx=(0, 8))
        self.tabs = ttk.Notebook(self); self.tabs.pack(fill="both", expand=True)
        self.tabs.bind("<<NotebookTabChanged>>", self.show_view_mode)
        self.views: list[ViewPane] = []
        self.current_mode = current_mode
        self._load_mode_views()
        self.log_panel = ttk.Frame(self, padding=(0, 8, 0, 0))
        log_actions = ttk.Frame(self.log_panel); log_actions.pack(fill="x")
        ttk.Label(log_actions, text="Diagnostics").pack(side="left")
        ttk.Button(log_actions, text="Copy", command=self.copy_logs).pack(side="right")
        ttk.Button(log_actions, text="Clear", command=self.clear_logs).pack(side="right", padx=6)
        self.log_text = tk.Text(self.log_panel, height=8, wrap="word", state="disabled")
        self.log_text.pack(fill="both", expand=True, pady=(4, 0))
        self._log("INFO", f"Taskr {APP_VERSION} started; log file: {store.path.with_name('taskr.log')}")
        self.refresh()

    def toggle_logs(self) -> None:
        if self.log_panel.winfo_manager(): self.log_panel.pack_forget()
        else: self.log_panel.pack(fill="both", expand=False)

    def _log(self, level: str, message: str) -> None:
        message = safe_error(Exception(message)).removeprefix("Exception: ")
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        LOGGER.log(getattr(logging, level), message)
        self.log_text.configure(state="normal")
        self.log_text.insert("end", f"{stamp} {level} {message}\n")
        self.log_text.see("end"); self.log_text.configure(state="disabled")

    def clear_logs(self) -> None:
        self.log_text.configure(state="normal"); self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")

    def copy_logs(self) -> None:
        self.clipboard_clear(); self.clipboard_append(self.log_text.get("1.0", "end-1c"))

    def change_mode(self, _event: tk.Event | None = None) -> None:
        names = {"ac": "category0", "vehicles": "category1", "home": "category2"}
        self.current_mode = names[self.mode.get()]
        self._load_mode_views(); self.refresh()

    def show_view_mode(self, _event: tk.Event | None = None) -> None:
        if not self.views: return
        return

    def _load_mode_views(self) -> None:
        for tab in self.tabs.tabs(): self.tabs.forget(tab)
        self.views.clear()
        for settings in self.config.views_for(self.current_mode): self._append_view(settings)

    def _append_view(self, settings: ViewConfig) -> None:
        pane = ViewPane(self, self.tabs, settings); self.views.append(pane); self.tabs.add(pane, text=settings.name)

    def add_view(self) -> None:
        settings = ViewConfig(name=f"View {len(self.views) + 1}", mode=self.current_mode)
        self.config.views_for(self.current_mode).append(settings)
        self._append_view(settings); self.tabs.select(self.views[-1]); self.save_views()

    def remove_view(self) -> None:
        if len(self.views) == 1: messagebox.showinfo("Remove view", "At least one view is required."); return
        index = self.tabs.index("current"); self.tabs.forget(index); self.views.pop(index)
        self.config.views_for(self.current_mode).pop(index); self.save_views()

    def rename_view(self) -> None:
        self.views[self.tabs.index("current")].rename()

    def edit_view(self) -> None:
        ViewConfigDialog(self.views[self.tabs.index("current")])

    def reorder_view(self, offset: int) -> None:
        old_index = self.tabs.index("current")
        new_index = old_index + offset
        if not 0 <= new_index < len(self.views): return
        self.config.reorder_view(self.current_mode, old_index, new_index)
        pane = self.views.pop(old_index); self.views.insert(new_index, pane)
        self.tabs.insert(new_index, pane); self.tabs.select(pane); self.save_views()

    def save_views(self) -> None:
        try: self.config.save()
        except OSError as error: messagebox.showerror("Save failed", str(error))

    def open_add_tasks(self) -> None:
        window = tk.Toplevel(self); window.title("Add Tasks"); window.transient(self.winfo_toplevel()); window.grab_set()
        body = ttk.Frame(window, padding=12); body.pack(fill="both", expand=True)
        inputs: dict[str, object] = {}
        active_view = self.views[self.tabs.index("current")].settings
        ttk.Label(body, text="Assigned").grid(row=0, column=0, sticky="w", pady=4)
        assigned = ttk.Combobox(body, values=self.config.assigned, width=55)
        assigned.grid(row=0, column=1, sticky="ew"); inputs["Assigned"] = assigned
        ttk.Label(body, text="Parent").grid(row=1, column=0, sticky="w", pady=4)
        parent_tasks = [task for task in self.tasks if task_matches(task, active_view)]
        parent_labels = [""] + [f"{task.task} â€” {task.id}" for task in parent_tasks]
        parent = ttk.Combobox(body, values=parent_labels, state="readonly", width=55)
        parent.grid(row=1, column=1, sticky="ew"); parent.current(0); inputs["Parent"] = parent
        for row, label in enumerate(("Task", "Details"), 2):
            ttk.Label(body, text=label).grid(row=row, column=0, sticky="nw", pady=4)
            widget = tk.Text(body, height=2 if label == "Task" else 5, width=55); widget.grid(row=row, column=1, sticky="ew"); inputs[label] = widget
        ttk.Label(body, text="Required").grid(row=4, column=0, sticky="w")
        buttons = ttk.Frame(body); buttons.grid(row=4, column=1, sticky="w", pady=8)

        def create_for(required: date | None) -> None:
            try:
                values = {"assigned": inputs["Assigned"].get().strip(), "mode": active_view.mode}
                values["task"] = inputs["Task"].get("1.0", "end").strip(); values["details"] = inputs["Details"].get("1.0", "end").strip()
                parent_index = parent.current()
                if parent_index > 0: values["parent"] = parent_tasks[parent_index - 1].id
                self.store.create(Task.new(user=self.config.user, required=required, **values))
                self.config.remember(values["assigned"]); self.config.save()
                window.destroy(); self.refresh()
            except Exception as error: messagebox.showerror("Create failed", str(error), parent=window)

        def future() -> None:
            value = simpledialog.askstring("Future date", "Date (YYYY-MM-DD):", parent=window)
            if not value: return
            try:
                chosen = date.fromisoformat(value)
                if chosen < date.today(): raise ValueError
                create_for(chosen)
            except ValueError: messagebox.showerror("Invalid date", "Choose today or a future date in YYYY-MM-DD format.", parent=window)

        for name in ("EOD", "EOW", "EOM"):
            ttk.Button(buttons, text=name, command=lambda n=name: create_for(target_date(n))).pack(side="left")
        ttk.Button(buttons, text="Future dateâ€¦", command=future).pack(side="left")
        ttk.Button(buttons, text="No date", command=lambda: create_for(None)).pack(side="left")
        ttk.Label(body, text="Selecting a date creates the task and initializes Priority.").grid(row=5, column=1, sticky="w")
        body.columnconfigure(1, weight=1)

    def refresh(self) -> None:
        try:
            self.tasks = self.store.list()
            for view in self.views: view.render()
            state = self.store.state()
            self.sync_text.set(f"Sync: {state.pending} pending" if state.pending else
                               (f"Sync: last {state.last_sync.replace('T', ' ')[:19]} UTC"
                                if state.last_sync else "Sync: never"))
            self._start_sync()
        except Exception as error:
            self._log("ERROR", f"Local load failed: {safe_error(error)}")
            messagebox.showerror("Load failed", safe_error(error))

    def _start_sync(self) -> None:
        if getattr(self, "_syncing", False): return
        self._syncing = True; self.sync_text.set("Sync: syncingâ€¦")
        self._log("INFO", "Sync started")
        results: queue.Queue[Exception | None] = queue.Queue()

        def work() -> None:
            try:
                self.store.sync()
                results.put(None)
            except Exception as error:
                results.put(error)

        def finished(error: Exception | None) -> None:
            self._syncing = False
            if error:
                pending = self.store.state().pending
                detail = safe_error(error)
                self.sync_text.set(f"Sync: offline ({pending} pending) â€” {detail}")
                self._log("ERROR", f"Sync failed; {pending} pending: {detail}")
                return
            self.tasks = self.store.list()
            for view in self.views: view.render()
            state = self.store.state()
            self.sync_text.set(f"Sync: last {state.last_sync.replace('T', ' ')[:19]} UTC")
            self._log("INFO", f"Sync completed; {state.pending} pending")
            if state.pending: self._start_sync()

        def poll() -> None:
            try: result = results.get_nowait()
            except queue.Empty: self.after(50, poll)
            else: finished(result)

        threading.Thread(target=work, name="taskr-sync", daemon=True).start()
        self.after(50, poll)


def main() -> None:
    config = AppConfig.load()
    if not config.api_url: raise SystemExit("Set TASKR_API_URL or api_url in ~/.config/taskr/config.json")
    remote = AppsScriptTaskStore(config.api_url)
    root = tk.Tk(); TaskrApp(root, config, SQLiteTaskStore(default_cache_path(), remote)); root.mainloop()


if __name__ == "__main__": main()

