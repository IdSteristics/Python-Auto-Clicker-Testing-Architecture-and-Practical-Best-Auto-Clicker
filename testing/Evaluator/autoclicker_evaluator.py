"""Auto Clicker Evaluator - run with F5 in IDLE, pick the AI-written script, answer the dialogs.
Writes a report and updates leaderboard.txt in the same folder as this file."""

import ast
import io
import os
import re
import subprocess
import sys
import tempfile
import time
import tokenize
import tkinter as tk
from collections import Counter
from datetime import datetime
from tkinter import filedialog, messagebox, simpledialog

try:
    import psutil
except ImportError:
    psutil = None

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LEADERBOARD_FILE = os.path.join(BASE_DIR, "leaderboard.txt")
WEIGHTS = {
    "Functionality": 30,
    "Architecture": 25,
    "Efficiency & Density": 20,
    "Runtime Performance": 25,
}
LAUNCH_SECONDS = 4
CPU_SAMPLE_SECONDS = 3
TARGET_INTERVAL_MS = 100
EXPECTED_CPS = 1000 / TARGET_INTERVAL_MS
MODEL_COL_WIDTH = 28

PIP_NAMES = {
    "pyautogui": "pyautogui", "pynput": "pynput", "keyboard": "keyboard", "mouse": "mouse",
    "win32api": "pywin32", "win32con": "pywin32", "win32gui": "pywin32", "pydirectinput": "pydirectinput",
    "customtkinter": "customtkinter", "ttkbootstrap": "ttkbootstrap", "ttkthemes": "ttkthemes",
    "PIL": "pillow", "psutil": "psutil", "numpy": "numpy", "cv2": "opencv-python",
    "PyQt5": "PyQt5", "PyQt6": "PyQt6", "PySide6": "PySide6", "pyperclip": "pyperclip",
}
GUI_MODULES = {"tkinter", "customtkinter", "ttkbootstrap", "PyQt5", "PyQt6", "PySide2", "PySide6", "wx", "kivy", "pygame"}
CLICK_BACKENDS = {"pyautogui", "pynput", "win32api", "pydirectinput", "mouse", "ctypes", "Xlib", "pymouse"}
GUI_NAMES = {
    "tk", "ttk", "ctk", "Tk", "Frame", "Label", "Entry", "Button", "Spinbox", "Combobox", "Checkbutton",
    "Radiobutton", "LabelFrame", "Toplevel", "StringVar", "IntVar", "BooleanVar", "DoubleVar", "messagebox",
    "Canvas", "Scale", "OptionMenu", "CTk", "CTkButton", "CTkEntry", "CTkLabel", "QWidget", "QApplication",
    "QPushButton", "QMainWindow", "ttkbootstrap",
}
FALLBACK_STDLIB = {
    "os", "sys", "time", "threading", "tkinter", "random", "ctypes", "json", "re", "datetime", "math",
    "queue", "typing", "dataclasses", "enum", "collections", "functools", "itertools", "logging",
    "platform", "subprocess", "atexit", "signal", "struct", "string", "copy", "pathlib", "abc",
    "contextlib", "__future__", "statistics", "traceback", "tempfile", "io", "operator", "weakref",
}
STDLIB = set(getattr(sys, "stdlib_module_names", FALLBACK_STDLIB))
NEST_NODES = (ast.If, ast.For, ast.While, ast.Try, ast.With, ast.AsyncFor, ast.AsyncWith)
WAIT_CALLS = {"sleep", "wait", "after", "get", "join", "acquire", "recv", "accept", "select", "wait_for"}


