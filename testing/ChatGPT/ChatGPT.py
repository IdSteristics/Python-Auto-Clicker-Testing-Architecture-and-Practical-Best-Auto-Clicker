"""Auto Clicker — single-file Windows GUI application."""

from __future__ import annotations

import random
import threading
import time
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Callable

import pyautogui
from pynput import keyboard, mouse

# Tunable constants
APP_TITLE = "Auto Clicker"
WINDOW_SIZE = "560x650"
DEFAULT_HOTKEY = "F6"
GUI_POLL_MS = 100
MIN_SLEEP_SECONDS = 0.001
MAX_RANDOM_OFFSET_MS = 60000
BUTTON_MAP = {"Left": "left", "Right": "right", "Middle": "middle"}
PYNPUT_BUTTON_MAP = {
    "Left": mouse.Button.left,
    "Right": mouse.Button.right,
    "Middle": mouse.Button.middle,
}


def parse_nonnegative_int(value: str, label: str) -> int:
    """Parse a nonnegative integer and provide a useful validation error."""
    if not value.strip():
        raise ValueError(f"{label} cannot be empty.")
    try:
        parsed = int(value)
    except ValueError as exc:
        raise ValueError(f"{label} must be a whole number.") from exc
    if parsed < 0:
        raise ValueError(f"{label} cannot be negative.")
    return parsed


class ClickEngine:
    """Runs timed clicks independently from the Tkinter GUI."""

    def __init__(self, on_update: Callable[[str, int], None]) -> None:
        self._on_update = on_update
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._count = 0

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(
        self,
        interval_seconds: float,
        random_offset_ms: int,
        use_random_offset: bool,
        button: str,
        click_type: str,
        repeat_count: int | None,
        position: tuple[int, int] | None,
    ) -> None:
        with self._lock:
            if self.running:
                return
            self._stop_event.clear()
            self._count = 0
            self._thread = threading.Thread(
                target=self._run,
                args=(
                    interval_seconds,
                    random_offset_ms,
                    use_random_offset,
                    button,
                    click_type,
                    repeat_count,
                    position,
                ),
                daemon=True,
                name="AutoClickEngine",
            )
            self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()

    def _run(
        self,
        interval_seconds: float,
        random_offset_ms: int,
        use_random_offset: bool,
        button: str,
        click_type: str,
        repeat_count: int | None,
        position: tuple[int, int] | None,
    ) -> None:
        try:
            next_click = time.perf_counter()
            while not self._stop_event.is_set():
                if position is not None:
                    pyautogui.click(
                        x=position[0],
                        y=position[1],
                        clicks=2 if click_type == "Double" else 1,
                        interval=0.05,
                        button=BUTTON_MAP[button],
                    )
                else:
                    pyautogui.click(
                        clicks=2 if click_type == "Double" else 1,
                        interval=0.05,
                        button=BUTTON_MAP[button],
                    )

                self._count += 1
                self._on_update("Running", self._count)
                if repeat_count is not None and self._count >= repeat_count:
                    break

                delay = interval_seconds
                if use_random_offset and random_offset_ms:
                    delay += random.uniform(-random_offset_ms, random_offset_ms) / 1000.0
                delay = max(MIN_SLEEP_SECONDS, delay)
                next_click += delay
                remaining = next_click - time.perf_counter()
                if remaining > 0:
                    self._stop_event.wait(remaining)
                else:
                    # Reset the schedule after a slow click rather than accumulating drift.
                    next_click = time.perf_counter()
        except Exception as exc:
            self._on_update(f"Error: {exc}", self._count)
        finally:
            self._on_update("Stopped", self._count)


class SequenceRecorder:
    """Records mouse clicks and replays them with their recorded delays."""

    def __init__(self, on_change: Callable[[str], None]) -> None:
        self.events: list[dict[str, object]] = []
        self._on_change = on_change
        self._recording = False
        self._last_time = 0.0
        self._listener: mouse.Listener | None = None

    @property
    def recording(self) -> bool:
        return self._recording

    def start(self) -> None:
        if self._recording:
            return
        self.events.clear()
        self._last_time = time.perf_counter()
        self._recording = True

        def on_click(x: int, y: int, button: mouse.Button, pressed: bool) -> None:
            if not pressed or not self._recording:
                return
            now = time.perf_counter()
            delay = max(0.0, now - self._last_time)
            self._last_time = now
            self.events.append(
                {
                    "x": int(x),
                    "y": int(y),
                    "button": {
                        mouse.Button.left: "left",
                        mouse.Button.right: "right",
                        mouse.Button.middle: "middle",
                    }.get(button, "left"),
                    "delay": delay,
                }
            )
            self._on_change(f"Recorded {len(self.events)} click(s)")

        self._listener = mouse.Listener(on_click=on_click)
        self._listener.start()
        self._on_change("Recording clicks…")

    def stop(self) -> None:
        self._recording = False
        if self._listener is not None:
            self._listener.stop()
            self._listener = None
        self._on_change(f"Recording stopped ({len(self.events)} click(s))")

    def replay(self, stop_event: threading.Event, loop: bool, on_update: Callable[[str, int], None]) -> None:
        if not self.events:
            raise ValueError("Record at least one click before playback.")
        events = [event.copy() for event in self.events]
        count = 0
        while not stop_event.is_set():
            for event in events:
                if stop_event.wait(float(event["delay"])):
                    return
                pyautogui.click(
                    x=int(event["x"]),
                    y=int(event["y"]),
                    clicks=1,
                    button=event["button"],
                )
                count += 1
                on_update("Playing back", count)
            if not loop:
                return


