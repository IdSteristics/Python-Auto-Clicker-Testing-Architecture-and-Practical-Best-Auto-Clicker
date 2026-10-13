import ctypes
import functools
import itertools
import queue
import random
import threading
import time
import tkinter as tk
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from tkinter import messagebox, ttk

from pynput import keyboard, mouse

APP_TITLE = "Auto Clicker"
DEFAULT_HOTKEY = keyboard.Key.f6
BUTTONS = {"Left": mouse.Button.left, "Right": mouse.Button.right, "Middle": mouse.Button.middle}
CLICK_COUNTS = {"Single": 1, "Double": 2}
SINGLE = CLICK_COUNTS["Single"]
FIELDS = {
    "hours": ("Hours", "0"), "minutes": ("Minutes", "0"), "seconds": ("Seconds", "0"),
    "ms": ("Milliseconds", "100"), "jitter": ("Random offset", "0"),
    "repeat": ("Repeat count", "1"), "x": ("X", "0"), "y": ("Y", "0"),
}
INTERVAL_FIELDS = (("hours", "hrs"), ("minutes", "mins"), ("seconds", "secs"), ("ms", "ms"))
POLL_MS = 100
MS_PER_S = 1000
S_PER_MIN = 60
S_PER_HOUR = 3600
MAX_INPUT = 1_000_000
MAX_INTERVAL_S = 24 * S_PER_HOUR
PICK_DELAY_S = 3
RESYNC_LAG_S = 0.25
LOOP_PAUSE_S = 0.2
JOIN_TIMEOUT_S = 2.0
TIMER_RESOLUTION_MS = 1
DPI_PER_MONITOR = 2
ENTRY_WIDTH = 7
COMBO_WIDTH = 8
PAD = 6
PAD_SMALL = 2
CAPTURE_TEXT = "Capture position"

HotKey = keyboard.Key | keyboard.KeyCode
Point = tuple[int, int]
Bounds = tuple[int, int, int, int]


class InputError(ValueError):
    pass


class State(Enum):
    IDLE = "Idle"
    RUNNING = "Running"
    PLAYING = "Playing"
    RECORDING = "Recording"


@dataclass(frozen=True)
class ClickSettings:
    interval: float
    jitter: float
    button: mouse.Button
    count: int
    repeat: int | None
    position: Point | None


@dataclass(frozen=True)
class Step:
    delay: float
    position: Point
    button: mouse.Button


@dataclass(frozen=True)
class Form:
    fields: dict[str, str]
    use_jitter: bool
    forever: bool
    pick: bool
    button: str
    kind: str


def read_int(text: str, label: str, minimum: int = 0) -> int:
    try:
        value = int(text.strip())
    except ValueError:
        raise InputError(f"{label} must be a whole number (not empty or text).") from None
    if not minimum <= value <= MAX_INPUT:
        raise InputError(f"{label} must be between {minimum} and {MAX_INPUT}.")
    return value


def parse_settings(form: Form) -> ClickSettings:
    def num(key: str, minimum: int = 0) -> int:
        return read_int(form.fields[key], FIELDS[key][0], minimum)

    interval = num("hours") * S_PER_HOUR + num("minutes") * S_PER_MIN + num("seconds") + num("ms") / MS_PER_S
    if not 0 < interval <= MAX_INTERVAL_S:
        raise InputError(f"Total interval must be above 0 and at most {MAX_INTERVAL_S // S_PER_HOUR} hours.")
    jitter = num("jitter") / MS_PER_S if form.use_jitter else 0.0
    if jitter > interval:
        raise InputError("Random offset can't exceed the click interval.")
    repeat = None if form.forever else num("repeat", 1)
    position = (num("x", -MAX_INPUT), num("y", -MAX_INPUT)) if form.pick else None
    return ClickSettings(interval, jitter, BUTTONS[form.button], CLICK_COUNTS[form.kind], repeat, position)


def key_label(key: HotKey) -> str:
    name = key.name if isinstance(key, keyboard.Key) else key.char
    return (name or str(key)).upper()


def call_windows(library: str, function: str, argument: int) -> None:
    try:
        getattr(getattr(ctypes.windll, library), function)(argument)
    except (AttributeError, OSError):
        pass