class Scorecard:
    def __init__(self):
        self.items = []

    def add(self, category, name, earned, maximum, detail="", measured=True):
        self.items.append({"category": category, "name": name, "earned": earned if measured else 0.0,
                           "max": maximum, "detail": detail, "measured": measured})

    def rows(self, category):
        return [i for i in self.items if i["category"] == category]

    def category_scores(self):
        scores = {}
        for category, weight in WEIGHTS.items():
            rows = [r for r in self.rows(category) if r["measured"]]
            available = sum(r["max"] for r in rows)
            earned = sum(r["earned"] for r in rows)
            scores[category] = weight * earned / available if available else 0.0
        return scores

    def total(self):
        return sum(self.category_scores().values())

    def coverage(self):
        measured = sum(i["max"] for i in self.items if i["measured"])
        overall = sum(i["max"] for i in self.items)
        return 100 * measured / overall if overall else 0.0


def tier(value, limits, points):
    for limit, pts in zip(limits, points):
        if value <= limit:
            return pts
    return 0


def grade(score):
    for limit, letter in ((90, "S"), (80, "A"), (70, "B"), (60, "C"), (50, "D")):
        if score >= limit:
            return letter
    return "F"


def line_metrics(source, tree):
    doc_lines = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            first = node.body[0] if node.body else None
            if (isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant)
                    and isinstance(first.value.value, str)):
                doc_lines.update(range(first.lineno, first.end_lineno + 1))
    skip = {tokenize.COMMENT, tokenize.NL, tokenize.NEWLINE, tokenize.INDENT, tokenize.DEDENT,
            tokenize.ENDMARKER, tokenize.ENCODING}
    code, comments = set(), set()
    for tok in tokenize.generate_tokens(io.StringIO(source).readline):
        if tok.type == tokenize.COMMENT:
            comments.add(tok.start[0])
        elif tok.type not in skip:
            code.update(range(tok.start[0], tok.end[0] + 1))
    code -= doc_lines
    return len(code), len(comments - code - doc_lines), len(doc_lines), len(source.splitlines())


def complexity(node):
    score = 1
    for n in ast.walk(node):
        if isinstance(n, (ast.If, ast.For, ast.AsyncFor, ast.While, ast.ExceptHandler, ast.IfExp, ast.Assert)):
            score += 1
        elif isinstance(n, ast.BoolOp):
            score += len(n.values) - 1
        elif isinstance(n, ast.comprehension):
            score += 1 + len(n.ifs)
    return score


def max_depth(node, depth=0):
    best = depth
    for child in ast.iter_child_nodes(node):
        best = max(best, max_depth(child, depth + 1 if isinstance(child, NEST_NODES) else depth))
    return best


def magic_numbers(node):
    count = 0
    for child in ast.iter_child_nodes(node):
        if isinstance(child, ast.keyword):
            continue
        if (isinstance(child, ast.Constant) and isinstance(child.value, (int, float))
                and not isinstance(child.value, bool) and child.value not in (0, 1, 2, -1)):
            count += 1
        count += magic_numbers(child)
    return count


def is_constant_assign(node):
    if isinstance(node, ast.Assign):
        targets = node.targets
    elif isinstance(node, ast.AnnAssign):
        targets = [node.target]
    else:
        return False
    return all(isinstance(t, ast.Name) and t.id.isupper() for t in targets)


def collect_imports(tree):
    modules, bound, star = set(), {}, False
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            for a in n.names:
                modules.add(a.name.split(".")[0])
                bound[(a.asname or a.name).split(".")[0]] = a.name.split(".")[0]
        elif isinstance(n, ast.ImportFrom) and not n.level and n.module:
            top = n.module.split(".")[0]
            modules.add(top)
            for a in n.names:
                if a.name == "*":
                    star = True
                else:
                    bound[a.asname or a.name] = top
    return modules, bound, star


def class_split(tree):
    gui, logic = [], []
    for c in (n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)):
        refs = {n.id for n in ast.walk(c) if isinstance(n, ast.Name)}
        refs |= {n.attr for n in ast.walk(c) if isinstance(n, ast.Attribute)}
        bases = " ".join(ast.unparse(b) for b in c.bases)
        methods = [m for m in c.body if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef))]
        if refs & GUI_NAMES or re.search(r"Tk|Frame|QWidget|QMainWindow|CTk", bases):
            gui.append(c.name)
        elif len(methods) >= 2:
            logic.append(c.name)
    return gui, logic


