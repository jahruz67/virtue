"""Shared visual language for the desktop control center and its dialogs."""

STYLE = """
QWidget { color: #202631; font-family: 'Segoe UI', 'Noto Sans', sans-serif; font-size: 14px; }
QMainWindow, QDialog, QWidget#workspace { background: #f5f6f8; }
QFrame#toolbar { background: #ffffff; border-bottom: 1px solid #dce1e8; }
QLabel { background: transparent; }
QLabel#sectionTitle { font-size: 20px; font-weight: 600; }
QLabel#muted { color: #5c6676; }
QLabel#vmTitle { font-size: 20px; font-weight: 600; }
QLabel#statusChip { color: #526071; background: #edf0f4; border-radius: 10px; padding: 4px 9px; font-size: 12px; }
QLabel#statusChip[running="true"] { color: #14724d; background: #edf8f1; }
QLabel#success { color: #14724d; }
QLabel#attention { color: #965a10; }
QLabel#error { color: #b42332; }
QLabel#vmPreview { background: transparent; }
QFrame#card { background: #ffffff; border: 1px solid #dce1e8; border-radius: 12px; }
QFrame#banner { background: #edf8f1; border: 1px solid #cde5d6; border-radius: 8px; }
QFrame#banner QLabel { color: #23613f; }
QFrame#warning { background: #fff6e7; border: 1px solid #e9c98a; border-radius: 8px; }
QFrame#warning QLabel { color: #805013; }
QPushButton, QToolButton { background: #ffffff; border: 1px solid #b8c2cf; padding: 8px 13px; border-radius: 6px; font-weight: 600; }
QPushButton:hover, QToolButton:hover { background: #f0f3f8; border-color: #8d9bad; }
QPushButton:pressed, QToolButton:pressed { background: #e5ebf4; }
QPushButton:focus, QToolButton:focus { border: 2px solid #245edb; padding: 7px 12px; }
QPushButton:disabled, QToolButton:disabled { background: #f0f2f5; color: #8b95a4; border-color: #dce1e8; }
QPushButton#primary { background: #245edb; color: #ffffff; border-color: #245edb; }
QPushButton#primary:hover { background: #1d4fc0; border-color: #1d4fc0; }
QPushButton#primary:pressed { background: #1945a7; }
QPushButton#primary:focus { border: 2px solid #102c70; }
QPushButton#primary:disabled { background: #e4eaf6; color: #8999bb; border-color: #d5def0; }
QPushButton#danger { color: #b42332; border-color: #d98b94; }
QPushButton#danger:hover { background: #fff0f1; }
QPushButton[compact="true"] { font-size: 18px; padding: 6px; }
QPushButton[compact="true"]:focus { padding: 5px; }
QPushButton[compact="true"]::menu-indicator { image: none; width: 0px; }
QLineEdit, QSpinBox, QComboBox, QTextEdit, QPlainTextEdit, QTextBrowser, QListWidget { background: #ffffff; border: 1px solid #b8c2cf; border-radius: 6px; padding: 8px; selection-background-color: #245edb; selection-color: #ffffff; }
QLineEdit:focus, QSpinBox:focus, QComboBox:focus, QTextBrowser:focus { border: 2px solid #245edb; padding: 7px; }
QLineEdit:disabled, QSpinBox:disabled, QComboBox:disabled { background: #eef0f4; color: #7a8697; border-color: #dce1e8; }
QComboBox QAbstractItemView { background: #ffffff; color: #202631; selection-background-color: #e8efff; selection-color: #1949b2; border: 1px solid #b8c2cf; }
QSpinBox::up-button, QSpinBox::down-button { width: 20px; }
QCheckBox { spacing: 9px; background: transparent; border: 2px solid transparent; border-radius: 4px; padding: 2px; }
QCheckBox::indicator { width: 17px; height: 17px; }
QCheckBox:focus { border-color: #245edb; }
QMenu { background: #ffffff; color: #202631; border: 1px solid #c6ced9; padding: 6px; menu-scrollable: 1; }
QMenu::item { padding: 8px 22px; border-radius: 4px; }
QMenu::item:selected { background: #e8efff; color: #1949b2; }
QMenu::item:disabled { color: #8b95a4; }
QMenu::separator { height: 1px; background: #e3e7ed; margin: 5px 10px; }
QMenu::scroller { height: 22px; }
QScrollArea, QWidget#vmList { background: #f5f6f8; border: none; }
QScrollBar:vertical { background: #f5f6f8; width: 10px; }
QScrollBar::handle:vertical { background: #c0c8d4; border-radius: 5px; min-height: 30px; }
QScrollBar::handle:vertical:hover { background: #9aa7b8; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QTabWidget::pane { background: #ffffff; border: 1px solid #dce1e8; border-radius: 8px; }
QWidget#settingsPage { background: #ffffff; }
QTabBar::tab { background: #edf0f4; border-bottom: 2px solid transparent; padding: 11px 18px; color: #5c6676; }
QTabBar::tab:selected { color: #1949b2; background: #ffffff; border-bottom: 2px solid #245edb; }
QTabBar::tab:hover { color: #1949b2; background: #e8efff; }
QTabBar::tab:focus { border: 2px solid #245edb; padding: 10px 16px; }
QGroupBox { background: #ffffff; border: 1px solid #dce1e8; border-radius: 8px; margin-top: 12px; padding-top: 14px; }
QGroupBox::title { subcontrol-origin: margin; left: 12px; padding: 0 6px; }
QProgressBar { border: none; border-radius: 3px; background: #e5ebf4; max-height: 5px; }
QProgressBar::chunk { background: #245edb; border-radius: 3px; }
QProgressBar#setupProgress { min-height: 6px; max-height: 6px; }
QProgressBar#setupProgress[attention="true"]::chunk { background: #b57318; }
QStatusBar { color: #5c6676; background: #f5f6f8; border-top: 1px solid #e3e7ed; font-size: 12px; }
QToolTip { color: #202631; background: #ffffff; border: 1px solid #b8c2cf; padding: 6px; }
"""
