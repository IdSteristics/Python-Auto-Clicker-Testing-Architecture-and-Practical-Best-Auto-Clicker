"""
Build script: turns the Auto Clicker .py file into a Windows .exe.

How to use (in IDLE):
  1. Put this file in the SAME folder as your auto clicker script.
  2. Open this file in IDLE and press F5 (Run > Run Module).
  3. Wait for "BUILD COMPLETE". Your app will be in the 'dist' folder
     and a copy is placed next to this script as AutoClicker.exe.

Note: PyInstaller builds for the OS it runs on, so run this on Windows
to get a Windows .exe.
"""

import shutil
import subprocess
import sys
from pathlib import Path

# ---- Settings you can change -------------------------------------------
SOURCE_NAME = "Sonnet 5.5 Medium.py"   # your auto clicker script's file name
APP_NAME = "AutoClicker"               # name of the finished .exe
ICON_FILE = None                       # e.g. "icon.ico" (optional), or None
# -------------------------------------------------------------------------

HERE = Path(__file__).resolve().parent
SOURCE = HERE / SOURCE_NAME
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def run(args):
    """Run a command and stream its output into the IDLE shell."""
    print(">", " ".join(str(a) for a in args))
    proc = subprocess.Popen(
        args,
        cwd=HERE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        text=True,
        creationflags=NO_WINDOW,
    )
    for line in proc.stdout:
        print(line.rstrip())
    return proc.wait()


def main():
    if not SOURCE.exists():
        print(f"ERROR: Could not find '{SOURCE_NAME}' in {HERE}")
        print("Edit SOURCE_NAME at the top of this script, or move the files together.")
        return

    # PyInstaller dislikes extra dots in file names, so build from a clean copy.
    build_src = HERE / f"{APP_NAME}_main.py"
    shutil.copyfile(SOURCE, build_src)

    py = sys.executable
    # IDLE may report pythonw.exe; that works fine for -m calls.

    print("Step 1/3: Installing/updating dependencies...")
    if run([py, "-m", "pip", "install", "--upgrade", "pyinstaller", "pynput"]) != 0:
        print("ERROR: Could not install dependencies. Check your internet connection.")
        return

    print("\nStep 2/3: Building executable (this can take a minute or two)...")
    cmd = [
        py, "-m", "PyInstaller",
        "--noconfirm", "--clean",
        "--onefile",
        "--windowed",                 # no black console window
        "--name", APP_NAME,
        "--hidden-import", "pynput.keyboard._win32",
        "--hidden-import", "pynput.mouse._win32",
    ]
    if ICON_FILE and (HERE / ICON_FILE).exists():
        cmd += ["--icon", str(HERE / ICON_FILE)]
    cmd.append(str(build_src))

    if run(cmd) != 0:
        print("\nERROR: Build failed. Read the messages above for the cause.")
        return

    print("\nStep 3/3: Finishing up...")
    exe = HERE / "dist" / f"{APP_NAME}.exe"
    if exe.exists():
        shutil.copyfile(exe, HERE / f"{APP_NAME}.exe")
        print(f"\nBUILD COMPLETE: {HERE / (APP_NAME + '.exe')}")
    else:
        print("Build finished but the .exe was not found in the 'dist' folder.")

    # Clean up temporary files
    build_src.unlink(missing_ok=True)
    shutil.rmtree(HERE / "build", ignore_errors=True)
    (HERE / f"{APP_NAME}.spec").unlink(missing_ok=True)


if __name__ == "__main__":
    main()