def static_analysis(source, card):
    try:
        t0 = time.perf_counter()
        tree = ast.parse(source)
        compile(tree, "<benchmark>", "exec")
        parse_ms = (time.perf_counter() - t0) * 1000
    except SyntaxError as exc:
        for category, weight in WEIGHTS.items():
            card.add(category, "Script compiles", 0, weight, f"line {exc.lineno}: {exc.msg}")
        return None

    low = source.lower()
    sloc, comment_lines, doc_lines, total_lines = line_metrics(source, tree)
    funcs = [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    classes = [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]
    lengths = [f.end_lineno - f.lineno + 1 for f in funcs]
    complexities = [complexity(f) for f in funcs]
    avg_cplx = sum(complexities) / len(complexities) if complexities else complexity(tree)
    max_len = max(lengths, default=sloc)
    depth = max_depth(tree)
    modules, bound, star = collect_imports(tree)
    third_party = sorted(m for m in modules if m not in STDLIB)
    gui_cls, logic_cls = class_split(tree)

    def has(*patterns):
        return all(re.search(p, low) for p in patterns)

    cat = "Functionality"
    units = sum(bool(re.search(p, low)) for p in (r"hour", r"\bmin|minute", r"sec", r"milli|\bms\b"))
    card.add(cat, "Interval fields (h/m/s/ms)", 5 * units / 4, 5, f"{units}/4 units found")
    random_pts = 3 if has(r"random", r"offset|jitter|variance|variation") else 1 if has(r"random") else 0
    card.add(cat, "Random offset", random_pts, 3)
    buttons = sum(bool(re.search(p, low)) for p in (r"left", r"right", r"middle"))
    card.add(cat, "Mouse button choice", buttons, 3, f"{buttons}/3 buttons")
    ctypes_pts = 3 if has(r"double", r"single") else 1 if has(r"double") else 0
    card.add(cat, "Click type single/double", ctypes_pts, 3)
    repeat_pts = (2 if has(r"repeat|times|count") else 0) + (2 if has(r"until stop|infinite|forever|unlimited|repeat until") else 0)
    card.add(cat, "Repeat count / until stopped", repeat_pts, 4)
    cursor_pts = (2 if has(r"current|cursor|get_?pos") else 0) + (2 if has(r"pick|capture|coordinate|set.?location|\by\b.*\bx\b|\bx\b.*\by\b") else 0)
    card.add(cat, "Cursor position (current/pick XY)", cursor_pts, 4)
    card.add(cat, "Start / Stop controls", 2 if has(r"start", r"stop") else 0, 2)
    hotkey = has(r"hotkey|globalhotkeys|add_hotkey|<f\d+>|\bf(?:[6-9]|1[0-2])\b|on_press")
    card.add(cat, "Hotkey support", 3 if hotkey else 0, 3)
    rec_pts = 2 if has(r"record", r"playback|replay|play") else 1 if has(r"record") else 0
    card.add(cat, "Record & playback", rec_pts, 2)
    gui_ok = bool(modules & GUI_MODULES)
    backend_ok = bool(modules & CLICK_BACKENDS)
    card.add(cat, "GUI toolkit + click backend", 0.5 * gui_ok + 0.5 * backend_ok, 1,
             f"gui={'yes' if gui_ok else 'no'} backend={'yes' if backend_ok else 'no'}")
    func_points = sum(i["earned"] for i in card.rows(cat))

    cat = "Architecture"
    card.add(cat, "Class-based design", 0 if not classes else 2 if len(classes) == 1 else 4, 4, f"{len(classes)} classes")
    plain_funcs = [f for f in tree.body if isinstance(f, ast.FunctionDef)
                   and not ({n.id for n in ast.walk(f) if isinstance(n, ast.Name)} & GUI_NAMES)]
    if gui_cls and logic_cls:
        sep = 5
    elif len(plain_funcs) >= 2:
        sep = 2
    else:
        sep = 0
    card.add(cat, "GUI / logic separation", sep, 5, f"gui={gui_cls or '-'} logic={logic_cls or '-'}")
    uses_thread = has(r"threading\.thread|\bthread\(")
    uses_after = has(r"\.after\(")
    card.add(cat, "Non-blocking GUI", 4 if uses_thread else 3 if uses_after else 0, 4,
             "threading" if uses_thread else "after()" if uses_after else "none")
    sync = has(r"event\(|lock\(|rlock\(|queue\(|condition\(|semaphore\(")
    card.add(cat, "Thread / event-loop safety", 2 if sync or (uses_after and not uses_thread) else 0, 2)
    guard = any(isinstance(n, ast.If) and "__name__" in ast.unparse(n.test) for n in tree.body)
    card.add(cat, "Main guard", 1 if guard else 0, 1)
    consts = sum(is_constant_assign(n) for n in tree.body)
    card.add(cat, "Module-level constants", 2 if consts >= 3 else 1 if consts else 0, 2, f"{consts} constants")
    hinted = sum(f.returns is not None or any(a.annotation for a in f.args.args) for f in funcs)
    ratio = hinted / len(funcs) if funcs else 0
    card.add(cat, "Type hints", 2 if ratio >= 0.5 else 1 if ratio > 0 else 0, 2, f"{ratio:.0%} of functions")
    tries = [n for n in ast.walk(tree) if isinstance(n, ast.Try)]
    bare = any(h.type is None for t in tries for h in t.handlers)
    card.add(cat, "Error handling", 0 if not tries else 1 if bare else 2, 2,
             f"{len(tries)} try blocks" + (", bare except" if bare else ""))
    card.add(cat, "Clean shutdown", 2 if has(r"wm_delete_window|closeevent|atexit|abouttoquit") else 0, 2)
    card.add(cat, "No global statements", 0 if any(isinstance(n, ast.Global) for n in ast.walk(tree)) else 1, 1)

    cat = "Efficiency & Density"
    density = func_points / sloc * 100 if sloc else 0
    card.add(cat, "Feature density", 5 * min(1, density / 12), 5, f"{density:.1f} feature pts per 100 SLOC (target 12)")
    card.add(cat, "Average cyclomatic complexity", tier(avg_cplx, (3, 5, 8, 12), (4, 3, 2, 1)), 4, f"{avg_cplx:.2f}")
    card.add(cat, "Longest function", tier(max_len, (40, 60, 90), (3, 2, 1)), 3, f"{max_len} lines")
    card.add(cat, "Nesting depth", tier(depth, (4, 6), (2, 1)), 2, f"{depth}")
    norm = [ln.strip() for ln in source.splitlines() if len(ln.strip()) > 20 and not ln.strip().startswith("#")]
    dup_ratio = sum(c - 1 for c in Counter(norm).values() if c > 1) / len(norm) if norm else 0
    card.add(cat, "Duplicate code", tier(dup_ratio, (0.03, 0.08, 0.15), (3, 2, 1)), 3, f"{dup_ratio:.1%} duplicate lines")
    magic = sum(magic_numbers(n) for n in tree.body if not is_constant_assign(n))
    magic_rate = magic / sloc * 100 if sloc else 0
    card.add(cat, "Magic numbers", tier(magic_rate, (3, 8), (2, 1)), 2, f"{magic} ({magic_rate:.1f} per 100 SLOC)")
    used = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    unused = sorted(b for b, m in bound.items() if b not in used and m != "__future__")
    card.add(cat, "Import hygiene", 0 if star or unused else 1, 1,
             "star import" if star else f"unused: {', '.join(unused)}" if unused else "clean")

    cat = "Runtime Performance"
    clock = 3 if has(r"perf_counter|monotonic") else 1 if has(r"time\.time\(") else 0
    card.add(cat, "Precise timing clock", clock, 3, "perf_counter/monotonic" if clock == 3 else "time.time" if clock else "none")
    busy = 0
    for loop in (n for n in ast.walk(tree) if isinstance(n, ast.While)):
        names = {c.func.attr if isinstance(c.func, ast.Attribute) else getattr(c.func, "id", "")
                 for c in ast.walk(loop) if isinstance(c, ast.Call)}
        busy += not (names & WAIT_CALLS)
    card.add(cat, "No busy-wait loops", 3 if busy == 0 else 0, 3, f"{busy} suspicious loops")

    return {
        "sloc": sloc, "comment_lines": comment_lines, "doc_lines": doc_lines, "total_lines": total_lines,
        "functions": len(funcs), "classes": len(classes), "avg_complexity": avg_cplx,
        "max_complexity": max(complexities, default=0), "longest_function": max_len, "nesting": depth,
        "duplicate_ratio": dup_ratio, "magic_numbers": magic, "parse_ms": parse_ms,
        "imports": sorted(modules), "third_party": third_party,
        "avg_line_len": sum(len(l) for l in source.splitlines() if l.strip()) / max(1, sum(1 for l in source.splitlines() if l.strip())),
    }


def python_exe():
    exe = sys.executable
    if os.path.basename(exe).lower() == "pythonw.exe":
        candidate = os.path.join(os.path.dirname(exe), "python.exe")
        if os.path.exists(candidate):
            return candidate
    return exe


def runtime_probe(path):
    probe = {"alive": False, "returncode": None, "stderr": "", "cpu": None, "rss": None, "threads": None}
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    with tempfile.TemporaryFile() as err:
        proc = subprocess.Popen([python_exe(), path], cwd=os.path.dirname(path),
                                stdout=subprocess.DEVNULL, stderr=err, creationflags=flags)
        deadline = time.perf_counter() + LAUNCH_SECONDS
        while time.perf_counter() < deadline and proc.poll() is None:
            time.sleep(0.1)
        probe["alive"] = proc.poll() is None
        probe["returncode"] = proc.poll()
        if probe["alive"] and psutil:
            try:
                p = psutil.Process(proc.pid)
                p.cpu_percent(None)
                time.sleep(CPU_SAMPLE_SECONDS)
                probe["cpu"] = p.cpu_percent(None)
                probe["rss"] = p.memory_info().rss / 1048576
                probe["threads"] = p.num_threads()
            except psutil.Error:
                pass
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(3)
            except subprocess.TimeoutExpired:
                proc.kill()
        err.seek(0)
        probe["stderr"] = err.read().decode("utf-8", "replace").strip()[-800:]
    return probe


def runtime_scoring(card, probe, cps):
    cat = "Runtime Performance"
    if probe is None:
        for name, mx in (("Launch stability", 5), ("Idle CPU", 5), ("Memory footprint", 3)):
            card.add(cat, name, 0, mx, "skipped", measured=False)
    else:
        if probe["alive"]:
            card.add(cat, "Launch stability", 5 if not probe["stderr"] else 2, 5,
                     f"alive after {LAUNCH_SECONDS}s" + (", stderr output" if probe["stderr"] else ""))
        else:
            card.add(cat, "Launch stability", 0, 5, f"exited early (code {probe['returncode']})")
        if not probe["alive"]:
            card.add(cat, "Idle CPU", 0, 5, "not running")
            card.add(cat, "Memory footprint", 0, 3, "not running")
        elif probe["cpu"] is None:
            card.add(cat, "Idle CPU", 0, 5, "needs psutil (pip install psutil)", measured=False)
            card.add(cat, "Memory footprint", 0, 3, "needs psutil (pip install psutil)", measured=False)
        else:
            card.add(cat, "Idle CPU", tier(probe["cpu"], (1, 3, 8), (5, 4, 2)), 5, f"{probe['cpu']:.1f}%")
            card.add(cat, "Memory footprint", tier(probe["rss"], (60, 100, 200), (3, 2, 1)), 3, f"{probe['rss']:.1f} MB")
    if cps is None:
        card.add(cat, f"CPS accuracy @ {TARGET_INTERVAL_MS} ms", 0, 6, "skipped", measured=False)
    else:
        err = abs(cps - EXPECTED_CPS) / EXPECTED_CPS
        card.add(cat, f"CPS accuracy @ {TARGET_INTERVAL_MS} ms", tier(err, (0.02, 0.05, 0.10, 0.20, 0.30), (6, 5, 4, 2, 1)), 6,
                 f"measured {cps:.2f} vs expected {EXPECTED_CPS:.2f} ({err:.1%} error)")


def build_report(model, path, card, metrics, probe, cps, stamp):
    scores = card.category_scores()
    total = card.total()
    bar = "=" * 78
    out = [bar, "AUTO CLICKER BENCHMARK REPORT", bar,
           f"Model       : {model}",
           f"File        : {os.path.basename(path)}",
           f"Date        : {stamp}",
           f"Python      : {sys.version.split()[0]} on {sys.platform}",
           f"Total score : {total:.2f} / 100   Grade {grade(total)}",
           f"Coverage    : {card.coverage():.0f}% of rubric measured", ""]
    for category, weight in WEIGHTS.items():
        out.append(f"{category.upper()}  {scores[category]:.2f} / {weight}")
        out.append("-" * 78)
        for r in card.rows(category):
            pts = f"{r['earned']:.1f}/{r['max']:.1f}" if r["measured"] else "skipped"
            out.append(f"  {r['name']:<36}{pts:>10}   {r['detail']}")
        out.append("")
    if metrics:
        out += ["CODE METRICS", "-" * 78,
                f"  Source lines of code     : {metrics['sloc']}",
                f"  Comment lines            : {metrics['comment_lines']}",
                f"  Docstring lines          : {metrics['doc_lines']}",
                f"  Total lines              : {metrics['total_lines']}",
                f"  Functions / classes      : {metrics['functions']} / {metrics['classes']}",
                f"  Avg / max complexity     : {metrics['avg_complexity']:.2f} / {metrics['max_complexity']}",
                f"  Longest function (lines) : {metrics['longest_function']}",
                f"  Max nesting depth        : {metrics['nesting']}",
                f"  Duplicate line ratio     : {metrics['duplicate_ratio']:.1%}",
                f"  Magic numbers            : {metrics['magic_numbers']}",
                f"  Avg line length          : {metrics['avg_line_len']:.1f}",
                f"  Parse + compile time     : {metrics['parse_ms']:.2f} ms", ""]
    if probe:
        out += ["RUNTIME PROBE", "-" * 78,
                f"  Alive after {LAUNCH_SECONDS}s          : {probe['alive']}",
                f"  Idle CPU                 : {'n/a' if probe['cpu'] is None else format(probe['cpu'], '.1f') + '%'}",
                f"  Memory (RSS)             : {'n/a' if probe['rss'] is None else format(probe['rss'], '.1f') + ' MB'}",
                f"  Threads                  : {probe['threads'] if probe['threads'] is not None else 'n/a'}"]
        if probe["stderr"]:
            out += ["  stderr tail:"] + [f"    {ln}" for ln in probe["stderr"].splitlines()[-8:]]
        out.append("")
    if cps is not None:
        out += [f"  Manual CPS test          : {cps:.2f} clicks/s (expected {EXPECTED_CPS:.2f})", ""]
    losses = sorted((r for r in card.items if r["measured"] and r["max"] - r["earned"] > 0),
                    key=lambda r: r["earned"] - r["max"])[:5]
    if losses:
        out += ["BIGGEST POINT LOSSES", "-" * 78]
        out += [f"  -{r['max'] - r['earned']:.1f}  {r['category']}: {r['name']} ({r['detail']})" for r in losses]
        out.append("")
    if metrics:
        out += ["INSTALL REQUIREMENTS DETECTED", "-" * 78]
        if metrics["third_party"]:
            pip = sorted({PIP_NAMES.get(m, m) for m in metrics["third_party"]})
            out.append("  pip install " + " ".join(pip))
        else:
            out.append("  No third-party modules detected (standard library only).")
        out.append("")
    out.append(bar)
    return "\n".join(out), total, scores


def read_leaderboard():
    rows = []
    if os.path.exists(LEADERBOARD_FILE):
        with open(LEADERBOARD_FILE, "r", encoding="utf-8") as f:
            for line in f:
                if line.startswith("|"):
                    cols = [c.strip() for c in line.strip().strip("|").split("|")]
                    if len(cols) == 11 and cols[0].isdigit():
                        rows.append(cols[1:])
    return rows


def write_leaderboard(rows):
    rows.sort(key=lambda r: float(r[1]), reverse=True)
    fmt = "| {:>4} | {:<%d} | {:>6} | {:^5} | {:>5} | {:>5} | {:>5} | {:>5} | {:>5} | {:>4} | {:<16} |" % MODEL_COL_WIDTH
    header = fmt.format("Rank", "Model", "Total", "Grade", "Func", "Arch", "Eff", "Perf", "SLOC", "Cov%", "Date")
    rule = "+" + "-" * (len(header) - 2) + "+"
    lines = ["AUTO CLICKER AI LEADERBOARD", rule, header, rule]
    lines += [fmt.format(i, *r) for i, r in enumerate(rows, 1)]
    lines += [rule, "Func=30  Arch=25  Eff=20  Perf=25  (Total=100). Cov% = share of the rubric that was measured."]
    with open(LEADERBOARD_FILE, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def main():
    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    path = filedialog.askopenfilename(parent=root, title="Select the AI-generated auto clicker (.py)",
                                      filetypes=[("Python files", "*.py")])
    if not path or os.path.abspath(path) == os.path.abspath(__file__):
        root.destroy()
        print("No valid file selected.")
        return
    model = (simpledialog.askstring("Model", "Which AI model/chatbot wrote this script?", parent=root) or "Unknown").strip()
    model = model.replace("|", "/")[:MODEL_COL_WIDTH] or "Unknown"
    cps = simpledialog.askfloat(
        "CPS accuracy (optional)",
        f"Set the clicker to {TARGET_INTERVAL_MS} ms, Left, Single, Repeat until stopped, then run the\n"
        f"5 SECOND TEST on the CPS test site and enter the Click/s it showed.\n"
        f"Cancel to skip.", parent=root, minvalue=0.0)
    do_probe = messagebox.askyesno(
        "Runtime probe",
        f"Launch the script for about {LAUNCH_SECONDS + CPU_SAMPLE_SECONDS} seconds to measure stability, idle CPU and memory?\n\n"
        "Its window will open and close automatically. The evaluator never starts clicking.", parent=root)

    with open(path, "r", encoding="utf-8", errors="replace") as f:
        source = f.read()
    card = Scorecard()
    metrics = static_analysis(source, card)
    probe = runtime_probe(path) if (metrics and do_probe) else None
    if metrics:
        runtime_scoring(card, probe, cps)

    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    report, total, scores = build_report(model, path, card, metrics, probe, cps, stamp)
    safe = re.sub(r"[^A-Za-z0-9_-]+", "_", model)
    report_path = os.path.join(BASE_DIR, f"report_{safe}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report + "\n")

    rows = read_leaderboard()
    rows.append([model, f"{total:.2f}", grade(total), f"{scores['Functionality']:.1f}",
                 f"{scores['Architecture']:.1f}", f"{scores['Efficiency & Density']:.1f}",
                 f"{scores['Runtime Performance']:.1f}", str(metrics["sloc"] if metrics else 0),
                 f"{card.coverage():.0f}", stamp])
    write_leaderboard(rows)

    print(report)
    print(f"\nReport saved     : {report_path}")
    print(f"Leaderboard saved: {LEADERBOARD_FILE}")
    messagebox.showinfo("Benchmark complete", f"{model}: {total:.2f} / 100 (Grade {grade(total)})\n\n"
                        f"Saved in:\n{BASE_DIR}", parent=root)
    root.destroy()


if __name__ == "__main__":
    main()
