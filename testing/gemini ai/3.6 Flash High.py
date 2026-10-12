import enum
import random
import threading
import time
import tkinter as tk
from dataclasses import dataclass, field
from tkinter import messagebox, ttk
from typing import Callable, List, Optional, Tuple

from pynput import keyboard, mouse

# Module-level Constants
DEFAULT_HOTKEY: str = "F6"
DEFAULT_INTERVAL_MS: int = 100
MIN_TOTAL_INTERVAL_SEC: float = 0.0001
WINDOW_TITLE: str = "Auto Clicker"
WINDOW_SIZE: str = "460x540"


class WorkMode(enum.Enum):
    INTERVAL = "interval"
    PLAYBACK = "playback"


class RepeatMode(enum.Enum):
    UNLIMITED = "unlimited"
    COUNT = "count"


class TargetPositionMode(enum.Enum):
    CURRENT = "current"
    FIXED = "fixed"


class ClickType(enum.Enum):
    SINGLE = "single"
    DOUBLE = "double"


class MouseButton(enum.Enum):
    LEFT = "left"
    RIGHT = "right"
    MIDDLE = "middle"


@dataclass
class ClickEvent:
    x: int
    y: int
    button: MouseButton
    delay: float


@dataclass
class EngineConfig:
    mode: WorkMode = WorkMode.INTERVAL
    base_interval: float = 0.1
    use_random_offset: bool = False
    random_offset_max: float = 0.0
    button: MouseButton = MouseButton.LEFT
    click_type: ClickType = ClickType.SINGLE
    repeat_mode: RepeatMode = RepeatMode.UNLIMITED
    repeat_count: int = 100
    pos_mode: TargetPositionMode = TargetPositionMode.CURRENT
    fixed_pos: Tuple[int, int] = (0, 0)
    recorded_sequence: List[ClickEvent] = field(default_factory=list)
    loop_playback: bool = False


class HotkeyManager:
    """Listens for global keypresses to trigger start/stop actions even when unfocused."""

    def __init__(self, callback: Callable[[], None], initial_hotkey: str = DEFAULT_HOTKEY) -> None:
        self.callback = callback
        self.hotkey_str: str = initial_hotkey.strip().upper()
        self._listener: Optional[keyboard.Listener] = None
        self.start()

    def set_hotkey(self, key_str: str) -> None:
        self.hotkey_str = key_str.strip().upper()

    def start(self) -> None:
        self.stop()
        self._listener = keyboard.Listener(on_press=self._on_press)
        self._listener.daemon = True
        self._listener.start()

    def _on_press(self, key) -> None:
        pressed_key = self._get_key_str(key)
        if pressed_key and pressed_key == self.hotkey_str:
            self.callback()

    @staticmethod
    def _get_key_str(key) -> str:
        if isinstance(key, keyboard.Key):
            return key.name.upper() if key.name else ""
        if hasattr(key, "char") and key.char:
            return key.char.upper()
        return ""

    def stop(self) -> None:
        if self._listener and self._listener.is_alive():
            self._listener.stop()
            self._listener = None


class PositionPicker:
    """Captures the next mouse click coordinates on screen."""

    def __init__(self, on_picked: Callable[[int, int], None]) -> None:
        self.on_picked = on_picked
        self._listener: Optional[mouse.Listener] = None
        self._has_pressed: bool = False

    def start(self) -> None:
        self.stop()
        self._has_pressed = False
        self._listener = mouse.Listener(on_click=self._on_click)
        self._listener.daemon = True
        self._listener.start()

    def _on_click(self, x: int, y: int, button: mouse.Button, pressed: bool) -> Optional[bool]:
        if pressed:
            self._has_pressed = True
        elif self._has_pressed:
            self.on_picked(int(x), int(y))
            return False
        return None

    def stop(self) -> None:
        if self._listener and self._listener.is_alive():
            self._listener.stop()
            self._listener = None


