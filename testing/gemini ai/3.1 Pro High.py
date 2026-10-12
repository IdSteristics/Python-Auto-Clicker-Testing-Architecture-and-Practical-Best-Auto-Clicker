import threading
import time
import random
import tkinter as tk
from tkinter import ttk, messagebox
from typing import Callable, Optional, List, Tuple, Any
from pynput import mouse, keyboard

# --- MODULE-LEVEL CONSTANTS ---
DEFAULT_HOTKEY: keyboard.Key = keyboard.Key.f6
UI_PADDING: int = 5
BUTTON_MAP: dict = {"Left": mouse.Button.left, "Right": mouse.Button.right, "Middle": mouse.Button.middle}
STATE_IDLE: str = "Idle"
STATE_RUNNING: str = "Running"
STATE_RECORDING: str = "Recording"
STATE_PLAYING: str = "Playing"

class ClickEngine:
    """Handles the background auto-clicking loop with drift compensation."""
    def __init__(self, interval: float, random_ms: float, button: mouse.Button, double_click: bool,
                 max_clicks: int, pos: Optional[Tuple[int, int]], on_update: Callable[[int], None],
                 on_finish: Callable[[], None]):
        self.interval = interval
        self.random_ms = random_ms
        self.button = button
        self.click_count = 2 if double_click else 1
        self.max_clicks = max_clicks
        self.pos = pos
        self.on_update = on_update
        self.on_finish = on_finish
        
        self.mouse_ctrl = mouse.Controller()
        self.stopped = threading.Event()
        self.clicks = 0
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        self.stopped.clear()
        self.clicks = 0
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self.stopped.set()

    def _run(self) -> None:
        try:
            next_time = time.perf_counter()
            while not self.stopped.is_set():
                if self.max_clicks > 0 and self.clicks >= self.max_clicks:
                    break

                if self.pos is not None:
                    self.mouse_ctrl.position = self.pos

                self.mouse_ctrl.click(self.button, self.click_count)
                self.clicks += 1
                self.on_update(self.clicks)

                offset = random.uniform(0, self.random_ms / 1000.0) if self.random_ms > 0 else 0.0
                actual_interval = self.interval + offset
                next_time += actual_interval
                sleep_time = next_time - time.perf_counter()

                if sleep_time > 0:
                    self.stopped.wait(sleep_time)
                else:
                    next_time = time.perf_counter() 
        except Exception as e:
            print(f"Engine Error: {e}")
        finally:
            self.on_finish()

class RecordEngine:
    """Handles recording and replaying mouse click sequences."""
    def __init__(self, on_finish: Callable[[], None]):
        self.sequence: List[Tuple[float, int, int, mouse.Button]] = []
        self.on_finish = on_finish
        self.stopped = threading.Event()
        self.mouse_ctrl = mouse.Controller()
        self.listener: Optional[mouse.Listener] = None
        self._last_time: float = 0.0
        self._thread: Optional[threading.Thread] = None
        self.looping = False

    def start_recording(self) -> None:
        self.sequence.clear()
        self.stopped.clear()
        self._last_time = time.perf_counter()
        self.listener = mouse.Listener(on_click=self._on_click)
        self.listener.start()

    def stop_recording(self) -> None:
        if self.listener is not None:
            self.listener.stop()
            self.listener = None
        self.on_finish()

    def _on_click(self, x: int, y: int, button: mouse.Button, pressed: bool) -> None:
        if pressed:
            now = time.perf_counter()
            delay = now - self._last_time
            self._last_time = now
            self.sequence.append((delay, int(x), int(y), button))

    def start_playback(self, loop: bool) -> None:
        if not self.sequence:
            self.on_finish()
            return
        self.stopped.clear()
        self.looping = loop
        self._thread = threading.Thread(target=self._play_run, daemon=True)
        self._thread.start()

    def stop_playback(self) -> None:
        self.stopped.set()

    def _play_run(self) -> None:
        try:
            while not self.stopped.is_set():
                for delay, x, y, button in self.sequence:
                    if self.stopped.is_set():
                        break
                    self.stopped.wait(delay)
                    if self.stopped.is_set():
                        break
                    self.mouse_ctrl.position = (x, y)
                    self.mouse_ctrl.click(button, 1)
                if not self.looping:
                    break
        finally:
            self.on_finish()

