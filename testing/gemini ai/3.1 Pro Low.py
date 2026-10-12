import tkinter as tk
from tkinter import ttk, messagebox
import threading
import time
import random
from typing import Optional, List, Dict, Any, Callable
from pynput.mouse import Controller as MouseController, Button as MouseButton, Listener as MouseListener
from pynput.keyboard import Listener as KeyboardListener, Key, KeyCode

# --- CONSTANTS ---
DEFAULT_HOTKEY = "<117>"  # F6 in pynput keycodes (platform dependent, using strings for mapping)
DEFAULT_INTERVAL_MS = 100
MAX_SLEEP_WAIT = 0.01

class ClickEngine:
    """Handles all click logic, timing, and recording in background threads."""
    def __init__(self, update_status_cb: Callable[[str, int], None]):
        self.mouse = MouseController()
        self.update_status = update_status_cb
        
        self.running = False
        self.stop_event = threading.Event()
        self.click_thread: Optional[threading.Thread] = None
        
        self.clicks_done = 0
        self.hotkey = Key.f6
        self.key_listener: Optional[KeyboardListener] = None
        
        # Recording state
        self.is_recording = False
        self.recorded_events: List[Dict[str, Any]] = []
        self.record_start_time = 0.0
        self.mouse_listener: Optional[MouseListener] = None

    def start_keyboard_listener(self, toggle_cb: Callable[[], None]):
        def on_press(key):
            # Check if key matches our configured hotkey (simplified check)
            if str(key) == str(self.hotkey) or key == self.hotkey:
                toggle_cb()
                
        self.key_listener = KeyboardListener(on_press=on_press)
        self.key_listener.start()

    def stop_keyboard_listener(self):
        if self.key_listener:
            self.key_listener.stop()

    def update_hotkey(self, key_str: str):
        key_str = key_str.lower()
        if hasattr(Key, key_str):
            self.hotkey = getattr(Key, key_str)
        else:
            self.hotkey = KeyCode.from_char(key_str)

    def start_clicking(self, interval: float, offset_ms: float, button: str, click_type: str, 
                       repeat_type: str, repeat_count: int, use_location: bool, loc_x: int, loc_y: int):
        if self.running or self.is_recording:
            return
        
        self.running = True
        self.stop_event.clear()
        self.clicks_done = 0
        
        btn = MouseButton.left
        if button == "Right": btn = MouseButton.right
        elif button == "Middle": btn = MouseButton.middle
        
        is_double = (click_type == "Double")
        limit = repeat_count if repeat_type == "Repeat N times" else -1
        
        self.click_thread = threading.Thread(
            target=self._click_loop, 
            args=(interval, offset_ms, btn, is_double, limit, use_location, loc_x, loc_y),
            daemon=True
        )
        self.click_thread.start()

    def _click_loop(self, base_interval: float, offset_ms: float, button: MouseButton, 
                    is_double: bool, limit: int, use_loc: bool, x: int, y: int):
        self.update_status("Running", self.clicks_done)
        next_time = time.perf_counter()
        
        while not self.stop_event.is_set():
            if limit != -1 and self.clicks_done >= limit:
                break
                
            if use_loc:
                self.mouse.position = (x, y)
                
            self.mouse.click(button, 2 if is_double else 1)
            self.clicks_done += 1
            self.update_status("Running", self.clicks_done)
            
            # Calculate sleep with drift compensation and random offset
            offset = random.uniform(0, offset_ms / 1000.0) if offset_ms > 0 else 0
            interval = base_interval + offset
            next_time += interval
            
            sleep_time = next_time - time.perf_counter()
            if sleep_time > 0:
                self.stop_event.wait(sleep_time)
            else:
                next_time = time.perf_counter() # Reset drift if we fall behind

        self.running = False
        self.update_status("Stopped", self.clicks_done)

    def stop_clicking(self):
        if self.running:
            self.stop_event.set()
            self.running = False
            self.update_status("Stopped", self.clicks_done)

    # --- Recording and Playback ---
    def toggle_recording(self) -> bool:
        if self.running: return False
        
        if self.is_recording:
            self.is_recording = False
            if self.mouse_listener:
                self.mouse_listener.stop()
            self.update_status("Recording Finished", len(self.recorded_events))
            return False
        else:
            self.is_recording = True
            self.recorded_events.clear()
            self.record_start_time = time.perf_counter()
            
            def on_click(x, y, button, pressed):
                if pressed:
                    delay = time.perf_counter() - self.record_start_time
                    self.recorded_events.append({"x": x, "y": y, "button": button, "delay": delay})
                    self.record_start_time = time.perf_counter()
                    
            self.mouse_listener = MouseListener(on_click=on_click)
            self.mouse_listener.start()
            self.update_status("Recording...", 0)
            return True

    def play_recording(self, loop: bool):
        if self.running or self.is_recording or not self.recorded_events:
            return
            
        self.running = True
        self.stop_event.clear()
        self.click_thread = threading.Thread(target=self._playback_loop, args=(loop,), daemon=True)
        self.click_thread.start()

    def _playback_loop(self, loop: bool):
        self.update_status("Playing macro", 0)
        plays = 0
        while not self.stop_event.is_set():
            for event in self.recorded_events:
                if self.stop_event.is_set():
                    break
                self.stop_event.wait(event["delay"])
                self.mouse.position = (event["x"], event["y"])
                self.mouse.click(event["button"], 1)
            
            plays += 1
            self.update_status("Playing macro", plays)
            if not loop:
                break
                
        self.running = False
        self.update_status("Stopped", plays)

