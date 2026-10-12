import random
import threading
import time
import tkinter as tk
from tkinter import messagebox, ttk
from typing import List, Optional, Tuple, Union

import pynput.keyboard as keyboard
import pynput.mouse as mouse

# Module-level constants
DEFAULT_HOTKEY: str = "Key.f6"
DEFAULT_INTERVAL_MS: float = 100.0
THREAD_POLL_INTERVAL: float = 0.01

# Mapping Tkinter labels to pynput mouse buttons
BUTTON_MAP = {
    "Left": mouse.Button.left,
    "Right": mouse.Button.right,
    "Middle": mouse.Button.middle,
}


class ClickEngine:
    """Handles auto-clicking, thread management, timing accuracy, and click sequences."""

    def __init__(self) -> None:
        self.mouse_controller = mouse.Controller()
        self.stop_event = threading.Event()
        self.click_thread: Optional[threading.Thread] = None
        self.record_listener: Optional[mouse.Listener] = None
        
        self.is_running: bool = False
        self.is_recording: bool = False
        self.click_count: int = 0
        self.recorded_events: List[Tuple[float, str, str, Tuple[int, int]]] = []
        
        self.status_callback = None

    def set_status_callback(self, callback) -> None:
        self.status_callback = callback

    def _update_status(self, text: str) -> None:
        if self.status_callback:
            self.status_callback(text)

    def start_clicking(
        self,
        interval_s: float,
        random_offset_ms: float,
        use_random: bool,
        button_name: str,
        click_type: str,
        repeat_until_stopped: bool,
        max_repeats: int,
        use_fixed_pos: bool,
        fixed_pos: Tuple[int, int],
        is_macro: bool = False,
        macro_loop: bool = False,
    ) -> None:
        if self.is_running:
            return

        self.is_running = True
        self.click_count = 0
        self.stop_event.clear()

        if is_macro:
            target_fn = self._macro_loop
            args = (macro_loop,)
        else:
            target_fn = self._click_loop
            args = (
                interval_s,
                random_offset_ms,
                use_random,
                button_name,
                click_type,
                repeat_until_stopped,
                max_repeats,
                use_fixed_pos,
                fixed_pos,
            )

        self.click_thread = threading.Thread(target=target_fn, args=args, daemon=True)
        self.click_thread.start()

    def stop_clicking(self) -> None:
        if not self.is_running:
            return
        self.stop_event.set()
        self.is_running = False
        self._update_status(f"Stopped. Total Clicks: {self.click_count}")

    def _click_loop(
        self,
        interval_s: float,
        random_offset_ms: float,
        use_random: bool,
        button_name: str,
        click_type: str,
        repeat_until_stopped: bool,
        max_repeats: int,
        use_fixed_pos: bool,
        fixed_pos: Tuple[int, int],
    ) -> None:
        btn = BUTTON_MAP.get(button_name, mouse.Button.left)
        clicks_to_do = 2 if click_type == "Double" else 1
        
        next_time = time.perf_counter()

        while not self.stop_event.is_set():
            if not repeat_until_stopped and self.click_count >= max_repeats:
                break

            if use_fixed_pos:
                self.mouse_controller.position = fixed_pos

            self.mouse_controller.click(btn, clicks_to_do)
            self.click_count += 1
            self._update_status(f"Running... Clicks: {self.click_count}")

            # Calculate interval with optional random jitter
            current_interval = interval_s
            if use_random and random_offset_ms > 0:
                jitter = random.uniform(-random_offset_ms, random_offset_ms) / 1000.0
                current_interval = max(0.001, interval_s + jitter)

            next_time += current_interval
            
            # Drift-compensated non-busy wait
            while time.perf_counter() < next_time:
                if self.stop_event.is_set():
                    break
                sleep_time = min(THREAD_POLL_INTERVAL, next_time - time.perf_counter())
                if sleep_time > 0:
                    time.sleep(sleep_time)

        self.is_running = False
        self._update_status(f"Finished/Stopped. Total Clicks: {self.click_count}")

    def start_recording((self) -> None:
        if self.is_recording:
            return
        self.recorded_events.clear()
        self.is_recording = True
        self.last_record_time = time.perf_counter()

        def on_click(x, y, button, pressed):
            if not self.is_recording:
                return False
            if pressed:
                now = time.perf_counter()
                delay = now - self.last_record_time
                self.last_record_time = now
                
                btn_str = "Left"
                if button == mouse.Button.right:
                    btn_str = "Right"
                elif button == mouse.Button.middle:
                    btn_str = "Middle"

                self.recorded_events.append((delay, btn_str, "Single", (x, y)))
                self._update_status(f"Recording... Captured {len(self.recorded_events)} events")

        self.record_listener = mouse.Listener(on_click=on_click)
        self.record_listener.start()
        self._update_status("Recording started...")

    def stop_recording() -> None:
        if not self.is_recording:
            return
        self.is_recording = False
        if self.record_listener:
            self.record_listener.stop()
        self._update_status(f"Recording stopped. {len(self.recorded_events)} clicks saved.")

    def _macro_loop(self, loop: bool) -> None:
        while not self.stop_event.is_set():
            for delay, btn_str, _, pos in self.recorded_events:
                if self.stop_event.is_set():
                    break

                # Drift-compensated wait before executing event
                target_time = time.perf_counter() + delay
                while time.perf_counter() < target_time:
                    if self.stop_event.is_set():
                        break
                    sleep_time = min(THREAD_POLL_INTERVAL, target_time - time.perf_counter())
                    if sleep_time > 0:
                        time.sleep(sleep_time)

                if self.stop_event.is_set():
                    break

                self.mouse_controller.position = pos
                self.mouse_controller.click(BUTTON_MAP[btn_str], 1)
                self.click_count += 1
                self._update_status(f"Replaying Macro... Clicks: {self.click_count}")

            if not loop:
                break

        self.is_running = False
        self._update_status(f"Macro complete. Total Clicks: {self.click_count}")


class AutoClickerGUI:
    """Handles layout, input validation, hotkeys, and GUI interactions."""

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("Python Auto Clicker")
        self.root.resizable(False, False)
        
        self.engine = ClickEngine()
        self.engine.set_status_callback(self._update_status_label)

        self.current_hotkey_str = "F6"
        self.pynput_hotkey = DEFAULT_HOTKEY
        self.keyboard_listener: Optional[keyboard.Listener] = None
        self.is_listening_for_hotkey = False

        self._build_gui()
        self._register_hotkey()

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build_gui(self) -> None:
        padding = {"padx": 5, "pady": 5}

        # --- Interval Frame ---
        interval_frame = ttk.LabelFrame(self.root, text="Click Interval")
        interval_frame.grid(row=0, column=0, columnspan=2, sticky="ew", **padding)

        self.hrs_var = tk.StringVar(value="0")
        self.mins_var = tk.StringVar(value="0")
        self.secs_var = tk.StringVar(value="0")
        self.ms_var = tk.StringVar(value="100")

        for i, (label, var) in enumerate(
            [("Hours:", self.hrs_var), ("Mins:", self.mins_var), ("Secs:", self.secs_var), ("MS:", self.ms_var)]
        ):
            ttk.Label(interval_frame, text=label).grid(row=0, column=i * 2, **padding)
            ttk.Entry(interval_frame, textvariable=var, width=5).grid(row=0, column=i * 2 + 1, **padding)

        # --- Random Offset Frame ---
        offset_frame = ttk.LabelFrame(self.root, text="Random Interval Offset")
        offset_frame.grid(row=1, column=0, columnspan=2, sticky="ew", **padding)

        self.use_random_var = tk.BooleanVar(value=False)
        self.random_ms_var = tk.StringVar(value="10")
        
        ttk.Checkbutton(offset_frame, text="Enable Random Offset (+/- MS):", variable=self.use_random_var).grid(
            row=0, column=0, **padding
        )
        ttk.Entry(offset_frame, textvariable=self.random_ms_var, width=6).grid(row=0, column=1, **padding)

        # --- Click Options Frame ---
        options_frame = ttk.LabelFrame(self.root, text="Click Options")
        options_frame.grid(row=2, column=0, **padding, sticky="nsew")

        self.btn_var = tk.StringVar(value="Left")
        self.type_var = tk.StringVar(value="Single")

        ttk.Label(options_frame, text="Button:").grid(row=0, column=0, **padding)
        ttk.OptionMenu(options_frame, self.btn_var, "Left", "Left", "Right", "Middle").grid(row=0, column=1, **padding)

        ttk.Label(options_frame, text="Type:").grid(row=1, column=0, **padding)
        ttk.OptionMenu(options_frame, self.type_var, "Single", "Single", "Double").grid(row=1, column=1, **padding)

        # --- Click Repeat Frame ---
        repeat_frame = ttk.LabelFrame(self.root, text="Click Repeat")
        repeat_frame.grid(row=2, column=1, **padding, sticky="nsew")

        self.repeat_mode_var = tk.StringVar(value="until_stopped")
        self.repeat_count_var = tk.StringVar(value="10")

        ttk.Radiobutton(
            repeat_frame, text="Repeat until stopped", variable=self.repeat_mode_var, value="until_stopped"
        ).grid(row=0, column=0, columnspan=2, sticky="w", **padding)
        ttk.Radiobutton(repeat_frame, text="Repeat (times):", variable=self.repeat_mode_var, value="count").grid(
            row=1, column=0, sticky="w", **padding
        )
        ttk.Entry(repeat_frame, textvariable=self.repeat_count_var, width=6).grid(row=1, column=1, **padding)

        # --- Cursor Position Frame ---
        pos_frame = ttk.LabelFrame(self.root, text="Cursor Position")
        pos_frame.grid(row=3, column=0, columnspan=2, sticky="ew", **padding)

        self.pos_mode_var = tk.StringVar(value="current")
        self.pos_x_var = tk.StringVar(value="0")
        self.pos_y_var = tk.StringVar(value="0")

        ttk.Radiobutton(pos_frame, text="Current location", variable=self.pos_mode_var, value="current").grid(
            row=0, column=0, sticky="w", **padding
        )
        ttk.Radiobutton(pos_frame, text="Pick location:", variable=self.pos_mode_var, value="fixed").grid(
            row=0, column=1, sticky="w", **padding
        )

        ttk.Label(pos_frame, text="X:").grid(row=0, column=2, **padding)
        ttk.Entry(pos_frame, textvariable=self.pos_x_var, width=5).grid(row=0, column=3, **padding)
        ttk.Label(pos_frame, text="Y:").grid(row=0, column=4, **padding)
        ttk.Entry(pos_frame, textvariable=self.pos_y_var, width=5).grid(row=0, column=5, **padding)
        
        self.pick_btn = ttk.Button(pos_frame, text="Capture Pos", command=self._capture_location)
        self.pick_btn.grid(row=0, column=6, **padding)

        # --- Macro / Record Frame ---
        macro_frame = ttk.LabelFrame(self.root, text="Record & Playback")
        macro_frame.grid(row=4, column=0, columnspan=2, sticky="ew", **padding)

        self.macro_loop_var = tk.BooleanVar(value=False)
        self.rec_btn = ttk.Button(macro_frame, text="Record", command=self._toggle_record)
        self.rec_btn.grid(row=0, column=0, **padding)
        
        self.play_btn = ttk.Button(macro_frame, text="Play Macro", command=self._play_macro)
        self.play_btn.grid(row=0, column=1, **padding)
        
        ttk.Checkbutton(macro_frame, text="Loop Macro", variable=self.macro_loop_var).grid(row=0, column=2, **padding)

        # --- Hotkey & Control Buttons ---
        ctrl_frame = ttk.Frame(self.root)
        ctrl_frame.grid(row=5, column=0, columnspan=2, sticky="ew", **padding)

        self.start_btn = ttk.Button(ctrl_frame, text="Start (F6)", command=self._start_clicking)
        self.start_btn.grid(row=0, column=0, padx=5)

        self.stop_btn = ttk.Button(ctrl_frame, text="Stop", command=self._stop_clicking, state=tk.DISABLED)
        self.stop_btn.grid(row=0, column=1, padx=5)

        self.hotkey_btn = ttk.Button(ctrl_frame, text="Set Hotkey", command=self._listen_for_new_hotkey)
        self.hotkey_btn.grid(row=0, column=2, padx=5)

        # --- Status Line ---
        self.status_var = tk.StringVar(value="Status: Idle")
        status_label = ttk.Label(self.root, textvariable=self.status_var, relief=tk.SUNKEN, anchor="w")
        status_label.grid(row=6, column=0, columnspan=2, sticky="ew", padx=5, pady=5)

    def _update_status_label(self, text: str) -> None:
        self.root.after(0, lambda: self.status_var.set(f"Status: {text}"))

    def _validate_inputs(self) -> Optional[Tuple[float, float, int, Tuple[int, int]]]:
        try:
            h = float(self.hrs_var.get() or 0)
            m = float(self.mins_var.get() or 0)
            s = float(self.secs_var.get() or 0)
            ms = float(self.ms_var.get() or 0)

            if min(h, m, s, ms) < 0:
                raise ValueError("Interval values cannot be negative.")

            total_interval_s = (h * 3600) + (m * 60) + s + (ms / 1000.0)
            if total_interval_s <= 0:
                raise ValueError("Total interval must be greater than 0.")

            random_ms = float(self.random_ms_var.get() or 0)
            if random_ms < 0:
                raise ValueError("Random offset cannot be negative.")

            repeats = int(self.repeat_count_var.get() or 1)
            if repeats <= 0 and self.repeat_mode_var.get() == "count":
                raise ValueError("Repeat count must be greater than 0.")

            x = int(self.pos_x_var.get() or 0)
            y = int(self.pos_y_var.get() or 0)

            return total_interval_s, random_ms, repeats, (x, y)
        except ValueError as err:
            messagebox.showerror("Input Error", str(err))
            return None

    def _start_clicking() -> None:
        data = self._validate_inputs()
        if not data:
            return

        interval_s, random_ms, repeats, pos = data
        self.start_btn.config(state=tk.DISABLED)
        self.stop_btn.config(state=tk.NORMAL)

        self.engine.start_clicking(
            interval_s=interval_s,
            random_offset_ms=random_ms,
            use_random=self.use_random_var.get(),
            button_name=self.btn_var.get(),
            click_type=self.type_var.get(),
            repeat_until_stopped=(self.repeat_mode_var.get() == "until_stopped"),
            max_repeats=repeats,
            use_fixed_pos=(self.pos_mode_var.get() == "fixed"),
            fixed_pos=pos,
        )
        self._poll_engine_state()

    def _stop_clicking() -> None:
        self.engine.stop_clicking()
        self.start_btn.config(state=tk.NORMAL)
        self.stop_btn.config(state=tk.DISABLED)

    def _toggle_clicking() -> None:
        if self.engine.is_running:
            self._stop_clicking()
        else:
            self._start_clicking()

    def _poll_engine_state() -> None:
        if self.engine.is_running:
            self.root.after(200, self._poll_engine_state)
        else:
            self.start_btn.config(state=tk.NORMAL)
            self.stop_btn.config(state=tk.DISABLED)

    def _capture_location() -> None:
        self.status_var.set("Status: Click anywhere in 2 seconds to capture position...")
        self.root.update()
        time.sleep(2)
        pos = mouse.Controller().position
        self.pos_x_var.set(str(pos[0]))
        self.pos_y_var.set(str(pos[1]))
        self.pos_mode_var.set("fixed")
        self.status_var.set(f"Status: Captured Position ({pos[0]}, {pos[1]})")

    def _toggle_record() -> None:
        if self.engine.is_recording:
            self.engine.stop_recording()
            self.rec_btn.config(text="Record")
        else:
            self.rec_btn.config(text="Stop Rec")
            self.engine.start_recording()

    def _play_macro() -> None:
        if not self.engine.recorded_events:
            messagebox.showwarning("Warning", "No recorded actions to play!")
            return
        
        self.start_btn.config(state=tk.DISABLED)
        self.stop_btn.config(state=tk.NORMAL)
        
        self.engine.start_clicking(
            interval_s=0,
            random_offset_ms=0,
            use_random=False,
            button_name="Left",
            click_type="Single",
            repeat_until_stopped=False,
            max_repeats=1,
            use_fixed_pos=False,
            fixed_pos=(0, 0),
            is_macro=True,
            macro_loop=self.macro_loop_var.get(),
        )
        self._poll_engine_state()

    def _register_hotkey() -> None:
        if self.keyboard_listener:
            self.keyboard_listener.stop()

        def on_press(key):
            try:
                key_str = str(key)
                if key_str == self.pynput_hotkey:
                    self.root.after(0, self._toggle_clicking)
            except Exception:
                pass

        self.keyboard_listener = keyboard.Listener(on_press=on_press)
        self.keyboard_listener.start()

    def _listen_for_new_hotkey() -> None:
        self.hotkey_btn.config(text="Press Any Key...")
        self.status_var.set("Status: Press a key to set as hotkey...")

        if self.keyboard_listener:
            self.keyboard_listener.stop()

        def on_single_press(key):
            self.pynput_hotkey = str(key)
            # Format display label cleanly
            clean_name = str(key).replace("Key.", "").replace("'", "").upper()
            self.current_hotkey_str = clean_name
            
            self.root.after(0, lambda: self._finalize_hotkey(clean_name))
            return False

        temp_listener = keyboard.Listener(on_press=on_single_press)
        temp_listener.start()

    def _finalize_hotkey(self, name: str) -> None:
        self.hotkey_btn.config(text="Set Hotkey")
        self.start_btn.config(text=f"Start ({name})")
        self.status_var.set(f"Status: Hotkey updated to {name}")
        self._register_hotkey()

    def _on_close() -> None:
        self.engine.stop_clicking()
        self.engine.stop_recording()
        if self.keyboard_listener:
            self.keyboard_listener.stop()
        self.root.destroy()


if __name__ == "__main__":
    root = tk.Tk()
    app = AutoClickerGUI(root)
    root.mainloop()