class MacroRecorder:
    """Records user mouse clicks and relative delays between clicks."""

    def __init__(self) -> None:
        self.recorded_clicks: List[ClickEvent] = []
        self._listener: Optional[mouse.Listener] = None
        self._last_time: float = 0.0
        self._is_recording: bool = False
        self._has_pressed: bool = False

    @property
    def is_recording(self) -> bool:
        return self._is_recording

    def start(self) -> None:
        self.recorded_clicks.clear()
        self._is_recording = True
        self._has_pressed = False
        self._last_time = time.perf_counter()
        self._listener = mouse.Listener(on_click=self._on_click)
        self._listener.daemon = True
        self._listener.start()

    def _on_click(self, x: int, y: int, button: mouse.Button, pressed: bool) -> None:
        if not self._is_recording:
            return

        if not self._has_pressed:
            if pressed:
                self._has_pressed = True
                self._last_time = time.perf_counter()
            return

        if pressed:
            now = time.perf_counter()
            delay = now - self._last_time
            self._last_time = now

            btn_enum = MouseButton.LEFT
            if button == mouse.Button.right:
                btn_enum = MouseButton.RIGHT
            elif button == mouse.Button.middle:
                btn_enum = MouseButton.MIDDLE

            self.recorded_clicks.append(ClickEvent(x=int(x), y=int(y), button=btn_enum, delay=delay))

    def stop(self, discard_last: bool = True) -> List[ClickEvent]:
        self._is_recording = False
        if self._listener and self._listener.is_alive():
            self._listener.stop()
            self._listener = None
        if discard_last and self.recorded_clicks:
            self.recorded_clicks.pop()
        return list(self.recorded_clicks)


class AutoClickerEngine:
    """Background engine managing precise click loops and macro replay."""

    def __init__(self, on_status_change: Callable[[str, int], None]) -> None:
        self.on_status_change = on_status_change
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._mouse_controller = mouse.Controller()
        self._clicks_performed: int = 0
        self._is_running: bool = False

    @property
    def is_running(self) -> bool:
        return self._is_running

    @property
    def clicks_performed(self) -> int:
        return self._clicks_performed

    def start(self, config: EngineConfig) -> None:
        if self._is_running:
            return
        self._stop_event.clear()
        self._clicks_performed = 0
        self._is_running = True
        self._thread = threading.Thread(target=self._run, args=(config,), daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if not self._is_running:
            return
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.0)
        self._is_running = False
        self.on_status_change("Stopped", self._clicks_performed)

    def _perform_click(self, button: MouseButton, click_type: ClickType, pos: Optional[Tuple[int, int]]) -> None:
        if pos is not None:
            self._mouse_controller.position = pos

        pbtn = mouse.Button.left
        if button == MouseButton.RIGHT:
            pbtn = mouse.Button.right
        elif button == MouseButton.MIDDLE:
            pbtn = mouse.Button.middle

        count = 2 if click_type == ClickType.DOUBLE else 1
        self._mouse_controller.click(pbtn, count)
        self._clicks_performed += count

    def _run(self, config: EngineConfig) -> None:
        self.on_status_change("Running", self._clicks_performed)
        try:
            if config.mode == WorkMode.INTERVAL:
                self._run_interval(config)
            elif config.mode == WorkMode.PLAYBACK:
                self._run_playback(config)
        except Exception:
            pass
        finally:
            self._is_running = False
            self.on_status_change("Stopped", self._clicks_performed)

    def _run_interval(self, config: EngineConfig) -> None:
        target_pos = config.fixed_pos if config.pos_mode == TargetPositionMode.FIXED else None
        clicks_done = 0
        next_time = time.perf_counter()

        while not self._stop_event.is_set():
            if config.repeat_mode == RepeatMode.COUNT and clicks_done >= config.repeat_count:
                break

            interval = config.base_interval
            if config.use_random_offset and config.random_offset_max > 0:
                interval += random.uniform(0, config.random_offset_max)

            self._perform_click(config.button, config.click_type, target_pos)
            clicks_done += 1
            self.on_status_change("Running", self._clicks_performed)

            if config.repeat_mode == RepeatMode.COUNT and clicks_done >= config.repeat_count:
                break

            next_time += interval
            sleep_sec = next_time - time.perf_counter()
            if sleep_sec > 0:
                if self._stop_event.wait(timeout=sleep_sec):
                    break
            else:
                next_time = time.perf_counter()

    def _run_playback(self, config: EngineConfig) -> None:
        if not config.recorded_sequence:
            return

        while not self._stop_event.is_set():
            for evt in config.recorded_sequence:
                if self._stop_event.is_set():
                    break

                if evt.delay > 0:
                    if self._stop_event.wait(timeout=evt.delay):
                        break

                if self._stop_event.is_set():
                    break

                self._perform_click(evt.button, ClickType.SINGLE, (evt.x, evt.y))
                self.on_status_change("Running", self._clicks_performed)

            if not config.loop_playback:
                break


