from __future__ import annotations

import queue
import random
import threading
import time
import tkinter as tk
from dataclasses import dataclass
from tkinter import messagebox, ttk
from typing import Callable

import pyautogui
from pynput import keyboard, mouse


# ======================== CONFIGURATION ========================

APP_TITLE = "Auto Clicker"
WINDOW_SIZE = "720x730"
POLL_INTERVAL_MS = 50
CAPTURE_DELAY_MS = 3000
MIN_INTERVAL_MS = 1
HOTKEY_OPTIONS = ("F6", "F7", "F8", "F9", "F10", "F11", "F12")
MOUSE_BUTTONS = ("Left", "Right", "Middle")
CLICK_TYPES = ("Single", "Double")

# Disable the extra delay after each PyAutoGUI action.
# Keep PyAutoGUI's built-in corner failsafe enabled.
pyautogui.PAUSE = 0


# ========================= DATA MODELS ==========================

@dataclass(frozen=True)
class ClickSettings:
    interval_ms: int
    random_offset_ms: int
    button: str
    double_click: bool
    position: tuple[int, int] | None


@dataclass(frozen=True)
class RecordedClick:
    delay: float
    x: int
    y: int
    button: str


# ========================= CLICK ENGINE ========================

class ClickEngine:
    """Runs clicks and recorded sequences in a background thread."""

    def __init__(
        self,
        events: queue.SimpleQueue[tuple[str, object]],
    ) -> None:
        self._events = events
        self._lock = threading.Lock()
        self._stop_event: threading.Event | None = None
        self._thread: threading.Thread | None = None
        self._running = False
        self._completed = 0

    @property
    def is_running(self) -> bool:
        with self._lock:
            return self._running

    @property
    def completed_count(self) -> int:
        with self._lock:
            return self._completed

    def start_clicking(
        self,
        settings: ClickSettings,
        repeat_count: int | None,
    ) -> bool:
        return self._launch(self._click_loop, settings, repeat_count)

    def start_playback(
        self,
        sequence: tuple[RecordedClick, ...],
        loop: bool,
    ) -> bool:
        return self._launch(self._playback_loop, sequence, loop)

    def _launch(
        self,
        target: Callable[..., None],
        *args: object,
    ) -> bool:
        with self._lock:
            if self._running:
                return False

            stop_event = threading.Event()
            thread = threading.Thread(
                target=self._run_worker,
                args=(stop_event, target, args),
                name="AutoClickerWorker",
                daemon=True,
            )

            self._stop_event = stop_event
            self._thread = thread
            self._completed = 0
            self._running = True

        try:
            thread.start()
        except RuntimeError as exc:
            with self._lock:
                self._running = False
            self._events.put(("error", f"Could not start worker: {exc}"))
            return False

        return True

    def _run_worker(
        self,
        stop_event: threading.Event,
        target: Callable[..., None],
        args: tuple[object, ...],
    ) -> None:
        try:
            target(stop_event, *args)
        except Exception as exc:
            self._events.put(
                ("error", f"{type(exc).__name__}: {exc}")
            )
        finally:
            with self._lock:
                self._running = False
            self._events.put(("finished", None))

    def stop(self, wait: bool = False) -> None:
        with self._lock:
            stop_event = self._stop_event
            thread = self._thread

        if stop_event is not None:
            stop_event.set()

        if wait and thread is not None:
            if thread is not threading.current_thread():
                thread.join(timeout=0.5)

    def _add_completed(self, count: int) -> None:
        with self._lock:
            self._completed += count

    @staticmethod
    def _perform_click(
        position: tuple[int, int] | None,
        button: str,
        double_click: bool,
    ) -> None:
        clicks = 2 if double_click else 1
        x, y = position if position is not None else (None, None)

        pyautogui.click(
            x=x,
            y=y,
            clicks=clicks,
            interval=0,
            button=button,
        )

    def _click_loop(
        self,
        stop_event: threading.Event,
        settings: ClickSettings,
        repeat_count: int | None,
    ) -> None:
        deadline = time.perf_counter()
        actions = 0

        while (
            not stop_event.is_set()
            and (repeat_count is None or actions < repeat_count)
        ):
            delay = max(0.0, deadline - time.perf_counter())

            if stop_event.wait(delay):
                break

            self._perform_click(
                settings.position,
                settings.button,
                settings.double_click,
            )
            self._add_completed(
                2 if settings.double_click else 1
            )
            actions += 1

            interval_ms = settings.interval_ms

            if settings.random_offset_ms:
                interval_ms += random.uniform(
                    -settings.random_offset_ms,
                    settings.random_offset_ms,
                )

            interval_seconds = max(
                MIN_INTERVAL_MS / 1000,
                interval_ms / 1000,
            )

            # Advance the deadline instead of scheduling from scratch.
            # This compensates for timing drift between clicks.
            deadline += interval_seconds
            now = time.perf_counter()

            if deadline < now:
                deadline = now

    def _playback_loop(
        self,
        stop_event: threading.Event,
        sequence: tuple[RecordedClick, ...],
        loop: bool,
    ) -> None:
        while not stop_event.is_set():
            for item in sequence:
                if stop_event.wait(item.delay):
                    return

                self._perform_click(
                    (item.x, item.y),
                    item.button,
                    False,
                )
                self._add_completed(1)

            if not loop:
                return