class ClickEngine:
    def __init__(self, on_finish: Callable[[str | None], None]) -> None:
        self._mouse = mouse.Controller()
        self._on_finish = on_finish
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.clicks = 0

    @property
    def cursor(self) -> Point:
        return self._mouse.position

    def start(self, job: Callable[[], None]) -> None:
        self.shutdown()
        self._stop.clear()
        self.clicks = 0
        self._thread = threading.Thread(target=self._run, args=(job,), daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def shutdown(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(JOIN_TIMEOUT_S)

    def click_loop(self, s: ClickSettings) -> None:
        deadline = time.perf_counter()
        for done in itertools.count():
            if done == s.repeat or not self._wait_until(deadline):
                return
            self._click(s.button, s.count, s.position)
            deadline = self._advance(deadline, s.interval + random.uniform(-s.jitter, s.jitter))

    def play(self, steps: tuple[Step, ...], loop: bool) -> None:
        deadline = time.perf_counter()
        while True:
            for step in steps:
                deadline = self._advance(deadline, step.delay)
                if not self._wait_until(deadline):
                    return
                self._click(step.button, SINGLE, step.position)
            if not loop:
                return
            deadline = self._advance(deadline, LOOP_PAUSE_S)

    def _run(self, job: Callable[[], None]) -> None:
        error = None
        try:
            job()
        except Exception as exc:
            error = str(exc) or type(exc).__name__
        self._on_finish(error)

    def _click(self, button: mouse.Button, count: int, position: Point | None) -> None:
        if position is not None:
            self._mouse.position = position
        self._mouse.click(button, count)
        self.clicks += 1

    def _wait_until(self, deadline: float) -> bool:
        return not self._stop.wait(max(0.0, deadline - time.perf_counter()))

    @staticmethod
    def _advance(deadline: float, gap: float) -> float:
        target = deadline + gap
        now = time.perf_counter()
        return now if now - target > RESYNC_LAG_S else target


class Recorder:
    def __init__(self) -> None:
        self.steps: list[Step] = []
        self._bounds: Bounds = (0, 0, 0, 0)
        self._last: float | None = None
        self._listener: mouse.Listener | None = None

    def start(self, bounds: Bounds) -> None:
        self.steps, self._bounds, self._last = [], bounds, None
        self._listener = mouse.Listener(on_click=self._on_click)
        self._listener.start()

    def stop(self) -> None:
        if self._listener is not None:
            self._listener.stop()
            self._listener = None

    def _on_click(self, x: int, y: int, button: mouse.Button, pressed: bool) -> None:
        left, top, right, bottom = self._bounds
        if not pressed or (left <= x < right and top <= y < bottom):
            return
        now = time.perf_counter()
        self.steps.append(Step(0.0 if self._last is None else now - self._last, (x, y), button))
        self._last = now


class HotkeyManager:
    def __init__(self, on_trigger: Callable[[], None], on_captured: Callable[[], None]) -> None:
        self.hotkey: HotKey = DEFAULT_HOTKEY
        self._on_trigger = on_trigger
        self._on_captured = on_captured
        self._capturing = False
        self._held = False
        self._listener = keyboard.Listener(on_press=self._on_press, on_release=self._on_release)

    def start(self) -> None:
        self._listener.start()

    def stop(self) -> None:
        self._listener.stop()

    def capture_next(self) -> None:
        self._capturing = True

    def _on_press(self, key: HotKey | None) -> None:
        if key is None:
            return
        if self._capturing:
            self._capturing, self.hotkey, self._held = False, key, True
            self._on_captured()
        elif key == self.hotkey and not self._held:
            self._held = True
            self._on_trigger()

    def _on_release(self, key: HotKey | None) -> None:
        if key == self.hotkey:
            self._held = False


class AutoClickerApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self._events: queue.SimpleQueue[Callable[[], None]] = queue.SimpleQueue()
        self._engine = ClickEngine(lambda err: self._post(lambda: self._finished(err)))
        self._recorder = Recorder()
        self._hotkeys = HotkeyManager(lambda: self._post(self._toggle), lambda: self._post(self._show_hotkey))
        self._state = State.IDLE
        self._fields = {key: tk.StringVar(value=default) for key, (_, default) in FIELDS.items()}
        self._random = tk.BooleanVar()
        self._forever = tk.BooleanVar(value=True)
        self._pick = tk.BooleanVar()
        self._loop = tk.BooleanVar()
        self._button = tk.StringVar(value=next(iter(BUTTONS)))
        self._type = tk.StringVar(value=next(iter(CLICK_COUNTS)))
        self._status = tk.StringVar()
        self._hotkey_text = tk.StringVar()
        self._steps_text = tk.StringVar()
        self._build()
        self._show_hotkey()
        root.protocol("WM_DELETE_WINDOW", self._close)
        self._hotkeys.start()
        self._poll()

    def _group(self, title: str) -> ttk.LabelFrame:
        box = ttk.LabelFrame(self.root, text=title)
        box.pack(fill=tk.X, padx=PAD, pady=(PAD, 0))
        return box

    @staticmethod
    def _row(parent: tk.Widget) -> ttk.Frame:
        row = ttk.Frame(parent)
        row.pack(fill=tk.X, padx=PAD, pady=PAD_SMALL)
        return row

    @staticmethod
    def _pack(*widgets: tk.Widget) -> None:
        for widget in widgets:
            widget.pack(side=tk.LEFT, padx=PAD_SMALL)

    def _entry(self, row: ttk.Frame, key: str) -> ttk.Entry:
        return ttk.Entry(row, textvariable=self._fields[key], width=ENTRY_WIDTH)

    @staticmethod
    def _combo(row: ttk.Frame, var: tk.StringVar, values: tuple[str, ...]) -> ttk.Combobox:
        return ttk.Combobox(row, textvariable=var, values=values, state="readonly", width=COMBO_WIDTH)

    def _build(self) -> None:
        box = self._group("Click interval")
        row = self._row(box)
        for key, unit in INTERVAL_FIELDS:
            self._pack(self._entry(row, key), ttk.Label(row, text=unit))
        row = self._row(box)
        self._pack(ttk.Checkbutton(row, text="Random offset ±", variable=self._random),
                   self._entry(row, "jitter"), ttk.Label(row, text="ms"))

        row = self._row(self._group("Click options"))
        self._pack(ttk.Label(row, text="Button:"), self._combo(row, self._button, tuple(BUTTONS)),
                   ttk.Label(row, text="Type:"), self._combo(row, self._type, tuple(CLICK_COUNTS)))

        box = self._group("Click repeat")
        row = self._row(box)
        self._pack(ttk.Radiobutton(row, text="Repeat", variable=self._forever, value=False),
                   self._entry(row, "repeat"), ttk.Label(row, text="times"))
        row = self._row(box)
        self._pack(ttk.Radiobutton(row, text="Repeat until stopped", variable=self._forever, value=True))

        box = self._group("Cursor position")
        row = self._row(box)
        self._pack(ttk.Radiobutton(row, text="Current location", variable=self._pick, value=False))
        row = self._row(box)
        self._capture_btn = ttk.Button(row, text=CAPTURE_TEXT, command=self._capture)
        self._pack(ttk.Radiobutton(row, text="Pick location", variable=self._pick, value=True),
                   ttk.Label(row, text="X"), self._entry(row, "x"),
                   ttk.Label(row, text="Y"), self._entry(row, "y"), self._capture_btn)

        row = self._row(self.root)
        self._start_btn = ttk.Button(row, text="Start", command=self._start_clicking)
        self._stop_btn = ttk.Button(row, text="Stop", command=self._stop, state=tk.DISABLED)
        self._pack(self._start_btn, self._stop_btn)

        row = self._row(self._group("Hotkey"))
        self._pack(ttk.Label(row, textvariable=self._hotkey_text),
                   ttk.Button(row, text="Change", command=self._change_hotkey))

        row = self._row(self._group("Record & Playback"))
        self._record_btn = ttk.Button(row, text="Record", command=self._record)
        self._play_btn = ttk.Button(row, text="Play", command=self._play)
        self._pack(self._record_btn, self._play_btn, ttk.Checkbutton(row, text="Loop", variable=self._loop),
                   ttk.Label(row, textvariable=self._steps_text))

        ttk.Label(self.root, textvariable=self._status, relief=tk.SUNKEN, anchor=tk.W).pack(
            fill=tk.X, padx=PAD, pady=PAD)

    def _form(self) -> Form:
        fields = {key: var.get() for key, var in self._fields.items()}
        return Form(fields, self._random.get(), self._forever.get(), self._pick.get(),
                    self._button.get(), self._type.get())

    def _window_bounds(self) -> Bounds:
        r = self.root
        return (r.winfo_x(), r.winfo_y(), r.winfo_rootx() + r.winfo_width(), r.winfo_rooty() + r.winfo_height())

    def _post(self, action: Callable[[], None]) -> None:
        self._events.put(action)

    def _error(self, message: str) -> None:
        messagebox.showerror(APP_TITLE, message, parent=self.root)

    def _set_state(self, state: State) -> None:
        self._state = state
        idle = state is State.IDLE
        for button in (self._start_btn, self._record_btn, self._play_btn):
            button.config(state=tk.NORMAL if idle else tk.DISABLED)
        self._stop_btn.config(state=tk.DISABLED if idle else tk.NORMAL)
        self._refresh()

    def _begin(self, state: State, job: Callable[[], None]) -> None:
        self._set_state(state)
        self._engine.start(job)

    def _start_clicking(self) -> None:
        try:
            settings = parse_settings(self._form())
        except InputError as exc:
            self._error(str(exc))
            return
        self._begin(State.RUNNING, functools.partial(self._engine.click_loop, settings))

    def _play(self) -> None:
        if not self._recorder.steps:
            self._error("Record at least one click first.")
            return
        steps = tuple(self._recorder.steps)
        self._begin(State.PLAYING, functools.partial(self._engine.play, steps, self._loop.get()))

    def _record(self) -> None:
        self._set_state(State.RECORDING)
        self._recorder.start(self._window_bounds())

    def _stop(self) -> None:
        if self._state is State.RECORDING:
            self._recorder.stop()
            self._set_state(State.IDLE)
        else:
            self._engine.stop()

    def _toggle(self) -> None:
        if self._state is State.IDLE:
            self._start_clicking()
        else:
            self._stop()

    def _finished(self, error: str | None) -> None:
        self._set_state(State.IDLE)
        if error:
            self._error(f"Clicking stopped: {error}")

    def _capture(self, remaining: int = PICK_DELAY_S) -> None:
        if remaining:
            self._capture_btn.config(text=f"Move cursor... {remaining}", state=tk.DISABLED)
            self.root.after(MS_PER_S, self._capture, remaining - 1)
            return
        for key, value in zip(("x", "y"), self._engine.cursor):
            self._fields[key].set(str(value))
        self._pick.set(True)
        self._capture_btn.config(text=CAPTURE_TEXT, state=tk.NORMAL)

    def _change_hotkey(self) -> None:
        self._hotkey_text.set("Press a key...")
        self._hotkeys.capture_next()

    def _show_hotkey(self) -> None:
        self._hotkey_text.set(f"Start/Stop hotkey: {key_label(self._hotkeys.hotkey)}")

    def _refresh(self) -> None:
        steps = len(self._recorder.steps)
        noun, count = ("Recorded", steps) if self._state is State.RECORDING else ("Clicks", self._engine.clicks)
        self._status.set(f"Status: {self._state.value}  |  {noun}: {count}")
        self._steps_text.set(f"Steps: {steps}")

    def _poll(self) -> None:
        while not self._events.empty():
            self._events.get_nowait()()
        self._refresh()
        self.root.after(POLL_MS, self._poll)

    def _close(self) -> None:
        self._hotkeys.stop()
        self._recorder.stop()
        self._engine.shutdown()
        self.root.destroy()


def main() -> None:
    call_windows("shcore", "SetProcessDpiAwareness", DPI_PER_MONITOR)
    call_windows("winmm", "timeBeginPeriod", TIMER_RESOLUTION_MS)
    root = tk.Tk()
    root.title(APP_TITLE)
    root.resizable(False, False)
    AutoClickerApp(root)
    root.mainloop()
    call_windows("winmm", "timeEndPeriod", TIMER_RESOLUTION_MS)


if __name__ == "__main__":
    main()
