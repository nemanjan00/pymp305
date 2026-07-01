"""Dracula theme (palette + Qt stylesheet) for the MP305 GUI.

Official Dracula colors: https://draculatheme.com  — bg #282a36, fg #f8f8f2,
purple #bd93f9, pink #ff79c6, green #50fa7b, cyan #8be9fd, orange #ffb86c, red #ff5555.
"""

C = {
    "bg":        "#21222c",   # app background (Dracula darker bg)
    "panel":     "#191a21",   # top bar
    "card":      "#282a36",   # Dracula background
    "card_hi":   "#343746",   # Background Light / floating interactive elements
    "hover":     "#424450",   # Background Lighter (hover surfaces)
    "line":      "#353747",   # opaque current-line fallback
    "stroke":    "#44475a",   # Dracula current line / selection
    "focus":     "#815cd6",   # Functional Purple (focus rings)
    "text":      "#f8f8f2",   # Dracula foreground
    "muted":     "#6272a4",   # Dracula comment
    "accent":    "#bd93f9",   # Dracula purple (signature accent)
    "accent_hi": "#caa9fa",
    "on":        "#50fa7b",   # Dracula green — output ON / good
    "off":       "#6272a4",
    "volt":      "#ffb86c",   # chart: voltage  (orange)
    "curr":      "#8be9fd",   # chart: current  (cyan)
    "pow":       "#50fa7b",   # chart: power    (green)
    "danger":    "#ff5555",   # Dracula red
    "warn":      "#f1fa8c",   # Dracula yellow
    "pink":      "#ff79c6",
}

STYLESHEET = f"""
* {{ font-family: "Inter", "Segoe UI", "Bahnschrift", sans-serif; color: {C['text']}; }}
QWidget#root {{ background: {C['bg']}; }}
QFrame.card {{
    background: {C['card']}; border: 1px solid {C['stroke']}; border-radius: 14px;
}}
QFrame.topbar {{ background: {C['panel']}; border-bottom: 1px solid {C['stroke']}; }}
QLabel.h1 {{ font-size: 28px; font-weight: 800; letter-spacing: 2px; color: {C['accent']}; }}
QLabel.sub {{ color: {C['muted']}; font-size: 12px; }}
QLabel.cardTitle {{ color: {C['muted']}; font-size: 12px; font-weight: 700; letter-spacing: 1px; }}
QLabel.bigValue {{ font-size: 46px; font-weight: 800; }}
QLabel.unit {{ color: {C['muted']}; font-size: 18px; font-weight: 700; }}
QLabel.statValue {{ font-size: 18px; font-weight: 700; }}

QPushButton {{
    background: {C['card_hi']}; border: 1px solid {C['stroke']}; border-radius: 10px;
    padding: 9px 16px; font-weight: 700;
}}
QPushButton:hover {{ background: {C['stroke']}; }}
QPushButton#primary {{ background: {C['accent']}; border: none; color: {C['panel']}; }}
QPushButton#primary:hover {{ background: {C['accent_hi']}; }}
QPushButton#danger {{ background: transparent; border: 1px solid {C['danger']}; color: {C['danger']}; }}
QPushButton#danger:hover {{ background: {C['danger']}; color: {C['panel']}; }}
QPushButton:disabled {{ color: {C['off']}; background: {C['card']}; border-color: {C['stroke']}; }}
QPushButton.tab {{ background: transparent; border: none; border-radius: 9px; color: {C['muted']}; padding: 8px 18px; }}
QPushButton.tab:hover {{ color: {C['text']}; }}
QPushButton.tab:checked {{ background: {C['card']}; color: {C['accent']}; }}

QDoubleSpinBox {{
    background: {C['bg']}; border: 1px solid {C['stroke']}; border-radius: 10px;
    padding: 8px 10px; font-size: 20px; font-weight: 800; min-height: 22px;
    selection-background-color: {C['accent']};
}}
QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{ width: 0; }}
QSlider::groove:horizontal {{ height: 6px; background: {C['stroke']}; border-radius: 3px; }}
QSlider::sub-page:horizontal {{ background: {C['accent']}; border-radius: 3px; }}
QSlider::handle:horizontal {{
    background: {C['text']}; width: 16px; height: 16px; margin: -6px 0; border-radius: 8px;
}}
QSlider::handle:horizontal:hover {{ background: {C['accent']}; }}
QLabel#pill {{
    background: {C['card_hi']}; border: 1px solid {C['stroke']}; border-radius: 11px;
    padding: 3px 12px; font-size: 12px; font-weight: 700; color: {C['muted']};
}}
QToolTip {{ background: {C['card']}; color: {C['text']}; border: 1px solid {C['stroke']}; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px 2px 2px 0; }}
QScrollBar::handle:vertical {{ background: {C['stroke']}; border-radius: 5px; min-height: 28px; }}
QScrollBar::handle:vertical:hover {{ background: {C['accent']}; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; background: transparent; }}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 0 2px 2px 2px; }}
QScrollBar::handle:horizontal {{ background: {C['stroke']}; border-radius: 5px; min-width: 28px; }}
QScrollBar::handle:horizontal:hover {{ background: {C['accent']}; }}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; background: transparent; }}
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{ background: transparent; }}
"""