class AutoClickerGUI:
    """The main GUI application for the Auto Clicker."""
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Auto Clicker")
        self.root.resizable(False, False)
        
        self.engine: Optional[ClickEngine] = None
        self.rec_engine: RecordEngine = RecordEngine(self.on_playback_finish)
        
        self.hotkey: Any = DEFAULT_HOTKEY
        self.listen_for_hotkey = False
        self.state = STATE_IDLE
        
        self.kb_listener = keyboard.Listener(on_press=self.on_key_press)
        self.kb_listener.start()

        self._build_ui()
        self._update_status(STATE_IDLE)
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    def _build_ui(self) -> None:
        main_frame = ttk.Frame(self.root, padding=UI_PADDING)
        main_frame.grid(row=0, column=0, sticky="nsew")

        # 1. Interval Settings
        lf_int = ttk.LabelFrame(main_frame, text="Click Interval", padding=UI_PADDING)
        lf_int.grid(row=0, column=0, columnspan=2, sticky="ew", pady=2)
        
        self.h_var, self.m_var, self.s_var, self.ms_var = tk.StringVar(value="0"), tk.StringVar(value="0"), tk.StringVar(value="0"), tk.StringVar(value="100")
        for i, (lbl, var) in enumerate([("Hours:", self.h_var), ("Mins:", self.m_var), ("Secs:", self.s_var), ("MS:", self.ms_var)]):
            ttk.Label(lf_int, text=lbl).grid(row=0, column=i*2, sticky="e", padx=2)
            ttk.Entry(lf_int, textvariable=var, width=5).grid(row=0, column=i*2+1, sticky="w", padx=2)

        # 2. Click Options & Random Offset
        lf_opt = ttk.LabelFrame(main_frame, text="Click Options", padding=UI_PADDING)
        lf_opt.grid(row=1, column=0, sticky="nsew", pady=2, padx=(0, 2))
        
        ttk.Label(lf_opt, text="Button:").grid(row=0, column=0, sticky="w")
        self.btn_var = tk.StringVar(value="Left")
        ttk.Combobox(lf_opt, textvariable=self.btn_var, values=list(BUTTON_MAP.keys()), width=8, state="readonly").grid(row=0, column=1, sticky="e")
        
        ttk.Label(lf_opt, text="Type:").grid(row=1, column=0, sticky="w")
        self.type_var = tk.StringVar(value="Single")
        ttk.Combobox(lf_opt, textvariable=self.type_var, values=["Single", "Double"], width=8, state="readonly").grid(row=1, column=1, sticky="e")

        self.rand_var = tk.BooleanVar(value=False)
        self.rand_ms_var = tk.StringVar(value="50")
        ttk.Checkbutton(lf_opt, text="Random Offset (ms):", variable=self.rand_var).grid(row=2, column=0, sticky="w")
        ttk.Entry(lf_opt, textvariable=self.rand_ms_var, width=5).grid(row=2, column=1, sticky="e")

        # 3. Repeat Options
        lf_rep = ttk.LabelFrame(main_frame, text="Click Repeat", padding=UI_PADDING)
        lf_rep.grid(row=1, column=1, sticky="nsew", pady=2, padx=(2, 0))
        
        self.rep_var = tk.IntVar(value=0)
        self.rep_times_var = tk.StringVar(value="10")
        ttk.Radiobutton(lf_rep, text="Repeat until stopped", variable=self.rep_var, value=0).grid(row=0, column=0, columnspan=2, sticky="w")
        ttk.Radiobutton(lf_rep, text="Repeat N times:", variable=self.rep_var, value=1).grid(row=1, column=0, sticky="w")
        ttk.Entry(lf_rep, textvariable=self.rep_times_var, width=5).grid(row=1, column=1, sticky="w")

        # 4. Cursor Position
        lf_pos = ttk.LabelFrame(main_frame, text="Cursor Position", padding=UI_PADDING)
        lf_pos.grid(row=2, column=0, columnspan=2, sticky="ew", pady=2)
        
        self.pos_var = tk.IntVar(value=0)
        ttk.Radiobutton(lf_pos, text="Current location", variable=self.pos_var, value=0).grid(row=0, column=0, sticky="w")
        ttk.Radiobutton(lf_pos, text="Pick location", variable=self.pos_var, value=1).grid(row=0, column=1, sticky="w")
        
        self.x_var, self.y_var = tk.StringVar(value="0"), tk.StringVar(value="0")
        ttk.Label(lf_pos, text="X:").grid(row=0, column=2, sticky="e", padx=2)
        ttk.Entry(lf_pos, textvariable=self.x_var, width=5).grid(row=0, column=3, sticky="w")
        ttk.Label(lf_pos, text="Y:").grid(row=0, column=4, sticky="e", padx=2)
        ttk.Entry(lf_pos, textvariable=self.y_var, width=5).grid(row=0, column=5, sticky="w")
        ttk.Button(lf_pos, text="Pick", width=5, command=self.pick_location).grid(row=0, column=6, padx=5)

        # 5. Hotkey & Main Controls
        lf_ctrl = ttk.Frame(main_frame, padding=UI_PADDING)
        lf_ctrl.grid(row=3, column=0, columnspan=2, sticky="ew", pady=2)
        
        self.lbl_hotkey = ttk.Label(lf_ctrl, text=f"Hotkey: {self._format_key(self.hotkey)}")
        self.lbl_hotkey.grid(row=0, column=0, sticky="w", padx=5)
        ttk.Button(lf_ctrl, text="Change Hotkey", command=self.wait_for_hotkey).grid(row=0, column=1, padx=5)
        
        self.btn_start = ttk.Button(lf_ctrl, text="Start", command=self.toggle_clicking)
        self.btn_start.grid(row=0, column=2, padx=5)
        self.btn_stop = ttk.Button(lf_ctrl, text="Stop", command=self.stop_all, state=tk.DISABLED)
        self.btn_stop.grid(row=0, column=3, padx=5)

        # 6. Record & Playback
        lf_rec = ttk.LabelFrame(main_frame, text="Record & Playback", padding=UI_PADDING)
        lf_rec.grid(row=4, column=0, columnspan=2, sticky="ew", pady=2)
        
        self.btn_rec = ttk.Button(lf_rec, text="Record", command=self.toggle_recording)
        self.btn_rec.grid(row=0, column=0, padx=5)
        self.btn_play = ttk.Button(lf_rec, text="Play", command=self.toggle_playback)
        self.btn_play.grid(row=0, column=1, padx=5)
        
        self.loop_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(lf_rec, text="Loop Playback", variable=self.loop_var).grid(row=0, column=2, padx=5)

        # 7. Status Line
        self.lbl_status = ttk.Label(main_frame, text="Ready.", anchor="w", foreground="blue")
        self.lbl_status.grid(row=5, column=0, columnspan=2, sticky="ew", pady=2)

    def _format_key(self, key: Any) -> str:
        if hasattr(key, 'name'): return key.name.upper()
        if hasattr(key, 'char') and key.char: return key.char.upper()
        return str(key).strip("'").upper()

    def _update_status(self, msg: str) -> None:
        self.lbl_status.config(text=f"Status: {self.state} | {msg}")

    def on_key_press(self, key: Any) -> None:
        if self.listen_for_hotkey:
            self.hotkey = key
            self.listen_for_hotkey = False
            self.root.after(0, lambda: self.lbl_hotkey.config(text=f"Hotkey: {self._format_key(self.hotkey)}"))
            self.root.after(0, lambda: self._update_status("Hotkey updated."))
            return
            
        if key == self.hotkey:
            if self.state == STATE_IDLE:
                self.root.after(0, self.toggle_clicking)
            elif self.state in (STATE_RUNNING, STATE_RECORDING, STATE_PLAYING):
                self.root.after(0, self.stop_all)

    def wait_for_hotkey(self) -> None:
        self.listen_for_hotkey = True
        self._update_status("Press any key to set hotkey...")

    def pick_location(self) -> None:
        self._update_status("Click anywhere to pick position...")
        def on_click(x, y, button, pressed):
            if pressed:
                self.root.after(0, lambda: self.x_var.set(str(int(x))))
                self.root.after(0, lambda: self.y_var.set(str(int(y))))
                self.root.after(0, lambda: self.pos_var.set(1))
                self.root.after(0, lambda: self._update_status("Position captured."))
                return False
        mouse.Listener(on_click=on_click).start()

    def get_parsed_inputs(self) -> tuple:
        try:
            h = abs(float(self.h_var.get() or 0))
            m = abs(float(self.m_var.get() or 0))
            s = abs(float(self.s_var.get() or 0))
            ms = abs(float(self.ms_var.get() or 0))
            interval = h * 3600 + m * 60 + s + ms / 1000.0
            
            rand_ms = abs(float(self.rand_ms_var.get() or 0)) if self.rand_var.get() else 0.0
            max_clicks = abs(int(self.rep_times_var.get() or 0)) if self.rep_var.get() == 1 else 0
            
            pos = None
            if self.pos_var.get() == 1:
                pos = (int(self.x_var.get() or 0), int(self.y_var.get() or 0))
                
            return interval, rand_ms, max_clicks, pos
        except ValueError:
            raise ValueError("Numeric fields contain invalid characters.")

    def toggle_clicking(self) -> None:
        if self.state != STATE_IDLE: return
        
        try:
            interval, rand_ms, max_clicks, pos = self.get_parsed_inputs()
            if interval <= 0:
                raise ValueError("Click interval must be greater than 0.")
        except ValueError as e:
            messagebox.showerror("Input Error", str(e))
            return

        self.state = STATE_RUNNING
        self._sync_ui_state()
        
        self.engine = ClickEngine(
            interval=interval,
            random_ms=rand_ms,
            button=BUTTON_MAP[self.btn_var.get()],
            double_click=(self.type_var.get() == "Double"),
            max_clicks=max_clicks,
            pos=pos,
            on_update=lambda c: self.root.after(0, lambda: self._update_status(f"Clicks: {c}")),
            on_finish=lambda: self.root.after(0, self.on_engine_finish)
        )
        self.engine.start()

    def toggle_recording(self) -> None:
        if self.state == STATE_IDLE:
            self.state = STATE_RECORDING
            self._sync_ui_state()
            self._update_status("Recording clicks... Press Hotkey to stop.")
            self.rec_engine.start_recording()
        
    def toggle_playback(self) -> None:
        if self.state == STATE_IDLE:
            if not self.rec_engine.sequence:
                messagebox.showinfo("Playback", "No sequence recorded.")
                return
            self.state = STATE_PLAYING
            self._sync_ui_state()
            self._update_status("Playing sequence... Press Hotkey to stop.")
            self.rec_engine.start_playback(self.loop_var.get())

    def stop_all(self) -> None:
        if self.state == STATE_RUNNING and self.engine:
            self.engine.stop()
        elif self.state == STATE_RECORDING:
            self.rec_engine.stop_recording()
        elif self.state == STATE_PLAYING:
            self.rec_engine.stop_playback()

    def on_engine_finish(self) -> None:
        self.state = STATE_IDLE
        self.engine = None
        self._sync_ui_state()
        self._update_status("Finished.")

    def on_playback_finish(self) -> None:
        if self.state in (STATE_RECORDING, STATE_PLAYING):
            self.state = STATE_IDLE
            self.root.after(0, self._sync_ui_state)
            msg = "Sequence recorded." if len(self.rec_engine.sequence) > 0 else "Ready."
            self.root.after(0, lambda: self._update_status(msg))

    def _sync_ui_state(self) -> None:
        is_idle = (self.state == STATE_IDLE)
        new_state = tk.NORMAL if is_idle else tk.DISABLED
        
        self.btn_start.config(state=new_state)
        self.btn_rec.config(state=new_state)
        self.btn_play.config(state=new_state)
        self.btn_stop.config(state=tk.NORMAL if not is_idle else tk.DISABLED)

    def on_close(self) -> None:
        self.stop_all()
        self.kb_listener.stop()
        self.root.destroy()

if __name__ == "__main__":
    root = tk.Tk()
    app = AutoClickerGUI(root)
    root.mainloop()
