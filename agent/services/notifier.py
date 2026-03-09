"""Desktop notification service — premium styled popup window.

Uses tkinter (bundled with Python) to show a modern notification
with the NGL logo, glowing status badge, and slide-in animation.

Usage:
    from services.notifier import notify
    notify("Action Complete", "Your invoices have been sent successfully!")
"""

import logging
import threading
import tkinter as tk
from pathlib import Path

logger = logging.getLogger("ngl.notifier")

# Global toggle — set via /settings/notifications endpoint
_enabled = False

# Resolve the NGL logo path
_LOGO_PATH = Path(__file__).resolve().parent.parent.parent / "app" / "assets" / "images" / "NGL logo.png"

# Theme colors
_ORANGE = "#F26522"
_GREEN = "#22C55E"
_GREEN_DIM = "#166534"
_RED = "#EF4444"
_RED_DIM = "#7F1D1D"
_YELLOW = "#EAB308"
_YELLOW_DIM = "#713F12"
_BG_DARK = "#0F172A"
_BG_CARD = "#1E293B"
_BG_INNER = "#162032"
_BORDER = "#334155"
_TEXT_PRIMARY = "#F1F5F9"
_TEXT_SECONDARY = "#94A3B8"
_TEXT_DIM = "#475569"

# Badge presets by keyword detection
_BADGE_PRESETS = {
    "success":  ("Success",  _GREEN,  _GREEN_DIM),
    "complete": ("Complete", _GREEN,  _GREEN_DIM),
    "sent":     ("Sent",     _GREEN,  _GREEN_DIM),
    "error":    ("Error",    _RED,    _RED_DIM),
    "fail":     ("Failed",   _RED,    _RED_DIM),
    "expire":   ("Expired",  _RED,    _RED_DIM),
    "warning":  ("Warning",  _YELLOW, _YELLOW_DIM),
    "attention": ("Attention", _YELLOW, _YELLOW_DIM),
    "login":    ("Action Required", _YELLOW, _YELLOW_DIM),
}


def set_enabled(enabled: bool) -> None:
    """Enable or disable desktop notifications."""
    global _enabled
    _enabled = enabled
    logger.info("Desktop notifications %s", "enabled" if enabled else "disabled")


def is_enabled() -> bool:
    return _enabled


def notify(title: str, message: str, *, force: bool = False) -> None:
    """Show a premium styled desktop notification.

    Args:
        title: Notification title (e.g. "Action Complete").
        message: Body text (e.g. "Your invoices have been sent").
        force: If True, show even when notifications are disabled.
    """
    if not force and not _enabled:
        return

    threading.Thread(
        target=_show_popup,
        args=(title, message),
        daemon=True,
    ).start()


def _detect_badge(title: str, message: str) -> tuple:
    """Auto-detect badge style from title/message keywords."""
    combined = (title + " " + message).lower()
    for keyword, preset in _BADGE_PRESETS.items():
        if keyword in combined:
            return preset
    return ("Info", _ORANGE, "#7C2D12")


