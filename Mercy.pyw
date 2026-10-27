"""Double-click launcher for Windows: .pyw files open with pythonw, so no console window appears."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from main import main

main()