class AutoClickerGUI:
    """Handles the Tkinter interface and input validation."""
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Auto Clicker")
        self.root.resizable(False, False)
        
        self.engine = ClickEngine(self.update_status_bar)
        
        self._build_gui()
        self.engine.start_keyboard_listener(self.toggle_clicking)
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    def _build_gui(self):
        main_frame = ttk.Frame(self.root, padding=10)
        main_frame.pack(fill=tk.BOTH, expand=True)

        # 1. Interval
        frame_interval = ttk.LabelFrame(main_frame, text="Click Interval", padding=5)
        frame_interval.pack(fill=tk.X, pady=5)
        
        self.h_var, self.m_var, self.s_var, self.ms_var = tk.StringVar(value="0"), tk.StringVar(value="0"), tk.StringVar(value="0"), tk.StringVar(value="100")
        ttk.Entry(frame_interval, textvariable=self.h_var, width=5).pack(side=tk.LEFT, padx=2)
        ttk.Label(frame_interval, text="hours").pack(side=tk.LEFT)
        ttk.Entry(frame_interval, textvariable=self.m_var, width=5).pack(side=tk.LEFT, padx=2)
        ttk.Label(frame_interval, text="mins").pack(side=tk.LEFT)
        ttk.Entry(frame_interval, textvariable=self.s_var, width=5).pack(side=tk.LEFT, padx=2)
        ttk.Label(frame_interval, text="secs").pack(side=tk.LEFT)
        ttk.Entry(frame_interval, textvariable=self.ms_var, width=5).pack(side=tk.LEFT, padx=2)
        ttk.Label(frame_interval, text="ms").pack(side=tk.LEFT)

        # 2. Random Offset
        self.offset_enabled = tk.BooleanVar(value=False)
        self.offset_var = tk.StringVar(value="50")
        frame_offset = ttk.Frame(frame_interval)
        frame_offset.pack(side=tk.RIGHT, padx=10)
        ttk.Checkbutton(frame_offset, text="Random offset", variable=self.offset_enabled).pack(side=tk.LEFT)
        ttk.Entry(frame_offset, textvariable=self.offset_var, width=5).pack(side=tk.LEFT, padx=2)
        ttk.Label(frame_offset, text="ms").pack(side=tk.LEFT)

        # 3. Options & 4. Repeat
        middle_frame = ttk.Frame(main_frame)
        middle_frame.pack(fill=tk.X, pady=5)
        
        frame_opts = ttk.LabelFrame(middle_frame, text="Click Options", padding=5)
        frame_opts.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 5))
        self.btn_var = tk.StringVar(value="Left")
        ttk.Combobox(frame_opts, textvariable=self.btn_var, values=["Left", "Right", "Middle"], state="readonly", width=10).pack(pady=2)
        self.type_var = tk.StringVar(value="Single")
        ttk.Combobox(frame_opts, textvariable=self.type_var, values=["Single", "Double"], state="readonly", width=10).pack(pady=2)
        
        frame_rep = ttk.LabelFrame(middle_frame, text="Click Repeat", padding=5)
        frame_rep.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True)
        self.rep_mode = tk.StringVar(value="Repeat until stopped")
        ttk.Radiobutton(frame_rep, text="Repeat until stopped", variable=self.rep_mode, value="Repeat until stopped").pack(anchor=tk.W)
        rep_n_frame = ttk.Frame(frame_rep)
        rep_n_frame.pack(anchor=tk.W)
        ttk.Radiobutton(rep_n_frame, text="Repeat", variable=self.rep_mode, value="Repeat N times").pack(side=tk.LEFT)
        self.rep_count = tk.StringVar(value="10")
        ttk.Entry(rep_n_frame, textvariable=self.rep_count, width=6).pack(side=tk.LEFT, padx=2)
        ttk.Label(rep_n_frame, text="times").pack(side=tk.LEFT)

        # 5. Position
        frame_pos = ttk.LabelFrame(main_frame, text="Cursor Position", padding=5)
        frame_pos.pack(fill=tk.X, pady=5)
        self.pos_mode = tk.StringVar(value="Current location")
        ttk.Radiobutton(frame_pos, text="Current location", variable=self.pos_mode, value="Current location").pack(side=tk.LEFT, padx=5)
        ttk.Radiobutton(frame_pos, text="Pick location", variable=self.pos_mode, value="Pick location").pack(side=tk.LEFT, padx=5)
        self.x_var, self.y_var = tk.StringVar(value="0"), tk.StringVar(value="0")
        ttk.Label(frame_pos, text="X:").pack(side=tk.LEFT)
        ttk.Entry(frame_pos, textvariable=self.x_var, width=5).pack(side=tk.LEFT, padx=2)
        ttk.Label(frame_pos, text="Y:").pack(side=tk.LEFT)
        ttk.Entry(frame_pos, textvariable=self.y_var, width=5).pack(side=tk.LEFT, padx=2)
        ttk.Button(frame_pos, text="Capture", command=self.capture_pos).pack(side=tk.LEFT, padx=5)

        # 6, 7. Controls & Hotkey
        frame_ctrl = ttk.Frame(main_frame)
        frame_ctrl.pack(fill=tk.X, pady=10)
        self.btn_start = ttk.Button(frame_ctrl, text="Start", command=self.start_clicker)
        self.btn_start.pack(side=tk.LEFT, expand=True, fill=tk.X, padx=2)
        self.btn_stop = ttk.Button(frame_ctrl, text="Stop", command=self.stop_clicker, state=tk.DISABLED)
        self.btn_stop.pack(side=tk.LEFT, expand=True, fill=tk.X, padx=2)
        
        self.hk_var = tk.StringVar(value="f6")
        ttk.Label(frame_ctrl, text="Hotkey:").pack(side=tk.LEFT, padx=2)
        hk_entry = ttk.Entry(frame_ctrl, textvariable=self.hk_var, width=5)
        hk_entry.pack(side=tk.LEFT, padx=2)
        hk_entry.bind("<FocusOut>", lambda e: self.engine.update_hotkey(self.hk_var.get()))

        # 8. Record/Playback
        frame_rec = ttk.LabelFrame(main_frame, text="Macro Record & Playback", padding=5)
        frame_rec.pack(fill=tk.X, pady=5)
        self.btn_rec = ttk.Button(frame_rec, text="Record Macro", command=self.toggle_record)
        self.btn_rec.pack(side=tk.LEFT, padx=2)
        self.btn_play = ttk.Button(frame_rec, text="Play Macro", command=self.play_macro)
        self.btn_play.pack(side=tk.LEFT, padx=2)
        self.loop_macro = tk.BooleanVar(value=False)
        ttk.Checkbutton(frame_rec, text="Loop", variable=self.loop_macro).pack(side=tk.LEFT, padx=5)

        # 9. Status
        self.status_var = tk.StringVar(value="Stopped | Clicks: 0")
        ttk.Label(main_frame, textvariable=self.status_var, relief=tk.SUNKEN, anchor=tk.W).pack(fill=tk.X, pady=(5,0))

    def _get_int(self, val: str, name: str, allow_zero: bool = True) -> int:
        try:
            v = int(val)
            if v < 0 or (v == 0 and not allow_zero):
                raise ValueError
            return v
        except ValueError:
            raise ValueError(f"Invalid {name}. Must be a positive integer.")

    def start_clicker(self):
        try:
            h = self._get_int(self.h_var.get(), "hours")
            m = self._get_int(self.m_var.get(), "minutes")
            s = self._get_int(self.s_var.get(), "seconds")
            ms = self._get_int(self.ms_var.get(), "milliseconds")
            total_ms = (h * 3600000) + (m * 60000) + (s * 1000) + ms
            
            if total_ms <= 0:
                raise ValueError("Total click interval must be greater than 0.")
                
            offset = self._get_int(self.offset_var.get(), "offset") if self.offset_enabled.get() else 0
            count = self._get_int(self.rep_count.get(), "repeat count") if self.rep_mode.get() == "Repeat N times" else 0
            
            x = self._get_int(self.x_var.get(), "X position")
            y = self._get_int(self.y_var.get(), "Y position")
            use_loc = self.pos_mode.get() == "Pick location"

            self.btn_start.config(state=tk.DISABLED)
            self.btn_stop.config(state=tk.NORMAL)
            
            self.engine.start_clicking(total_ms / 1000.0, offset, self.btn_var.get(), self.type_var.get(),
                                       self.rep_mode.get(), count, use_loc, x, y)
        except ValueError as e:
            messagebox.showerror("Input Error", str(e))

    def stop_clicker(self):
        self.engine.stop_clicking()
        self.btn_start.config(state=tk.NORMAL)
        self.btn_stop.config(state=tk.DISABLED)

    def toggle_clicking(self):
        if self.engine.running:
            self.root.after(0, self.stop_clicker)
        else:
            self.root.after(0, self.start_clicker)

    def capture_pos(self):
        messagebox.showinfo("Capture", "Move mouse to target and press Enter in 3 seconds.")
        time.sleep(3)
        x, y = self.engine.mouse.position
        self.x_var.set(str(int(x)))
        self.y_var.set(str(int(y)))

    def toggle_record(self):
        is_rec = self.engine.toggle_recording()
        self.btn_rec.config(text="Stop Recording" if is_rec else "Record Macro")
        if is_rec:
            self.btn_start.config(state=tk.DISABLED)
            self.btn_play.config(state=tk.DISABLED)
        else:
            self.btn_start.config(state=tk.NORMAL)
            self.btn_play.config(state=tk.NORMAL)

    def play_macro(self):
        if not self.engine.recorded_events:
            messagebox.showinfo("No Macro", "Record a macro first.")
            return
        self.btn_start.config(state=tk.DISABLED)
        self.btn_stop.config(state=tk.NORMAL)
        self.engine.play_recording(self.loop_macro.get())

    def update_status_bar(self, state: str, count: int):
        self.root.after(0, lambda: self.status_var.set(f"{state} | Clicks/Events: {count}"))
        if state == "Stopped":
            self.root.after(0, lambda: [self.btn_start.config(state=tk.NORMAL), self.btn_stop.config(state=tk.DISABLED)])

    def on_close(self):
        self.engine.stop_clicking()
        self.engine.stop_keyboard_listener()
        if self.engine.mouse_listener:
            self.engine.mouse_listener.stop()
        self.root.destroy()

if __name__ == "__main__":
    app_root = tk.Tk()
    app = AutoClickerGUI(app_root)
    app_root.mainloop()
