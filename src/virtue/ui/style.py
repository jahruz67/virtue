"""Layout accents that retain platform fonts, palettes and interactive controls."""

STYLE = """
QMainWindow, QDialog, QWidget#workspace { background: palette(window); }
QFrame#toolbar { background: palette(base); border-bottom: 1px solid palette(mid); }
QLabel { background: transparent; }
QLabel#sectionTitle { font-size: 20px; font-weight: 600; }
QLabel#muted { color: palette(window-text); }
QLabel#vmTitle { font-size: 20px; font-weight: 600; }
QLabel#statusChip { background: palette(alternate-base); border-radius: 10px; padding: 4px 9px; }
QLabel#statusChip[running="true"], QLabel#success { color: palette(link); }
QLabel#attention, QLabel#error { font-weight: 600; }
QLabel#vmPreview { background: transparent; }
QFrame#card { background: palette(base); border: 1px solid palette(mid); border-radius: 12px; }
QFrame#banner, QFrame#warning { background: palette(alternate-base); border: 1px solid palette(mid); border-radius: 8px; }
QPushButton#primary, QPushButton#danger { font-weight: 600; }
QPushButton[compact="true"] { padding: 6px; }
QPushButton[compact="true"]::menu-indicator { image: none; width: 0px; }
QScrollArea, QWidget#vmList { background: palette(window); border: none; }
QWidget#settingsPage { background: palette(base); }
QProgressBar#setupProgress { min-height: 6px; max-height: 6px; }
"""