def _show_popup(title: str, message: str) -> None:
    """Show a premium notification with slide-in animation."""
    try:
        root = tk.Tk()
        root.withdraw()

        # Frameless, always on top, transparent background for shadow effect
        root.overrideredirect(True)
        root.attributes("-topmost", True)
        root.attributes("-alpha", 0.0)  # start invisible for fade-in
        root.configure(bg=_BG_DARK)

        # Dimensions
        width = 400
        height = 160

        # Position: bottom-right, start off-screen to the right
        screen_w = root.winfo_screenwidth()
        screen_h = root.winfo_screenheight()
        final_x = screen_w - width - 24
        start_x = screen_w + 10
        y = screen_h - height - 70
        root.geometry(f"{width}x{height}+{start_x}+{y}")

        # Detect badge type
        badge_text, badge_fg, badge_bg = _detect_badge(title, message)

        # ── Outer shadow frame ──
        shadow = tk.Frame(root, bg=_BG_DARK, bd=0, highlightthickness=1,
                          highlightbackground=_BORDER)
        shadow.pack(fill="both", expand=True, padx=1, pady=1)

        # ── Main card ──
        card = tk.Frame(shadow, bg=_BG_CARD, padx=24, pady=18)
        card.pack(fill="both", expand=True)

        # ── Row 1: Badge + close button ──
        top_row = tk.Frame(card, bg=_BG_CARD)
        top_row.pack(fill="x")

        # Glowing status badge
        badge_frame = tk.Frame(top_row, bg=badge_bg, padx=10, pady=3,
                               highlightthickness=1, highlightbackground=badge_fg, bd=0)
        badge_frame.pack(side="left")
        tk.Label(badge_frame, text=badge_text.upper(), font=("Segoe UI", 7, "bold"),
                 fg=badge_fg, bg=badge_bg, bd=0).pack()

        # Close button
        close_btn = tk.Label(top_row, text="\u2715", font=("Segoe UI", 11),
                             fg=_TEXT_DIM, bg=_BG_CARD, cursor="hand2", padx=2)
        close_btn.pack(side="right")
        close_btn.bind("<Button-1>", lambda e: _fade_out(root))
        close_btn.bind("<Enter>", lambda e: close_btn.configure(fg=_TEXT_PRIMARY))
        close_btn.bind("<Leave>", lambda e: close_btn.configure(fg=_TEXT_DIM))

        # ── Row 2: Logo + title — with generous spacing ──
        title_row = tk.Frame(card, bg=_BG_CARD)
        title_row.pack(fill="x", pady=(16, 0))

        # Try to load logo
        logo_image = None
        if _LOGO_PATH.exists():
            try:
                from PIL import Image, ImageTk
                img = Image.open(_LOGO_PATH).resize((32, 32), Image.LANCZOS)
                logo_image = ImageTk.PhotoImage(img)
                logo_lbl = tk.Label(title_row, image=logo_image, bg=_BG_CARD, bd=0)
                logo_lbl.image = logo_image
                logo_lbl.pack(side="left", padx=(0, 14))
            except ImportError:
                _text_logo(title_row)
        else:
            _text_logo(title_row)

        # Title text
        tk.Label(title_row, text=title, font=("Segoe UI", 12, "bold"),
                 fg=_TEXT_PRIMARY, bg=_BG_CARD, anchor="w").pack(side="left")

        # ── Row 3: Message body — indented to align with title ──
        msg_frame = tk.Frame(card, bg=_BG_CARD)
        msg_frame.pack(fill="x", pady=(8, 0))

        # Spacer to align with text after logo
        tk.Frame(msg_frame, bg=_BG_CARD, width=46).pack(side="left")
        tk.Label(msg_frame, text=message, font=("Segoe UI", 9),
                 fg=_TEXT_SECONDARY, bg=_BG_CARD, anchor="w",
                 justify="left", wraplength=width - 100).pack(side="left", fill="x")

        # ── Bottom: subtle app name ──
        bottom = tk.Frame(card, bg=_BG_CARD)
        bottom.pack(fill="x", side="bottom", pady=(10, 0))
        tk.Label(bottom, text="NGL Accounting", font=("Segoe UI", 7),
                 fg=_TEXT_DIM, bg=_BG_CARD).pack(side="right")

        # Show and animate
        root.deiconify()
        _slide_in(root, start_x, final_x, y, width, height)

        # Auto-close after 12 seconds
        root.after(12000, lambda: _fade_out(root))

        root.mainloop()

    except Exception as e:
        logger.debug("Notification popup failed: %s", e)


def _text_logo(parent: tk.Frame) -> None:
    """Fallback text logo when image can't load."""
    lbl = tk.Label(parent, text="NGL", font=("Segoe UI", 12, "bold"),
                   fg=_ORANGE, bg=_BG_CARD)
    lbl.pack(side="left", padx=(0, 14))


def _slide_in(root: tk.Tk, start_x: int, end_x: int, y: int, w: int, h: int) -> None:
    """Animate the window sliding in from the right with fade."""
    steps = 16
    duration = 300  # ms total
    interval = duration // steps

    def step(i: int) -> None:
        if i > steps:
            return
        try:
            # Ease-out cubic
            t = i / steps
            ease = 1 - (1 - t) ** 3
            current_x = int(start_x + (end_x - start_x) * ease)
            alpha = min(1.0, ease * 1.1)  # fade in slightly faster than slide

            root.geometry(f"{w}x{h}+{current_x}+{y}")
            root.attributes("-alpha", alpha)
            root.after(interval, lambda: step(i + 1))
        except tk.TclError:
            pass  # window was closed during animation

    step(0)


def _fade_out(root: tk.Tk) -> None:
    """Fade out and destroy the window."""
    steps = 8
    interval = 25

    def step(i: int) -> None:
        if i > steps:
            try:
                root.destroy()
            except tk.TclError:
                pass
            return
        try:
            alpha = 1.0 - (i / steps)
            root.attributes("-alpha", alpha)
            root.after(interval, lambda: step(i + 1))
        except tk.TclError:
            pass

    step(0)
