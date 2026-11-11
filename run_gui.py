"""Ponto de entrada do executável (PyInstaller) e atalho: python run_gui.py"""
import sys

from mdconv.gui.main_window import main

if __name__ == "__main__":
    import multiprocessing
    multiprocessing.freeze_support()
    sys.exit(main())
