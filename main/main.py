"""Main entry point for Tapp Hit Detection Calibration and Test Wizard.
"""

import os
import sys
from pathlib import Path

# Ensure project repository root is on sys.path and set working directory to repo root
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
os.chdir(str(REPO_ROOT))

import tkinter as tk
from tapp_app.gui import TappAppGUI


def main():
    root = tk.Tk()
    app = TappAppGUI(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