class AutoClickerGUI:
    """Tkinter graphical user interface for the auto clicker application."""

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title(WINDOW_TITLE)
        self.root.geometry(WINDOW_SIZE)
        self.root.resizable(False, False)

        self.engine = AutoClickerEngine(on_status_change=self._on_status_change)
        self.hotkey_manager = HotkeyManager(callback=self._on_hotkey_triggered, initial_hotkey=DEFAULT_HOTKEY)
        self.picker = PositionPicker(on_picked=self._on_pos_picked)
        self.recorder = MacroRecorder()
        self.recorded_clicks: List[ClickEvent] = []

        self.work_mode_var = tk.StringVar(value="interval")
        self.button_var = tk.StringVar(value="Left")
        self.click_type_var = tk.StringVar(value="Single")
        self.repeat_var = tk.StringVar(value="unlimited")
        self.pos_var = tk.StringVar(value="current")
        self.random_var = tk.BooleanVar(value=False)
        self.loop_playback_var = tk.BooleanVar(value=False)
        self.status_var = tk.StringVar(value="Status: Idle | Clicks: 0")
        self.rec_status_var = tk.StringVar(value="0 clicks recorded")

        self._build_ui()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    def _build_ui(self) -> None:
        main_frame = ttk.Frame(self.root, padding=10)
        main_frame.pack(fill=tk.BOTH, expand=True)

        mode_frame = ttk.LabelFrame(main_frame, text=" Mode Selection ", padding=5)
        mode_frame.pack(fill=tk.X, pady=3)
        ttk.Radiobutton(mode_frame, text="Interval Auto Clicker", variable=self.work_mode_var, value="interval").pack(side=tk.LEFT, padx=15)
        ttk.Radiobutton(mode_frame, text="Record & Playback Macro", variable=self.work_mode_var, value="playback").pack(side=tk.LEFT, padx=15)

        interval_frame = ttk.LabelFrame(main_frame, text=" Click Interval ", padding=5)
        interval_frame.pack(fill=tk.X, pady=3)

        sub_int = ttk.Frame(interval_frame)
        sub_int.pack(fill=tk.X)

        ttk.Label(sub_int, text="Hours:").grid(row=0, column=0, padx=2)
        self.hrs_entry = ttk.Entry(sub_int, width=5)
        self.hrs_entry.insert(0, "0")
        self.hrs_entry.grid(row=0, column=1, padx=4)

        ttk.Label(sub_int, text="Mins:").grid(row=0, column=2, padx=2)
        self.mins_entry = ttk.Entry(sub_int, width=5)
        self.mins_entry.insert(0, "0")
        self.mins_entry.grid(row=0, column=3, padx=4)

        ttk.Label(sub_int, text="Secs:").grid(row=0, column=4, padx=2)
        self.secs_entry = ttk.Entry(sub_int, width=5)
        self.secs_entry.insert(0, "0")
        self.secs_entry.grid(row=0, column=5, padx=4)

        ttk.Label(sub_int, text="Ms:").grid(row=0, column=6, padx=2)
        self.ms_entry = ttk.Entry(sub_int, width=6)
        self.ms_entry.insert(0, str(DEFAULT_INTERVAL_MS))
        self.ms_entry.grid(row=0, column=7, padx=4)

        rand_frame = ttk.Frame(interval_frame)
        rand_frame.pack(fill=tk.X, pady=(4, 0))
        ttk.Checkbutton(rand_frame, text="Random Offset (+ ms):", variable=self.random_var).pack(side=tk.LEFT)
        self.random_entry = ttk.Entry(rand_frame, width=6)
        self.random_entry.insert(0, "0")
        self.random_entry.pack(side=tk.LEFT, padx=5)

        opt_frame = ttk.LabelFrame(main_frame, text=" Click Options ", padding=5)
        opt_frame.pack(fill=tk.X, pady=3)

        ttk.Label(opt_frame, text="Mouse Button:").grid(row=0, column=0, padx=5, sticky=tk.W)
        ttk.OptionMenu(opt_frame, self.button_var, "Left", "Left", "Right", "Middle").grid(row=0, column=1, padx=5, sticky=tk.W)

        ttk.Label(opt_frame, text="Click Type:").grid(row=0, column=2, padx=15, sticky=tk.W)
        ttk.OptionMenu(opt_frame, self.click_type_var, "Single", "Single", "Double").grid(row=0, column=3, padx=5, sticky=tk.W)

        repeat_frame = ttk.LabelFrame(main_frame, text=" Click Repeat ", padding=5)
        repeat_frame.pack(fill=tk.X, pady=3)

        ttk.Radiobutton(repeat_frame, text="Repeat until stopped", variable=self.repeat_var, value="unlimited").pack(anchor=tk.W)
        rep_cnt_frame = ttk.Frame(repeat_frame)
        rep_cnt_frame.pack(anchor=tk.W, pady=2)
        ttk.Radiobutton(rep_cnt_frame, text="Repeat N times:", variable=self.repeat_var, value="count").pack(side=tk.LEFT)
        self.repeat_entry = ttk.Entry(rep_cnt_frame, width=8)
        self.repeat_entry.insert(0, "100")
        self.repeat_entry.pack(side=tk.LEFT, padx=5)

        pos_frame = ttk.LabelFrame(main_frame, text=" Cursor Position ", padding=5)
        pos_frame.pack(fill=tk.X, pady=3)

        ttk.Radiobutton(pos_frame, text="Current location", variable=self.pos_var, value="current").pack(anchor=tk.W)
        fixed_pos_frame = ttk.Frame(pos_frame)
        fixed_pos_frame.pack(anchor=tk.W, pady=2)
        ttk.Radiobutton(fixed_pos_frame, text="Pick location", variable=self.pos_var, value="fixed").pack(side=tk.LEFT)
        ttk.Label(fixed_pos_frame, text="X:").pack(side=tk.LEFT, padx=(10, 2))
        self.x_entry = ttk.Entry(fixed_pos_frame, width=6)
        self.x_entry.insert(0, "0")
        self.x_entry.pack(side=tk.LEFT)
        ttk.Label(fixed_pos_frame, text="Y:").pack(side=tk.LEFT, padx=(10, 2))
        self.y_entry = ttk.Entry(fixed_pos_frame, width=6)
        self.y_entry.insert(0, "0")
        self.y_entry.pack(side=tk.LEFT)
        self.pick_btn = ttk.Button(fixed_pos_frame, text="Capture Pos", command=self.start_pick_pos)
        self.pick_btn.pack(side=tk.LEFT, padx=10)

        rec_frame = ttk.LabelFrame(main_frame, text=" Record & Playback ", padding=5)
        rec_frame.pack(fill=tk.X, pady=3)

        rec_btn_sub = ttk.Frame(rec_frame)
        rec_btn_sub.pack(fill=tk.X)

        self.rec_start_btn = ttk.Button(rec_btn_sub, text="Start Record", command=self.start_recording)
        self.rec_start_btn.pack(side=tk.LEFT, padx=3)
        self.rec_stop_btn = ttk.Button(rec_btn_sub, text="Stop Record", command=self.stop_recording, state=tk.DISABLED)
        self.rec_stop_btn.pack(side=tk.LEFT, padx=3)
        ttk.Checkbutton(rec_btn_sub, text="Loop Playback", variable=self.loop_playback_var).pack(side=tk.LEFT, padx=10)

        ttk.Label(rec_frame, textvariable=self.rec_status_var, font=("Segoe UI", 8, "italic")).pack(anchor=tk.W, pady=(2, 0))

        hotkey_frame = ttk.LabelFrame(main_frame, text=" Global Hotkey ", padding=5)
        hotkey_frame.pack(fill=tk.X, pady=3)

        ttk.Label(hotkey_frame, text="Start / Stop Key:").pack(side=tk.LEFT, padx=5)
        self.hotkey_entry = ttk.Entry(hotkey_frame, width=8)
        self.hotkey_entry.insert(0, DEFAULT_HOTKEY)
        self.hotkey_entry.pack(side=tk.LEFT, padx=5)
        self.apply_hotkey_btn = ttk.Button(hotkey_frame, text="Apply Hotkey", command=self.apply_hotkey)
        self.apply_hotkey_btn.pack(side=tk.LEFT, padx=5)

        ctrl_frame = ttk.Frame(main_frame, padding=5)
        ctrl_frame.pack(fill=tk.X, pady=5)

        self.start_btn = tk.Button(
            ctrl_frame,
            text=f"Start ({DEFAULT_HOTKEY})",
            bg="#28a745",
            fg="white",
            font=("Segoe UI", 10, "bold"),
            command=self.start_clicking,
            height=2,
        )
        self.start_btn.pack(side=tk.LEFT, expand=True, fill=tk.X, padx=5)

        self.stop_btn = tk.Button(
            ctrl_frame,
            text=f"Stop ({DEFAULT_HOTKEY})",
            bg="#dc3545",
            fg="white",
            font=("Segoe UI", 10, "bold"),
            command=self.stop_clicking,
            state=tk.DISABLED,
            height=2,
        )
        self.stop_btn.pack(side=tk.LEFT, expand=True, fill=tk.X, padx=5)

        status_bar = ttk.Label(main_frame, textvariable=self.status_var, relief=tk.SUNKEN, anchor=tk.W, padding=4)
        status_bar.pack(fill=tk.X, side=tk.BOTTOM, pady=(5, 0))

    def validate_and_build_config(self) -> Optional[EngineConfig]:
        mode = WorkMode.INTERVAL if self.work_mode_var.get() == "interval" else WorkMode.PLAYBACK

        if mode == WorkMode.PLAYBACK:
            if not self.recorded_clicks:
                messagebox.showerror("Error", "No recorded sequence available. Please record clicks first.")
                return None
            return EngineConfig(
                mode=WorkMode.PLAYBACK,
                recorded_sequence=list(self.recorded_clicks),
                loop_playback=self.loop_playback_var.get(),
            )

        hrs_s, mins_s = self.hrs_entry.get().strip(), self.mins_entry.get().strip()
        secs_s, ms_s = self.secs_entry.get().strip(), self.ms_entry.get().strip()

        if not (hrs_s.isdigit() and mins_s.isdigit() and secs_s.isdigit() and ms_s.isdigit()):
            messagebox.showerror("Invalid Input", "Interval values must be non-negative integers.")
            return None

        hrs, mins, secs, ms = int(hrs_s), int(mins_s), int(secs_s), int(ms_s)
        total_sec = hrs * 3600 + mins * 60 + secs + ms / 1000.0

        if total_sec <= MIN_TOTAL_INTERVAL_SEC:
            messagebox.showerror("Invalid Input", "Total click interval must be greater than 0.")
            return None

        use_rand = self.random_var.get()
        rand_offset_sec = 0.0
        if use_rand:
            rand_s = self.random_entry.get().strip()
            try:
                rand_ms = float(rand_s)
                if rand_ms < 0:
                    messagebox.showerror("Invalid Input", "Random offset cannot be negative.")
                    return None
                rand_offset_sec = rand_ms / 1000.0
            except ValueError:
                messagebox.showerror("Invalid Input", "Random offset must be a valid number.")
                return None

        rep_mode = RepeatMode.COUNT if self.repeat_var.get() == "count" else RepeatMode.UNLIMITED
        rep_count = 100
        if rep_mode == RepeatMode.COUNT:
            rep_s = self.repeat_entry.get().strip()
            if not rep_s.isdigit() or int(rep_s) <= 0:
                messagebox.showerror("Invalid Input", "Repeat count must be a positive integer.")
                return None
            rep_count = int(rep_s)

        pos_mode = TargetPositionMode.FIXED if self.pos_var.get() == "fixed" else TargetPositionMode.CURRENT
        fixed_x, fixed_y = 0, 0
        if pos_mode == TargetPositionMode.FIXED:
            x_s, y_s = self.x_entry.get().strip(), self.y_entry.get().strip()
            if not (x_s.isdigit() and y_s.isdigit()):
                messagebox.showerror("Invalid Input", "Position coordinates must be non-negative integers.")
                return None
            fixed_x, fixed_y = int(x_s), int(y_s)

        btn_str = self.button_var.get().lower()
        btn_enum = MouseButton.LEFT
        if btn_str == "right":
            btn_enum = MouseButton.RIGHT
        elif btn_str == "middle":
            btn_enum = MouseButton.MIDDLE

        click_type = ClickType.DOUBLE if self.click_type_var.get().lower() == "double" else ClickType.SINGLE

        return EngineConfig(
            mode=WorkMode.INTERVAL,
            base_interval=total_sec,
            use_random_offset=use_rand,
            random_offset_max=rand_offset_sec,
            button=btn_enum,
            click_type=click_type,
            repeat_mode=rep_mode,
            repeat_count=rep_count,
            pos_mode=pos_mode,
            fixed_pos=(fixed_x, fixed_y),
        )

    def apply_hotkey(self) -> None:
        key_str = self.hotkey_entry.get().strip().upper()
        if not key_str:
            messagebox.showerror("Invalid Hotkey", "Hotkey cannot be empty.")
            return
        self.hotkey_manager.set_hotkey(key_str)
        self.start_btn.config(text=f"Start ({key_str})")
        self.stop_btn.config(text=f"Stop ({key_str})")
        self.status_var.set(f"Hotkey updated to {key_str}")

    def start_pick_pos(self) -> None:
        self.status_var.set("Click anywhere on screen to pick position...")
        self.picker.start()

    def _on_pos_picked(self, x: int, y: int) -> None:
        self.root.after(0, lambda: self._apply_picked_pos(x, y))

    def _apply_picked_pos(self, x: int, y: int) -> None:
        self.x_entry.delete(0, tk.END)
        self.x_entry.insert(0, str(x))
        self.y_entry.delete(0, tk.END)
        self.y_entry.insert(0, str(y))
        self.pos_var.set("fixed")
        self.status_var.set(f"Picked location: X={x}, Y={y}")

    def start_recording(self) -> None:
        self.recorder.start()
        self.rec_start_btn.config(state=tk.DISABLED)
        self.rec_stop_btn.config(state=tk.NORMAL)
        self.rec_status_var.set("Recording clicks... Click anywhere on screen.")

    def stop_recording(self) -> None:
        clicks = self.recorder.stop(discard_last=True)
        self.recorded_clicks = clicks
        self.rec_start_btn.config(state=tk.NORMAL)
        self.rec_stop_btn.config(state=tk.DISABLED)
        self.rec_status_var.set(f"{len(clicks)} clicks recorded.")

    def _on_hotkey_triggered(self) -> None:
        self.root.after(0, self.toggle_start_stop)

    def toggle_start_stop(self) -> None:
        if self.engine.is_running:
            self.stop_clicking()
        else:
            self.start_clicking()

    def start_clicking(self) -> None:
        config = self.validate_and_build_config()
        if config is None:
            return
        self.engine.start(config)

    def stop_clicking(self) -> None:
        self.engine.stop()

    def _on_status_change(self, state: str, clicks: int) -> None:
        self.root.after(0, lambda: self._update_ui_status(state, clicks))

    def _update_ui_status(self, state: str, clicks: int) -> None:
        self.status_var.set(f"Status: {state} | Clicks: {clicks}")
        if state == "Running":
            self.start_btn.config(state=tk.DISABLED)
            self.stop_btn.config(state=tk.NORMAL)
        else:
            self.start_btn.config(state=tk.NORMAL)
            self.stop_btn.config(state=tk.DISABLED)

    def on_close(self) -> None:
        self.engine.stop()
        self.recorder.stop()
        self.picker.stop()
        self.hotkey_manager.stop()
        self.root.destroy()


if __name__ == "__main__":
    tk_root = tk.Tk()
    app = AutoClickerGUI(tk_root)
    tk_root.mainloop()
