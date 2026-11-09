STYLE = """
QWidget { background: #101723; color: #e5edf7; font-family: 'Segoe UI', 'Noto Sans', sans-serif; font-size: 14px; }
QMainWindow, QDialog { background: #101723; }
QLabel { background: transparent; }
QLabel#brand { font-size: 25px; font-weight: 700; color: #f6fafc; }
QLabel#headline { font-size: 30px; font-weight: 700; }
QLabel#sectionTitle { font-size: 20px; font-weight: 600; }
QLabel#muted { color: #9aaec5; }
QLabel#eyebrow { color: #66dac2; font-size: 11px; font-weight: 700; }
QLabel#vmTitle { font-size: 21px; font-weight: 600; }
QFrame#card { background: #192332; border: 1px solid #29374b; border-radius: 14px; }
QFrame#banner { background: #1c3040; border: 1px solid #37566c; border-radius: 9px; }
QFrame#warning { background: #352c20; border: 1px solid #665239; border-radius: 9px; }
QPushButton, QToolButton { background: #253449; border: 1px solid #364961; padding: 9px 15px; border-radius: 7px; font-weight: 600; }
QPushButton:hover, QToolButton:hover { background: #31445b; border-color: #52738f; }
QPushButton:pressed, QToolButton:pressed { background: #1c2b3d; }
QPushButton:disabled, QToolButton:disabled { background: #1c2634; color: #687c93; border-color: #283447; }
QPushButton#primary { background: #64dcc1; color: #0c2428; border-color: #64dcc1; }
QPushButton#primary:hover { background: #88e9d2; }
QPushButton#primary:disabled { background: #294d4a; color: #729f98; border-color: #294d4a; }
QPushButton#danger { color: #ffb2ad; border-color: #86564f; }
QLineEdit, QSpinBox, QComboBox, QTextEdit, QPlainTextEdit, QListWidget { background: #131d2b; border: 1px solid #36475e; border-radius: 6px; padding: 8px; selection-background-color: #375f72; }
QLineEdit:focus, QSpinBox:focus, QComboBox:focus { border-color: #64dcc1; }
QComboBox QAbstractItemView { background: #1d2c3d; selection-background-color: #3b5b70; }
QSpinBox::up-button, QSpinBox::down-button { width: 18px; }
QCheckBox { spacing: 9px; }
QCheckBox::indicator { width: 17px; height: 17px; }
QCheckBox::indicator:unchecked { border: 1px solid #52647b; border-radius: 4px; background: #162132; }
QCheckBox::indicator:checked { border: 1px solid #64dcc1; background: #64dcc1; border-radius: 4px; image: none; }
QMenu { background: #1c293a; border: 1px solid #3a4d65; padding: 6px; }
QMenu::item { padding: 8px 22px; border-radius: 4px; }
QMenu::item:selected { background: #355066; }
QMenu::item:disabled { color: #75879c; }
QScrollArea { border: none; }
QScrollBar:vertical { background: #101723; width: 10px; }
QScrollBar::handle:vertical { background: #3b4d62; border-radius: 5px; min-height: 30px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QTabWidget::pane { border: 1px solid #30425a; border-radius: 8px; }
QTabBar::tab { background: #172232; padding: 10px 18px; color: #9aadc4; }
QTabBar::tab:selected { color: #72e4cb; background: #26394b; }
QGroupBox { border: 1px solid #30425a; border-radius: 8px; margin-top: 12px; padding-top: 14px; }
QGroupBox::title { subcontrol-origin: margin; left: 12px; padding: 0 6px; }
QProgressBar { border: none; border-radius: 3px; background: #223144; max-height: 5px; }
QProgressBar::chunk { background: #64dcc1; border-radius: 3px; }
QProgressBar#setupProgress { min-height: 8px; max-height: 8px; }
QProgressBar#setupProgress[attention="true"]::chunk { background: #ffc08c; }
QStatusBar { color: #9daec2; background: #101723; }
QToolTip { color: #ecf5ff; background: #263b50; border: 1px solid #526881; padding: 6px; }
"""
