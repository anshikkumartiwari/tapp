"""Main entry point for Tapp Hit Detection Test Application.
"""

import sys
import tkinter as tk
from tapp_app.gui import TappAppGUI


def main():
    root = tk.Tk()
    app = TappAppGUI(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