class AutoClickerApp:
    """Owns widgets, validation, hotkeys, and application lifecycle."""

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title(APP_TITLE)
        self.root.geometry(WINDOW_SIZE)
        self.root.minsize(520, 620)

        self.status_var = tk.StringVar(value="Idle — 0 clicks")
        self.engine = ClickEngine(self._queue_status)
        self.recorder = SequenceRecorder(self._queue_recording_status)
        self.playback_stop = threading.Event()
        self.playback_thread: threading.Thread | None = None
        self._closing = False
        self._hotkey_listener: keyboard.Listener | None = None
        self.hotkey_var = tk.StringVar(value=DEFAULT_HOTKEY)
        self._build_ui()
        self._set_hotkey_listener(DEFAULT_HOTKEY)
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.root.after(GUI_POLL_MS, self._poll_engine)

    def _build_ui(self) -> None:
        root_frame = ttk.Frame(self.root, padding=12)
        root_frame.pack(fill="both", expand=True)

        interval_frame = ttk.LabelFrame(root_frame, text="Click interval", padding=8)
        interval_frame.pack(fill="x", pady=(0, 8))
        self.interval_vars: dict[str, tk.StringVar] = {}
        for index, (label, default) in enumerate(
            (("Hours", "0"), ("Minutes", "0"), ("Seconds", "0"), ("Milliseconds", "100"))
        ):
            ttk.Label(interval_frame, text=label).grid(row=0, column=index, padx=4, sticky="w")
            var = tk.StringVar(value=default)
            self.interval_vars[label] = var
            ttk.Entry(interval_frame, textvariable=var, width=9).grid(
                row=1, column=index, padx=4, pady=(3, 0), sticky="ew"
            )
            interval_frame.columnconfigure(index, weight=1)

        random_frame = ttk.Frame(root_frame)
        random_frame.pack(fill="x", pady=(0, 8))
        self.random_enabled = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            random_frame, text="Random offset (± milliseconds)", variable=self.random_enabled
        ).pack(side="left")
        self.random_ms_var = tk.StringVar(value="0")
        ttk.Entry(random_frame, textvariable=self.random_ms_var, width=8).pack(
            side="left", padx=6
        )

        options_frame = ttk.LabelFrame(root_frame, text="Click options", padding=8)
        options_frame.pack(fill="x", pady=(0, 8))
        self.button_var = tk.StringVar(value="Left")
        self.click_type_var = tk.StringVar(value="Single")
        ttk.Label(options_frame, text="Mouse button").grid(row=0, column=0, sticky="w")
        ttk.Combobox(
            options_frame, textvariable=self.button_var, values=tuple(BUTTON_MAP),
            state="readonly", width=12
        ).grid(row=0, column=1, padx=6, sticky="w")
        ttk.Label(options_frame, text="Click type").grid(row=0, column=2, padx=(12, 0), sticky="w")
        ttk.Combobox(
            options_frame, textvariable=self.click_type_var, values=("Single", "Double"),
            state="readonly", width=12
        ).grid(row=0, column=3, padx=6, sticky="w")

        repeat_frame = ttk.LabelFrame(root_frame, text="Repeat", padding=8)
        repeat_frame.pack(fill="x", pady=(0, 8))
        self.repeat_mode = tk.StringVar(value="until")
        ttk.Radiobutton(
            repeat_frame, text="Repeat until stopped", value="until", variable=self.repeat_mode
        ).grid(row=0, column=0, sticky="w")
        ttk.Radiobutton(
            repeat_frame, text="Repeat N times", value="count", variable=self.repeat_mode
        ).grid(row=1, column=0, sticky="w")
        self.repeat_count_var = tk.StringVar(value="100")
        ttk.Entry(repeat_frame, textvariable=self.repeat_count_var, width=10).grid(
            row=1, column=1, padx=8, sticky="w"
        )

        position_frame = ttk.LabelFrame(root_frame, text="Cursor position", padding=8)
        position_frame.pack(fill="x", pady=(0, 8))
        self.position_mode = tk.StringVar(value="current")
        ttk.Radiobutton(
            position_frame, text="Current location", value="current", variable=self.position_mode
        ).grid(row=0, column=0, sticky="w")
        ttk.Radiobutton(
            position_frame, text="Pick location", value="pick", variable=self.position_mode
        ).grid(row=1, column=0, sticky="w")
        self.x_var = tk.StringVar(value="0")
        self.y_var = tk.StringVar(value="0")
        ttk.Label(position_frame, text="X").grid(row=1, column=1, padx=(12, 2))
        ttk.Entry(position_frame, textvariable=self.x_var, width=8).grid(row=1, column=2)
        ttk.Label(position_frame, text="Y").grid(row=1, column=3, padx=(8, 2))
        ttk.Entry(position_frame, textvariable=self.y_var, width=8).grid(row=1, column=4)
        ttk.Button(position_frame, text="Capture position", command=self.capture_position).grid(
            row=1, column=5, padx=(10, 0)
        )

        hotkey_frame = ttk.LabelFrame(root_frame, text="Global hotkey", padding=8)
        hotkey_frame.pack(fill="x", pady=(0, 8))
        ttk.Label(hotkey_frame, text="Start/Stop key").pack(side="left")
        ttk.Entry(hotkey_frame, textvariable=self.hotkey_var, width=12).pack(
            side="left", padx=8
        )
        ttk.Button(hotkey_frame, text="Apply", command=self.apply_hotkey).pack(side="left")
        ttk.Label(hotkey_frame, text="(default F6)").pack(side="left", padx=8)

        recording_frame = ttk.LabelFrame(root_frame, text="Record & Playback", padding=8)
        recording_frame.pack(fill="x", pady=(0, 8))
        ttk.Button(recording_frame, text="Record", command=self.start_recording).pack(
            side="left", padx=(0, 5)
        )
        ttk.Button(recording_frame, text="Stop recording", command=self.stop_recording).pack(
            side="left", padx=5
        )
        self.loop_playback = tk.BooleanVar(value=False)
        ttk.Checkbutton(recording_frame, text="Loop playback", variable=self.loop_playback).pack(
            side="left", padx=5
        )
        ttk.Button(recording_frame, text="Play sequence", command=self.start_playback).pack(
            side="left", padx=5
        )

        controls = ttk.Frame(root_frame)
        controls.pack(fill="x", pady=(2, 8))
        self.start_button = ttk.Button(controls, text="Start", command=self.start_clicking)
        self.start_button.pack(side="left", fill="x", expand=True, padx=(0, 5))
        self.stop_button = ttk.Button(controls, text="Stop", command=self.stop_clicking, state="disabled")
        self.stop_button.pack(side="left", fill="x", expand=True, padx=(5, 0))

        ttk.Separator(root_frame).pack(fill="x", pady=(0, 6))
        ttk.Label(root_frame, textvariable=self.status_var, anchor="w").pack(fill="x")

    def _queue_status(self, state: str, count: int) -> None:
        if not self._closing:
            self.root.after(0, self._set_status, state, count)

    def _set_status(self, state: str, count: int) -> None:
        self.status_var.set(f"{state} — {count} clicks")
        running = state in ("Running", "Playing back")
        self.start_button.configure(state="disabled" if running else "normal")
        self.stop_button.configure(state="normal" if running else "disabled")

    def _queue_recording_status(self, text: str) -> None:
        if not self._closing:
            self.root.after(0, self.status_var.set, text)

    def _poll_engine(self) -> None:
        if self._closing:
            return
        if not self.engine.running and self.start_button["state"] == "disabled":
            self._set_status("Idle", 0)
        self.root.after(GUI_POLL_MS, self._poll_engine)

    def _interval_seconds(self) -> float:
        units = (("Hours", 3_600_000), ("Minutes", 60_000), ("Seconds", 1_000), ("Milliseconds", 1))
        total_ms = sum(
            parse_nonnegative_int(self.interval_vars[label].get(), label) * multiplier
            for label, multiplier in units
        )
        if total_ms <= 0:
            raise ValueError("The total click interval must be greater than zero.")
        return total_ms / 1000.0

    def start_clicking(self) -> None:
        try:
            interval = self._interval_seconds()
            random_ms = parse_nonnegative_int(self.random_ms_var.get(), "Random offset")
            repeat_count = None
            if self.repeat_mode.get() == "count":
                repeat_count = parse_nonnegative_int(self.repeat_count_var.get(), "Repeat count")
                if repeat_count <= 0:
                    raise ValueError("Repeat count must be at least 1.")
            position = None
            if self.position_mode.get() == "pick":
                x = parse_nonnegative_int(self.x_var.get(), "X coordinate")
                y = parse_nonnegative_int(self.y_var.get(), "Y coordinate")
                screen_width, screen_height = pyautogui.size()
                if x >= screen_width or y >= screen_height:
                    raise ValueError("The selected position is outside the screen.")
                position = (x, y)
            self._set_status("Running", 0)
            self.engine.start(
                interval, random_ms, self.random_enabled.get(), self.button_var.get(),
                self.click_type_var.get(), repeat_count, position
            )
        except (ValueError, pyautogui.FailSafeException) as exc:
            messagebox.showerror("Invalid settings", str(exc), parent=self.root)

    def stop_clicking(self) -> None:
        self.engine.stop()
        self.playback_stop.set()
        self._set_status("Stopping", 0)

    def capture_position(self) -> None:
        self.root.iconify()
        self.status_var.set("Move the pointer to the target; capturing in 3 seconds…")
        self.root.after(3000, self._finish_capture)

    def _finish_capture(self) -> None:
        if self._closing:
            return
        try:
            x, y = pyautogui.position()
            self.x_var.set(str(x))
            self.y_var.set(str(y))
            self.position_mode.set("pick")
            self.status_var.set(f"Captured position: X={x}, Y={y}")
        except Exception as exc:
            messagebox.showerror("Capture failed", str(exc), parent=self.root)
        finally:
            self.root.deiconify()

    def apply_hotkey(self) -> None:
        hotkey = self.hotkey_var.get().strip().upper()
        if not hotkey:
            messagebox.showerror("Invalid hotkey", "Enter a key such as F6 or F8.", parent=self.root)
            return
        self._set_hotkey_listener(hotkey)

    def _set_hotkey_listener(self, hotkey: str) -> None:
        if self._hotkey_listener is not None:
            self._hotkey_listener.stop()
            self._hotkey_listener = None
        try:
            key = getattr(keyboard.Key, hotkey.lower(), None)
            if key is not None:
                target = key
            elif len(hotkey) == 1:
                target = keyboard.KeyCode.from_char(hotkey.lower())
            else:
                raise ValueError("Use a single character or a key name such as F6, SPACE, or ESC.")
            self._hotkey_listener = keyboard.Listener(
                on_press=lambda pressed: self._on_hotkey(pressed, target)
            )
            self._hotkey_listener.start()
            self.hotkey_var.set(hotkey)
        except (ValueError, RuntimeError) as exc:
            messagebox.showerror("Hotkey error", str(exc), parent=self.root)

    def _on_hotkey(self, pressed: keyboard.Key | keyboard.KeyCode, target: keyboard.Key | keyboard.KeyCode) -> None:
        if pressed == target:
            self.root.after(0, self._toggle_hotkey)

    def _toggle_hotkey(self) -> None:
        if self.engine.running or (self.playback_thread and self.playback_thread.is_alive()):
            self.stop_clicking()
        else:
            self.start_clicking()

    def start_recording(self) -> None:
        if self.engine.running:
            messagebox.showwarning("Busy", "Stop auto clicking before recording.", parent=self.root)
            return
        self.recorder.start()

    def stop_recording(self) -> None:
        self.recorder.stop()

    def start_playback(self) -> None:
        if self.engine.running or (self.playback_thread and self.playback_thread.is_alive()):
            messagebox.showwarning("Busy", "Stop the current task before playback.", parent=self.root)
            return
        try:
            self.recorder.stop()
            self.playback_stop.clear()
            self.playback_thread = threading.Thread(
                target=self._playback_worker, args=(self.loop_playback.get(),),
                daemon=True, name="AutoClickPlayback"
            )
            self._set_status("Playing back", 0)
            self.playback_thread.start()
        except ValueError as exc:
            messagebox.showerror("Playback error", str(exc), parent=self.root)

    def _playback_worker(self, loop: bool) -> None:
        try:
            self.recorder.replay(self.playback_stop, loop, self._queue_status)
        except Exception as exc:
            self._queue_status(f"Error: {exc}", 0)
        finally:
            self._queue_status("Stopped", 0)

    def close(self) -> None:
        self._closing = True
        self.engine.stop()
        self.playback_stop.set()
        self.recorder.stop()
        if self._hotkey_listener is not None:
            self._hotkey_listener.stop()
        self.root.destroy()


def main() -> None:
    root = tk.Tk()
    AutoClickerApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
