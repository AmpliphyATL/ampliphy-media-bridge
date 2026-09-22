#!/usr/bin/env python3
"""
MetaBridge Console — Professional desktop app for AmpliPHy Radio cover art resolver.
Manages the Flask server and displays a professional dark-theme dashboard.
"""

import os
import sys
import json
import time
import subprocess
import threading
import requests
from pathlib import Path
import tkinter as tk
from tkinter import ttk, messagebox
from datetime import datetime

# Configuration
FLASK_HOST = "127.0.0.1"
FLASK_PORT = 8765
FLASK_URL = f"http://{FLASK_HOST}:{FLASK_PORT}"
DB_CONFIG_FILE = Path.home() / ".metabridge_config.json"

# Colors (dark theme)
BG_DARK = "#2a2a2a"
BG_PANEL = "#1e1e1e"
BG_INPUT = "#1a1a1a"
BORDER_COLOR = "#444"
TEXT_LIGHT = "#e0e0e0"
TEXT_DIM = "#999"
ACCENT_BLUE = "#0078d4"
SUCCESS_GREEN = "#107c10"
WARNING_RED = "#d83b01"


class MetaBridgeConsole:
    def __init__(self, root):
        self.root = root
        self.root.title("MetaBridge Console — AmpliPHy Radio")
        self.root.geometry("1200x800")
        self.root.configure(bg=BG_DARK)

        self.flask_process = None
        self.is_running = False
        self.stats_data = {}
        self.config = self.load_config()

        # Configure style
        style = ttk.Style()
        style.theme_use('clam')
        style.configure('TNotebook', background=BG_DARK, borderwidth=0)
        style.configure('TNotebook.Tab', background=BG_PANEL, foreground=TEXT_LIGHT)
        style.configure('TFrame', background=BG_DARK)
        style.configure('TLabel', background=BG_DARK, foreground=TEXT_LIGHT)

        self.build_ui()
        self.start_server()
        self.refresh_stats()

        # Handle window close
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    def load_config(self):
        """Load configuration from file or return defaults."""
        if DB_CONFIG_FILE.exists():
            with open(DB_CONFIG_FILE) as f:
                return json.load(f)
        return {
            "playoutone_ip": "127.0.0.1",
            "playoutone_port": "2766",
            "cirrus_url": "",
            "cirrus_user": "",
            "auto_start": True,
        }

    def save_config(self):
        """Save configuration to file."""
        with open(DB_CONFIG_FILE, 'w') as f:
            json.dump(self.config, f, indent=2)

    def build_ui(self):
        """Build the complete UI."""
        # Header
        header = tk.Frame(self.root, bg=ACCENT_BLUE, height=50)
        header.pack(fill=tk.X, side=tk.TOP)

        header_label = tk.Label(
            header, text="MetaBridge Console — AmpliPHy Radio",
            bg=ACCENT_BLUE, fg="white", font=("Helvetica", 16, "bold")
        )
        header_label.pack(side=tk.LEFT, padx=20, pady=10)

        status_label = tk.Label(
            header, text="● Starting...",
            bg=ACCENT_BLUE, fg=SUCCESS_GREEN, font=("Helvetica", 11)
        )
        status_label.pack(side=tk.RIGHT, padx=20, pady=10)
        self.status_label = status_label

        # Notebook (tabs)
        notebook = ttk.Notebook(self.root)
        notebook.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        # Dashboard tab
        dash_frame = tk.Frame(notebook, bg=BG_DARK)
        notebook.add(dash_frame, text="Dashboard")
        self.build_dashboard(dash_frame)

        # Settings tab
        settings_frame = tk.Frame(notebook, bg=BG_DARK)
        notebook.add(settings_frame, text="Settings")
        self.build_settings(settings_frame)

        # Logs tab
        logs_frame = tk.Frame(notebook, bg=BG_DARK)
        notebook.add(logs_frame, text="Logs")
        self.build_logs(logs_frame)

    def build_dashboard(self, parent):
        """Build the dashboard tab."""
        # Canvas with scrolling
        canvas = tk.Canvas(parent, bg=BG_DARK, highlightthickness=0)
        scrollbar = ttk.Scrollbar(parent, orient=tk.VERTICAL, command=canvas.yview)
        scrollable = tk.Frame(canvas, bg=BG_DARK)

        scrollable.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )

        canvas.create_window((0, 0), window=scrollable, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)

        # Status bar
        status_frame = tk.Frame(scrollable, bg=BG_PANEL, height=60)
        status_frame.pack(fill=tk.X, padx=10, pady=10)

        tk.Label(
            status_frame, text="● Live", bg=BG_PANEL, fg=SUCCESS_GREEN,
            font=("Helvetica", 12, "bold")
        ).pack(side=tk.LEFT, padx=15, pady=10)

        self.now_playing_label = tk.Label(
            status_frame, text="Waiting for stream...",
            bg=BG_PANEL, fg=TEXT_LIGHT, font=("Helvetica", 11)
        )
        self.now_playing_label.pack(side=tk.LEFT, padx=15, pady=10)

        # KPIs
        kpi_frame = tk.Frame(scrollable, bg=BG_DARK)
        kpi_frame.pack(fill=tk.X, padx=10, pady=10)

        self.kpi_labels = {}
        kpi_items = [
            ("events", "Events"),
            ("resolved_pct", "Resolved %"),
            ("cache_hits", "Cache Hits"),
            ("avg_resolve_ms", "Avg Resolve"),
        ]

        for key, label in kpi_items:
            kpi_box = tk.Frame(kpi_frame, bg=BG_PANEL, relief=tk.FLAT)
            kpi_box.pack(side=tk.LEFT, padx=10, pady=5)

            tk.Label(
                kpi_box, text="—", bg=BG_PANEL, fg=ACCENT_BLUE,
                font=("Helvetica", 16, "bold")
            ).pack(pady=5)

            tk.Label(
                kpi_box, text=label, bg=BG_PANEL, fg=TEXT_DIM,
                font=("Helvetica", 10)
            ).pack(pady=(0, 5))

            self.kpi_labels[key] = kpi_box.winfo_children()[0]

        # Resolved section
        self.build_section(scrollable, "RESOLVED TRACKS", SUCCESS_GREEN, "resolved")

        # Unresolved section
        self.build_section(scrollable, "UNRESOLVED TRACKS", WARNING_RED, "unresolved")

        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        self.canvas = canvas
        self.scrollable = scrollable

    def build_section(self, parent, title, color, section_key):
        """Build a collapsible section."""
        section_frame = tk.Frame(parent, bg=BG_PANEL)
        section_frame.pack(fill=tk.X, padx=10, pady=10)

        # Header
        header_frame = tk.Frame(section_frame, bg=BG_PANEL)
        header_frame.pack(fill=tk.X, padx=10, pady=10)

        tk.Label(
            header_frame, text=f"▼ {title}", bg=BG_PANEL, fg=color,
            font=("Helvetica", 12, "bold")
        ).pack(side=tk.LEFT)

        tk.Label(
            header_frame, text="(expanding soon)", bg=BG_PANEL, fg=TEXT_DIM,
            font=("Helvetica", 9)
        ).pack(side=tk.RIGHT)

        # Content area (will be populated)
        content_frame = tk.Frame(section_frame, bg=BG_DARK)
        content_frame.pack(fill=tk.X, padx=10, pady=10)

        self.section_frames = getattr(self, 'section_frames', {})
        self.section_frames[section_key] = content_frame

    def build_settings(self, parent):
        """Build the settings tab."""
        canvas = tk.Canvas(parent, bg=BG_DARK, highlightthickness=0)
        scrollbar = ttk.Scrollbar(parent, orient=tk.VERTICAL, command=canvas.yview)
        scrollable = tk.Frame(canvas, bg=BG_DARK)

        scrollable.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )

        canvas.create_window((0, 0), window=scrollable, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)

        # Form fields
        fields = [
            ("playoutone_ip", "PlayoutONE IP Address:", "127.0.0.1"),
            ("playoutone_port", "PlayoutONE Port:", "2766"),
            ("cirrus_url", "Cirrus URL:", "https://"),
            ("cirrus_user", "Cirrus Username:", ""),
        ]

        self.settings_entries = {}

        for key, label, default in fields:
            tk.Label(
                scrollable, text=label, bg=BG_DARK, fg=TEXT_LIGHT,
                font=("Helvetica", 11)
            ).pack(anchor=tk.W, padx=20, pady=(15, 5))

            entry = tk.Entry(
                scrollable, bg=BG_INPUT, fg=TEXT_LIGHT, borderwidth=1,
                insertbackground=TEXT_LIGHT, font=("Helvetica", 10)
            )
            entry.pack(fill=tk.X, padx=20, pady=(0, 10))
            entry.insert(0, self.config.get(key, default))

            self.settings_entries[key] = entry

        # Save button
        save_btn = tk.Button(
            scrollable, text="Save Settings", bg=ACCENT_BLUE, fg="white",
            font=("Helvetica", 11), padx=20, pady=10,
            command=self.save_settings
        )
        save_btn.pack(pady=20)

        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

    def build_logs(self, parent):
        """Build the logs tab."""
        log_text = tk.Text(
            parent, bg=BG_PANEL, fg=TEXT_LIGHT, font=("Courier", 10),
            insertbackground=TEXT_LIGHT, state=tk.DISABLED
        )
        log_text.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        self.log_text = log_text
        self.add_log("MetaBridge Console started.")

    def add_log(self, message):
        """Add a log entry."""
        if not hasattr(self, 'log_text'):
            return

        self.log_text.config(state=tk.NORMAL)
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.log_text.insert(tk.END, f"[{timestamp}] {message}\n")
        self.log_text.see(tk.END)
        self.log_text.config(state=tk.DISABLED)

    def save_settings(self):
        """Save settings from UI."""
        for key, entry in self.settings_entries.items():
            self.config[key] = entry.get()
        self.save_config()
        messagebox.showinfo("Success", "Settings saved!")
        self.add_log("Settings saved.")

    def start_server(self):
        """Start the Flask server."""
        if self.is_running:
            return

        try:
            # Start Flask in background
            env = os.environ.copy()
            env['METABRIDGE_LOG'] = 'WARNING'

            self.flask_process = subprocess.Popen(
                [sys.executable, "-m", "uvicorn", "app:app",
                 "--host", FLASK_HOST, "--port", str(FLASK_PORT)],
                cwd=str(Path(__file__).parent),
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )

            # Wait for server to be ready
            for _ in range(30):
                try:
                    requests.get(FLASK_URL, timeout=1)
                    self.is_running = True
                    self.status_label.config(text="● Live", fg=SUCCESS_GREEN)
                    self.add_log("Server started successfully.")

                    # Start refresh loop
                    self.refresh_loop()
                    return
                except:
                    time.sleep(0.5)

            self.add_log("ERROR: Server failed to start")
            self.status_label.config(text="● Error", fg=WARNING_RED)

        except Exception as e:
            self.add_log(f"ERROR: {e}")
            self.status_label.config(text="● Error", fg=WARNING_RED)

    def refresh_stats(self):
        """Fetch stats from the server."""
        try:
            response = requests.get(f"{FLASK_URL}/stats", timeout=2)
            if response.status_code == 200:
                self.stats_data = response.json()

                # Update KPIs
                self.kpi_labels['events'].config(
                    text=str(self.stats_data.get('n', 0))
                )
                self.kpi_labels['resolved_pct'].config(
                    text=f"{self.stats_data.get('resolved_pct', 0)}%"
                )
                self.kpi_labels['cache_hits'].config(
                    text=str(self.stats_data.get('cache_hits', 0))
                )
                avg_ms = self.stats_data.get('avg_resolve_ms')
                self.kpi_labels['avg_resolve_ms'].config(
                    text=f"{int(avg_ms) if avg_ms else 0} ms"
                )

        except Exception as e:
            pass

    def refresh_loop(self):
        """Periodically refresh stats."""
        if self.is_running:
            self.refresh_stats()
            self.root.after(2000, self.refresh_loop)

    def on_close(self):
        """Handle window close."""
        if messagebox.askyesno("Quit", "Stop MetaBridge and close?"):
            if self.flask_process:
                self.flask_process.terminate()
            self.root.destroy()


if __name__ == "__main__":
    root = tk.Tk()
    app = MetaBridgeConsole(root)
    root.mainloop()
