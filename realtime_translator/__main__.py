"""エントリポイント: python -m realtime_translator"""
import logging
import tkinter as tk
from pathlib import Path

from .constants import LOG_PATH
from .app import TranslatorApp


def main() -> None:
    logging.basicConfig(
        level=logging.ERROR,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.FileHandler(LOG_PATH, encoding="utf-8"),
            logging.StreamHandler(),
        ],
    )
    root = tk.Tk()
    root.minsize(700, 620)
    app = TranslatorApp(root)
    root.protocol("WM_DELETE_WINDOW", app.on_close)

    def _tk_exception_handler(exc, val, tb):
        logging.error("tkinter callback exception", exc_info=(exc, val, tb))
        app._append_error(f"内部エラー: {val}")
    root.report_callback_exception = _tk_exception_handler

    root.mainloop()


if __name__ == "__main__":
    main()
