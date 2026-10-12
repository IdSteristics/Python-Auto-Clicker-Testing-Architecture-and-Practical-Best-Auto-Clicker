import importlib.util
import sys

# Mapping of script files to their required top-level import modules
SCRIPTS_AND_MODULES = {
    "3.1 Pro High.py": ["threading", "time", "random", "tkinter", "typing", "pynput"],
    "3.6 Flash High.py": ["enum", "random", "threading", "time", "tkinter", "dataclasses", "typing", "pynput"],
    "3.1 Pro Low.py": ["tkinter", "threading", "time", "random", "typing", "pynput"],
    "3.6 Flash Low.py": ["random", "threading", "time", "tkinter", "typing", "pynput"],
    "ChatGPT Think.py": ["queue", "random", "threading", "time", "tkinter", "dataclasses", "typing", "pyautogui", "pynput"],
    "ChatGPT.py": ["random", "threading", "time", "tkinter", "typing", "pyautogui", "pynput"],
    "Sonnet 5.5 Medium.py": ["ctypes", "itertools", "queue", "random", "threading", "time", "tkinter", "collections", "dataclasses", "enum", "pynput"],
}

# Mapping module names to their PyPI pip package names
PIP_PACKAGES = {
    "pynput": "pynput",
    "pyautogui": "pyautogui",
}

def is_module_available(module_name: str) -> bool:
    """Check if a module is importable in the current Python environment."""
    try:
        return importlib.util.find_spec(module_name) is not None
    except (ImportError, AttributeError, ValueError):
        return False

def main():
    print("=" * 65)
    print("     MODULE IMPORT CHECKER FOR AUTO CLICKER SCRIPTS")
    print("=" * 65)
    print(f"Python Executable: {sys.executable}")
    print(f"Python Version   : {sys.version.split()[0]}\n")

    missing_third_party = set()
    missing_builtins = set()

    for filename, modules in SCRIPTS_AND_MODULES.items():
        print(f"File: {filename}")
        missing_in_file = []
        for mod in modules:
            available = is_module_available(mod)
            status = "✓ Installed" if available else "✗ MISSING"
            print(f"  - {mod:<15}: {status}")
            
            if not available:
                missing_in_file.append(mod)
                if mod in PIP_PACKAGES:
                    missing_third_party.add(PIP_PACKAGES[mod])
                else:
                    missing_builtins.add(mod)

        if missing_in_file:
            print(f"  Status: ✖ Missing {len(missing_in_file)} module(s): {', '.join(missing_in_file)}\n")
        else:
            print("  Status: ✔ Ready to run!\n")

    print("=" * 65)
    if missing_third_party or missing_builtins:
        print("SUMMARY & INSTALLATION INSTRUCTIONS")
        print("=" * 65)
        
        if missing_third_party:
            pkgs = " ".join(sorted(missing_third_party))
            print("\nTo install missing external packages, run this in Terminal/Command Prompt:")
            print(f"    pip install {pkgs}\n")
            print("Or run this directly inside the Python IDLE shell:")
            print(f"    import subprocess; subprocess.run(['pip', 'install', '{' \', \' '.join(sorted(missing_third_party))}'])")

        if missing_builtins:
            print(f"\nNote: The following standard modules were not found: {', '.join(missing_builtins)}")
            print("If tkinter is missing, install python3-tk via your operating system package manager.")
    else:
        print("ALL SET! All required imports across all 7 files are available.")
    print("=" * 65)

if __name__ == "__main__":
    main()
