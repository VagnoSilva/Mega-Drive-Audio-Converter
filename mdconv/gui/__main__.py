"""python -m mdconv.gui"""
import sys

try:
    from mdconv.gui.main_window import main
except ImportError as e:  # PySide6 ausente
    if "PySide6" in str(e):
        sys.exit("A interface precisa do PySide6. Instale com:  pip install PySide6")
    raise

sys.exit(main())
