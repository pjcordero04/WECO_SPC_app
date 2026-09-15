"""
Move ETEST CSV files one at a time to simulate real-time cable testing.

Moves individual CSV test report files from a source folder to a destination folder
with a configurable delay between each move. Used for testing the ETEST WECO SPC app
without a live cable tester connected.

GUI version with Pause/Resume button, source/destination folder browsing,
and configurable delay.
"""

import os
import re
import shutil
import threading
import tkinter as tk
from tkinter import ttk, filedialog

DELAY_SECONDS = 1


def extract_cable_number(filename):
    """
    Extract the cable number from the ETEST filename for sorting.

    Filename format: 2175420047_500V_LINK_TestReport_e-cct-146_2_1.csv
    The last number before .csv is the cable number within the run.
    The second-to-last number is the run number.
    Returns (run_number, cable_number) tuple for sorting.
    """
    name = os.path.splitext(filename)[0]
    parts = name.split('_')
    try:
        cable_num = int(parts[-1])
        run_num = int(parts[-2])
        return (run_num, cable_num)
    except (ValueError, IndexError):
        return (0, 0)


def get_sorted_csv_files(source_dir):
    """Get all CSV files sorted by run number then cable number."""
    csv_files = [
        f for f in os.listdir(source_dir)
        if f.lower().endswith('.csv') and os.path.isfile(os.path.join(source_dir, f))
    ]
    csv_files.sort(key=lambda name: extract_cable_number(name))
    return csv_files


class MoveEtestDataApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Move ETEST Data — CSV File Mover")
        self.root.geometry("750x550")
        self.root.resizable(True, True)

        self.paused = False
        self.running = False
        self.pause_event = threading.Event()
        self.pause_event.set()  # Not paused initially

        self.source_var = tk.StringVar()
        self.dest_var = tk.StringVar()
        self.delay_var = tk.DoubleVar(value=DELAY_SECONDS)

        self._build_ui()

    def _build_ui(self):
        # --- Source folder ---
        src_frame = ttk.LabelFrame(self.root, text="Source Folder (CSV files to move)", padding=10)
        src_frame.pack(fill=tk.X, padx=10, pady=(10, 5))

        ttk.Entry(src_frame, textvariable=self.source_var, width=70).pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(src_frame, text="Browse...", command=self.browse_source).pack(side=tk.LEFT, padx=(5, 0))

        # --- Destination folder ---
        dst_frame = ttk.LabelFrame(self.root, text="Destination Folder (where SPC app monitors)", padding=10)
        dst_frame.pack(fill=tk.X, padx=10, pady=(0, 5))

        ttk.Entry(dst_frame, textvariable=self.dest_var, width=70).pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(dst_frame, text="Browse...", command=self.browse_dest).pack(side=tk.LEFT, padx=(5, 0))

        # --- Settings ---
        settings_frame = ttk.LabelFrame(self.root, text="Settings", padding=10)
        settings_frame.pack(fill=tk.X, padx=10, pady=(0, 5))

        ttk.Label(settings_frame, text="Delay between moves (seconds):").pack(side=tk.LEFT)
        delay_entry = ttk.Entry(settings_frame, textvariable=self.delay_var, width=6)
        delay_entry.pack(side=tk.LEFT, padx=(10, 0))

        # --- Controls ---
        ctrl_frame = ttk.Frame(self.root, padding=10)
        ctrl_frame.pack(fill=tk.X, padx=10)

        self.start_btn = ttk.Button(ctrl_frame, text="Start", command=self.start_moving)
        self.start_btn.pack(side=tk.LEFT, padx=(0, 5))

        self.pause_btn = ttk.Button(ctrl_frame, text="Pause", command=self.toggle_pause, state=tk.DISABLED)
        self.pause_btn.pack(side=tk.LEFT, padx=(0, 5))

        self.stop_btn = ttk.Button(ctrl_frame, text="Stop", command=self.stop_moving, state=tk.DISABLED)
        self.stop_btn.pack(side=tk.LEFT, padx=(0, 5))

        self.status_label = ttk.Label(ctrl_frame, text="Ready", foreground="gray")
        self.status_label.pack(side=tk.LEFT, padx=10)

        # --- Progress bar ---
        progress_frame = ttk.Frame(self.root, padding=(10, 0))
        progress_frame.pack(fill=tk.X, padx=10)

        self.progress_var = tk.DoubleVar(value=0)
        self.progress_bar = ttk.Progressbar(progress_frame, variable=self.progress_var, maximum=100)
        self.progress_bar.pack(fill=tk.X)

        self.progress_label = ttk.Label(progress_frame, text="0 / 0")
        self.progress_label.pack(anchor=tk.E)

        # --- Log area ---
        log_frame = ttk.LabelFrame(self.root, text="Log", padding=5)
        log_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=(5, 10))

        self.log_text = tk.Text(log_frame, height=12, state=tk.DISABLED, font=("Consolas", 9))
        scrollbar = ttk.Scrollbar(log_frame, orient=tk.VERTICAL, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.log_text.pack(fill=tk.BOTH, expand=True)

    def browse_source(self):
        path = filedialog.askdirectory(title="Select Source Folder (CSV files)")
        if path:
            self.source_var.set(path)

    def browse_dest(self):
        path = filedialog.askdirectory(title="Select Destination Folder")
        if path:
            self.dest_var.set(path)

    def log(self, message):
        """Append a message to the log area (thread-safe)."""
        self.root.after(0, self._append_log, message)

    def _append_log(self, message):
        self.log_text.configure(state=tk.NORMAL)
        self.log_text.insert(tk.END, message + "\n")
        self.log_text.see(tk.END)
        self.log_text.configure(state=tk.DISABLED)

    def start_moving(self):
        """Start the move operation in a background thread."""
        if self.running:
            return

        source = self.source_var.get().strip()
        dest = self.dest_var.get().strip()

        if not source or not os.path.isdir(source):
            self.log("ERROR: Please select a valid source folder.")
            return
        if not dest:
            self.log("ERROR: Please select a destination folder.")
            return

        self.running = True
        self.stopped = False
        self.paused = False
        self.pause_event.set()
        self.start_btn.configure(state=tk.DISABLED)
        self.pause_btn.configure(state=tk.NORMAL)
        self.stop_btn.configure(state=tk.NORMAL)
        self.status_label.configure(text="Running...", foreground="green")

        thread = threading.Thread(target=self._move_worker, args=(source, dest), daemon=True)
        thread.start()

    def stop_moving(self):
        """Stop the move operation."""
        self.stopped = True
        self.pause_event.set()  # Unblock if paused
        self.log("⏹ Stopping...")

    def toggle_pause(self):
        """Toggle pause/resume state."""
        if not self.running:
            return

        if self.paused:
            self.paused = False
            self.pause_event.set()
            self.pause_btn.configure(text="Pause")
            self.status_label.configure(text="Running...", foreground="green")
            self.log("▶ Resumed")
        else:
            self.paused = True
            self.pause_event.clear()
            self.pause_btn.configure(text="Resume")
            self.status_label.configure(text="Paused", foreground="orange")
            self.log("⏸ Paused")

    def _move_worker(self, source_dir, dest_dir):
        """Background worker that moves CSV files one at a time."""
        self.stopped = False

        if not os.path.isdir(source_dir):
            self.log(f"ERROR: Source directory does not exist:\n  {source_dir}")
            self._finish()
            return

        # Create destination folder if it doesn't exist
        os.makedirs(dest_dir, exist_ok=True)
        self.log(f"Source: {source_dir}")
        self.log(f"Destination: {dest_dir}")

        csv_files = get_sorted_csv_files(source_dir)
        total = len(csv_files)

        if total == 0:
            self.log("No CSV files to move.")
            self._finish()
            return

        self.log(f"Found {total} CSV files to move.")
        self.root.after(0, self.progress_bar.configure, {"maximum": total})

        delay = self.delay_var.get()

        for i, filename in enumerate(csv_files, start=1):
            # Check if stopped
            if self.stopped:
                self.log(f"⏹ Stopped at {i-1}/{total} files.")
                break

            # Wait if paused
            self.pause_event.wait()

            if self.stopped:
                self.log(f"⏹ Stopped at {i-1}/{total} files.")
                break

            src_path = os.path.join(source_dir, filename)
            dst_path = os.path.join(dest_dir, filename)

            if not os.path.exists(src_path):
                self.log(f"[{i}/{total}] SKIP (missing): {filename}")
            elif os.path.exists(dst_path):
                self.log(f"[{i}/{total}] SKIP (already exists): {filename}")
            else:
                try:
                    shutil.copy2(src_path, dst_path)
                    self.log(f"[{i}/{total}] COPIED: {filename}")
                except Exception as e:
                    self.log(f"[{i}/{total}] ERROR: {filename} — {e}")

            # Update progress
            self.root.after(0, self._update_progress, i, total)

            # Delay between moves
            if i < total and not self.stopped:
                self._interruptible_sleep(delay)

        if not self.stopped:
            self.log("✓ Done. All CSV files moved to destination.")
        self._finish()

    def _interruptible_sleep(self, seconds):
        """Sleep in small increments so pause/stop takes effect quickly."""
        elapsed = 0.0
        increment = 0.1
        while elapsed < seconds:
            if self.stopped:
                return
            self.pause_event.wait()
            if self.stopped:
                return
            import time
            time.sleep(increment)
            elapsed += increment

    def _update_progress(self, current, total):
        self.progress_var.set(current)
        self.progress_label.configure(text=f"{current} / {total}")

    def _finish(self):
        """Reset UI state when done."""
        self.running = False
        self.root.after(0, self._reset_ui)

    def _reset_ui(self):
        self.start_btn.configure(state=tk.NORMAL)
        self.pause_btn.configure(state=tk.DISABLED, text="Pause")
        self.stop_btn.configure(state=tk.DISABLED)
        self.status_label.configure(text="Done", foreground="gray")


if __name__ == "__main__":
    root = tk.Tk()
    app = MoveEtestDataApp(root)
    root.mainloop()