# ============================ GUI ===============================

class AutoClickerApp:
    """Tkinter interface and global input-listener management."""

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title(APP_TITLE)
        self.root.geometry(WINDOW_SIZE)
        self.root.minsize(680, 680)

        self.events: queue.SimpleQueue[
            tuple[str, object]
        ] = queue.SimpleQueue()

        self.hotkey_events: queue.SimpleQueue[str] = (
            queue.SimpleQueue()
        )

        self.engine = ClickEngine(self.events)
        self.sequence: list[RecordedClick] = []

        self._recording_active = False
        self._record_started_at = 0.0
        self._last_record_time: float | None = None
        self._window_bounds = (0, 0, 0, 0)

        self._hotkey_name = "F6"
        self._hotkey_pressed_name: str | None = None
        self._engine_mode = "clicking"
        self._last_error: str | None = None
        self._notice = ""
        self._capture_pending = False
        self._closing = False

        self.keyboard_listener: keyboard.Listener | None = None
        self.mouse_listener: mouse.Listener | None = None

        self._build_ui()

        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.root.bind("<Configure>", self._update_window_bounds)

        self.root.update_idletasks()
        self._update_window_bounds()
        self._start_listeners()

        self.root.after(POLL_INTERVAL_MS, self._poll)

    # ------------------------- Widgets -------------------------

    def _build_ui(self) -> None:
        main = ttk.Frame(self.root, padding=10)
        main.pack(fill="both", expand=True)

        # Click interval
        interval_box = ttk.LabelFrame(
            main, text="Click interval", padding=8
        )
        interval_box.pack(fill="x", pady=3)

        self.interval_vars = {
            "Hours": tk.StringVar(value="0"),
            "Minutes": tk.StringVar(value="0"),
            "Seconds": tk.StringVar(value="0"),
            "Milliseconds": tk.StringVar(value="100"),
        }

        for column, (label, variable) in enumerate(
            self.interval_vars.items()
        ):
            ttk.Label(interval_box, text=label).grid(
                row=0, column=column, padx=5, sticky="w"
            )
            ttk.Entry(
                interval_box,
                textvariable=variable,
                width=12,
            ).grid(
                row=1,
                column=column,
                padx=5,
                pady=(3, 0),
                sticky="ew",
            )
            interval_box.columnconfigure(column, weight=1)

        # Random interval offset
        random_box = ttk.Frame(main)
        random_box.pack(fill="x", pady=3)

        self.random_enabled_var = tk.BooleanVar(value=False)
        self.random_offset_var = tk.StringVar(value="0")

        ttk.Checkbutton(
            random_box,
            text="Random offset (± milliseconds):",
            variable=self.random_enabled_var,
            command=self._update_random_controls,
        ).pack(side="left")

        self.random_offset_entry = ttk.Entry(
            random_box,
            textvariable=self.random_offset_var,
            width=10,
        )
        self.random_offset_entry.pack(side="left", padx=(6, 0))

        # Mouse button and click type
        options_box = ttk.LabelFrame(
            main, text="Click options", padding=8
        )
        options_box.pack(fill="x", pady=3)

        self.button_var = tk.StringVar(value="Left")
        self.click_type_var = tk.StringVar(value="Single")

        ttk.Label(
            options_box, text="Mouse button:"
        ).grid(row=0, column=0, sticky="w", padx=5)

        ttk.Combobox(
            options_box,
            textvariable=self.button_var,
            values=MOUSE_BUTTONS,
            state="readonly",
            width=12,
        ).grid(row=0, column=1, padx=5, sticky="w")

        ttk.Label(
            options_box, text="Click type:"
        ).grid(row=0, column=2, sticky="w", padx=(20, 5))

        ttk.Combobox(
            options_box,
            textvariable=self.click_type_var,
            values=CLICK_TYPES,
            state="readonly",
            width=12,
        ).grid(row=0, column=3, padx=5, sticky="w")

        # Repetition
        repeat_box = ttk.LabelFrame(
            main, text="Repeat", padding=8
        )
        repeat_box.pack(fill="x", pady=3)

        self.repeat_mode_var = tk.StringVar(value="until")
        self.repeat_count_var = tk.StringVar(value="100")

        ttk.Radiobutton(
            repeat_box,
            text="Repeat until stopped",
            variable=self.repeat_mode_var,
            value="until",
            command=self._update_repeat_controls,
        ).grid(row=0, column=0, sticky="w", padx=4)

        ttk.Radiobutton(
            repeat_box,
            text="Repeat",
            variable=self.repeat_mode_var,
            value="count",
            command=self._update_repeat_controls,
        ).grid(row=0, column=1, sticky="w", padx=4)

        self.repeat_count_entry = ttk.Entry(
            repeat_box,
            textvariable=self.repeat_count_var,
            width=10,
        )
        self.repeat_count_entry.grid(
            row=0, column=2, sticky="w", padx=(4, 6)
        )

        ttk.Label(repeat_box, text="times").grid(
            row=0, column=3, sticky="w"
        )

        # Cursor position
        position_box = ttk.LabelFrame(
            main, text="Cursor position", padding=8
        )
        position_box.pack(fill="x", pady=3)

        self.position_mode_var = tk.StringVar(value="current")
        self.x_var = tk.StringVar(value="0")
        self.y_var = tk.StringVar(value="0")

        ttk.Radiobutton(
            position_box,
            text="Current location",
            variable=self.position_mode_var,
            value="current",
            command=self._update_position_controls,
        ).grid(row=0, column=0, sticky="w", padx=4)

        ttk.Radiobutton(
            position_box,
            text="Pick location",
            variable=self.position_mode_var,
            value="pick",
            command=self._update_position_controls,
        ).grid(row=1, column=0, sticky="w", padx=4)

        ttk.Label(position_box, text="X:").grid(
            row=1, column=1, padx=(12, 2)
        )

        self.x_entry = ttk.Entry(
            position_box, textvariable=self.x_var, width=9
        )
        self.x_entry.grid(row=1, column=2, padx=3)

        ttk.Label(position_box, text="Y:").grid(
            row=1, column=3, padx=(8, 2)
        )

        self.y_entry = ttk.Entry(
            position_box, textvariable=self.y_var, width=9
        )
        self.y_entry.grid(row=1, column=4, padx=3)

        self.capture_button = ttk.Button(
            position_box,
            text="Capture in 3 seconds",
            command=self.capture_position,
        )
        self.capture_button.grid(
            row=1, column=5, padx=(8, 3)
        )

        # Start, stop, and global hotkey
        control_row = ttk.Frame(main)
        control_row.pack(fill="x", pady=5)

        self.start_button = ttk.Button(
            control_row,
            text="Start",
            command=self.start_clicking,
        )
        self.start_button.pack(side="left", padx=(0, 5))

        self.stop_button = ttk.Button(
            control_row,
            text="Stop",
            command=self.stop_clicking,
            state="disabled",
        )
        self.stop_button.pack(side="left", padx=(0, 14))

        ttk.Label(
            control_row, text="Global hotkey:"
        ).pack(side="left")

        self.hotkey_var = tk.StringVar(value="F6")
        self.hotkey_var.trace_add(
            "write", self._update_hotkey
        )

        ttk.Combobox(
            control_row,
            textvariable=self.hotkey_var,
            values=HOTKEY_OPTIONS,
            state="readonly",
            width=7,
        ).pack(side="left", padx=6)

        # Recording and playback
        recording_box = ttk.LabelFrame(
            main, text="Record & playback", padding=8
        )
        recording_box.pack(
            fill="both", expand=True, pady=3
        )

        record_controls = ttk.Frame(recording_box)
        record_controls.pack(fill="x", pady=(0, 6))

        self.record_button = ttk.Button(
            record_controls,
            text="Start Recording",
            command=self.toggle_recording,
        )
        self.record_button.pack(side="left", padx=(0, 5))

        self.play_button = ttk.Button(
            record_controls,
            text="Play Sequence",
            command=self.start_playback,
            state="disabled",
        )
        self.play_button.pack(side="left", padx=5)

        self.loop_var = tk.BooleanVar(value=False)

        ttk.Checkbutton(
            record_controls,
            text="Loop playback",
            variable=self.loop_var,
        ).pack(side="left", padx=8)

        list_frame = ttk.Frame(recording_box)
        list_frame.pack(fill="both", expand=True)

        self.sequence_list = tk.Listbox(
            list_frame, height=5, activestyle="dotbox"
        )

        scrollbar = ttk.Scrollbar(
            list_frame,
            orient="vertical",
            command=self.sequence_list.yview,
        )

        self.sequence_list.configure(
            yscrollcommand=scrollbar.set
        )

        self.sequence_list.pack(
            side="left", fill="both", expand=True
        )
        scrollbar.pack(side="right", fill="y")

        # Status
        self.status_var = tk.StringVar(
            value="Idle | Clicks performed: 0"
        )

        ttk.Label(
            main,
            textvariable=self.status_var,
            relief="sunken",
            anchor="w",
            padding=6,
        ).pack(fill="x", pady=(6, 0))

        self._update_random_controls()
        self._update_repeat_controls()
        self._update_position_controls()

    # -------------------- Widget state updates --------------------

    def _update_random_controls(self) -> None:
        self.random_offset_entry.configure(
            state=(
                "normal"
                if self.random_enabled_var.get()
                else "disabled"
            )
        )

    def _update_repeat_controls(self) -> None:
        self.repeat_count_entry.configure(
            state=(
                "normal"
                if self.repeat_mode_var.get() == "count"
                else "disabled"
            )
        )

    def _update_position_controls(self) -> None:
        enabled = self.position_mode_var.get() == "pick"
        state = "normal" if enabled else "disabled"

        self.x_entry.configure(state=state)
        self.y_entry.configure(state=state)

        self.capture_button.configure(
            state=(
                "disabled" if self._capture_pending else state
            )
        )

    def _update_hotkey(self, *_: object) -> None:
        self._hotkey_name = self.hotkey_var.get().upper()

    def _update_window_bounds(
        self, _event: object = None
    ) -> None:
        if self._closing:
            return

        try:
            left = self.root.winfo_rootx()
            top = self.root.winfo_rooty()

            self._window_bounds = (
                left,
                top,
                left + self.root.winfo_width(),
                top + self.root.winfo_height(),
            )
        except tk.TclError:
            pass

    # ----------------------- Global listeners -----------------------

    @staticmethod
    def _key_name(key: object) -> str:
        name = getattr(key, "name", None)

        if name is None:
            name = getattr(key, "char", None)

        return str(name).upper() if name is not None else ""

    def _on_key_press(self, key: object) -> None:
        name = self._key_name(key)

        if (
            name == self._hotkey_name
            and self._hotkey_pressed_name is None
        ):
            self._hotkey_pressed_name = name
            self.hotkey_events.put("toggle")

    def _on_key_release(self, key: object) -> None:
        if self._key_name(key) == self._hotkey_pressed_name:
            self._hotkey_pressed_name = None

    def _on_mouse_click(
        self,
        x: int,
        y: int,
        button: mouse.Button,
        pressed: bool,
    ) -> None:
        if (
            not pressed
            or not self._recording_active
            or self.engine.is_running
        ):
            return

        button_name = getattr(button, "name", "")

        if button_name not in ("left", "right", "middle"):
            return

        # Ignore clicks on this program's own controls.
        left, top, right, bottom = self._window_bounds

        if left <= x <= right and top <= y <= bottom:
            return

        now = time.perf_counter()

        reference = (
            self._last_record_time
            if self._last_record_time is not None
            else self._record_started_at
        )

        self._last_record_time = now

        item = RecordedClick(
            delay=max(0.0, now - reference),
            x=int(x),
            y=int(y),
            button=button_name,
        )

        self.events.put(("recorded", item))

    def _start_listeners(self) -> None:
        try:
            self.keyboard_listener = keyboard.Listener(
                on_press=self._on_key_press,
                on_release=self._on_key_release,
            )
            self.keyboard_listener.start()
        except Exception as exc:
            self.keyboard_listener = None
            messagebox.showwarning(
                "Global hotkey unavailable",
                f"The global keyboard listener could not start.\n\n{exc}",
                parent=self.root,
            )

        try:
            self.mouse_listener = mouse.Listener(
                on_click=self._on_mouse_click
            )
            self.mouse_listener.start()
        except Exception as exc:
            self.mouse_listener = None
            messagebox.showwarning(
                "Recording unavailable",
                f"The global mouse listener could not start.\n\n{exc}",
                parent=self.root,
            )

    # ------------------------ Input validation ------------------------

    @staticmethod
    def _read_nonnegative_int(
        label: str,
        variable: tk.StringVar,
    ) -> int:
        raw = variable.get().strip()

        if not raw:
            raise ValueError(f"{label} cannot be empty.")

        try:
            value = int(raw)
        except ValueError as exc:
            raise ValueError(
                f"{label} must be a whole number."
            ) from exc

        if value < 0:
            raise ValueError(f"{label} cannot be negative.")

        return value

    def _read_settings(
        self,
    ) -> tuple[ClickSettings, int | None]:
        values = {
            label: self._read_nonnegative_int(label, variable)
            for label, variable in self.interval_vars.items()
        }

        interval_ms = (
            values["Hours"] * 3_600_000
            + values["Minutes"] * 60_000
            + values["Seconds"] * 1_000
            + values["Milliseconds"]
        )

        if interval_ms == 0:
            raise ValueError(
                "The total click interval must be greater than 0 milliseconds."
            )

        offset_ms = 0

        if self.random_enabled_var.get():
            offset_ms = self._read_nonnegative_int(
                "Random offset", self.random_offset_var
            )

        position: tuple[int, int] | None = None

        if self.position_mode_var.get() == "pick":
            x = self._read_nonnegative_int(
                "X coordinate", self.x_var
            )
            y = self._read_nonnegative_int(
                "Y coordinate", self.y_var
            )
            position = (x, y)

        repeat_count: int | None = None

        if self.repeat_mode_var.get() == "count":
            repeat_count = self._read_nonnegative_int(
                "Repeat count", self.repeat_count_var
            )

            if repeat_count == 0:
                raise ValueError(
                    "Repeat count must be greater than 0."
                )

        settings = ClickSettings(
            interval_ms=interval_ms,
            random_offset_ms=offset_ms,
            button=self.button_var.get().lower(),
            double_click=self.click_type_var.get() == "Double",
            position=position,
        )

        return settings, repeat_count

    # -------------------------- Controls ---------------------------

    def start_clicking(self) -> None:
        if self.engine.is_running:
            return

        try:
            settings, repeat_count = self._read_settings()
        except ValueError as exc:
            messagebox.showerror(
                "Invalid settings", str(exc), parent=self.root
            )
            return

        self._last_error = None
        self._notice = ""
        self._engine_mode = "clicking"

        if not self.engine.start_clicking(
            settings, repeat_count
        ):
            return

        self._refresh_controls()
        self._refresh_status()

    def start_playback(self) -> None:
        if self.engine.is_running:
            return

        if self._recording_active:
            messagebox.showwarning(
                "Recording in progress",
                "Stop recording before playback.",
                parent=self.root,
            )
            return

        if not self.sequence:
            messagebox.showwarning(
                "No sequence",
                "Record at least one mouse click first.",
                parent=self.root,
            )
            return

        self._last_error = None
        self._notice = ""
        self._engine_mode = "playback"

        if self.engine.start_playback(
            tuple(self.sequence), self.loop_var.get()
        ):
            self._refresh_controls()
            self._refresh_status()

    def stop_clicking(self) -> None:
        self.engine.stop()
        self._notice = "Stopping..."
        self._refresh_status()

    def toggle_recording(self) -> None:
        if self._recording_active:
            self._recording_active = False
            self.record_button.configure(
                text="Start Recording"
            )
            self._notice = (
                f"Recording stopped ({len(self.sequence)} clicks recorded)."
            )
            self._refresh_controls()
            self._refresh_status()
            return

        if self.engine.is_running:
            messagebox.showwarning(
                "Clicker is running",
                "Stop clicking before recording.",
                parent=self.root,
            )
            return

        if (
            self.mouse_listener is None
            or not self.mouse_listener.is_alive()
        ):
            messagebox.showerror(
                "Recording unavailable",
                "The global mouse listener is not running.",
                parent=self.root,
            )
            return

        self.sequence.clear()
        self.sequence_list.delete(0, tk.END)

        self._record_started_at = time.perf_counter()
        self._last_record_time = None
        self._recording_active = True
        self._last_error = None
        self._notice = "Click outside this window to record."

        self.record_button.configure(text="Stop Recording")
        self._refresh_controls()
        self._refresh_status()

    def capture_position(self) -> None:
        if (
            self.position_mode_var.get() != "pick"
            or self._capture_pending
        ):
            return

        self._capture_pending = True
        self.capture_button.configure(state="disabled")

        self._notice = (
            "Move the cursor to the target; capturing in 3 seconds..."
        )
        self._refresh_status()

        self.root.after(
            CAPTURE_DELAY_MS, self._finish_capture
        )

    def _finish_capture(self) -> None:
        if self._closing:
            return

        self._capture_pending = False

        try:
            x, y = pyautogui.position()
            self.x_var.set(str(int(x)))
            self.y_var.set(str(int(y)))
            self._notice = (
                f"Captured cursor position ({int(x)}, {int(y)})."
            )
        except Exception as exc:
            self._notice = f"Could not capture position: {exc}"

        self._update_position_controls()
        self._refresh_status()

    # -------------------------- Event loop --------------------------

    def _handle_event(
        self,
        kind: str,
        payload: object,
    ) -> None:
        if kind == "recorded" and isinstance(
            payload, RecordedClick
        ):
            self.sequence.append(payload)
            index = len(self.sequence)

            description = (
                f"{index:03d}: ({payload.x}, {payload.y})  "
                f"{payload.button.title()}  "
                f"delay {payload.delay * 1000:.0f} ms"
            )

            self.sequence_list.insert(tk.END, description)
            self.sequence_list.yview_moveto(1.0)

        elif kind == "error":
            self._last_error = str(payload)
            messagebox.showerror(
                "Auto Clicker error",
                self._last_error,
                parent=self.root,
            )

        elif kind == "finished":
            self._notice = ""

    def _poll(self) -> None:
        if self._closing:
            return

        self._update_window_bounds()

        while True:
            try:
                kind, payload = self.events.get_nowait()
            except queue.Empty:
                break

            self._handle_event(kind, payload)

        while True:
            try:
                action = self.hotkey_events.get_nowait()
            except queue.Empty:
                break

            if action == "toggle":
                if self.engine.is_running:
                    self.stop_clicking()
                else:
                    self.start_clicking()

        self._refresh_controls()
        self._refresh_status()

        self.root.after(POLL_INTERVAL_MS, self._poll)

    def _refresh_controls(self) -> None:
        running = self.engine.is_running

        self.start_button.configure(
            state="disabled" if running else "normal"
        )
        self.stop_button.configure(
            state="normal" if running else "disabled"
        )

        self.play_button.configure(
            state=(
                "normal"
                if (
                    self.sequence
                    and not running
                    and not self._recording_active
                )
                else "disabled"
            )
        )

        self.record_button.configure(
            state="normal"
        )

        self.capture_button.configure(
            state=(
                "normal"
                if (
                    self.position_mode_var.get() == "pick"
                    and not self._capture_pending
                )
                else "disabled"
            )
        )

    def _refresh_status(self) -> None:
        if self.engine.is_running:
            state = (
                "Playback"
                if self._engine_mode == "playback"
                else "Running"
            )
        elif self._recording_active:
            state = "Recording"
        elif self._last_error:
            state = "Error"
        else:
            state = "Idle"

        parts = [
            state,
            f"Clicks performed: {self.engine.completed_count}",
        ]

        if self._recording_active or self.sequence:
            parts.append(
                f"Recorded clicks: {len(self.sequence)}"
            )

        if self._last_error and state == "Error":
            parts.append(self._last_error)

        if self._notice:
            parts.append(self._notice)

        self.status_var.set(" | ".join(parts))

    # ----------------------- Clean shutdown -----------------------

    def close(self) -> None:
        if self._closing:
            return

        self._closing = True
        self._recording_active = False
        self.engine.stop()

        listeners = (
            self.keyboard_listener,
            self.mouse_listener,
        )

        for listener in listeners:
            if listener is not None:
                listener.stop()

        for listener in listeners:
            if listener is not None and listener.is_alive():
                listener.join(timeout=0.3)

        self.engine.stop(wait=True)
        self.root.destroy()


def main() -> None:
    root = tk.Tk()
    AutoClickerApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
