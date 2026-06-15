"""
Move subfolders from 'move data 1by1' to 'C:\\TESTDATA\\<part_number>' one at a time.

GUI version with Pause/Resume button and part number entry. Subfolders are moved in
chronological order (oldest timestamp first) based on the YYYYMMDD-HHMMSS pattern
in the folder name.
"""

import os
import re
import shutil
import threading
import tkinter as tk
from tkinter import ttk

SOURCE_DIR = r"C:\TESTDATA\move data 1by1"
DEST_BASE = r"C:\TESTDATA"

DELAY_SECONDS = 2


def extract_timestamp(folder_name):
    """Extract YYYYMMDD-HHMMSS from the folder name (3rd-4th segments split by '-')."""
    m = re.search(r"(\d{8})-(\d{6})", folder_name)
    if m:
        return m.group(1) + m.group(2)
    return ""


def get_sorted_subfolders(source_dir):
    """Get all subfolders sorted by timestamp (oldest first)."""
    subfolders = [
        entry.name for entry in os.scandir(source_dir)
        if entry.is_dir()
    ]
    subfolders.sort(key=lambda name: extract_timestamp(name))
    return subfolders


class MoveDataApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Move Data — Folder Mover")
        self.root.geometry("700x500")
        self.root.resizable(True, True)

        self.paused = False
        self.running = False
        self.pause_event = threading.Event()
        self.pause_event.set()  # Not paused initially

        self._build_ui()

    def _build_ui(self):
        # --- Part number entry ---
        part_frame = ttk.LabelFrame(self.root, text="Part Number", padding=10)
        part_frame.pack(fill=tk.X, padx=10, pady=(10, 5))

        ttk.Label(part_frame, text="Enter part number:").pack(side=tk.LEFT)
        self.part_number_var = tk.StringVar()
        self.part_entry = ttk.Entry(part_frame, textvariable=self.part_number_var, width=30)
        self.part_entry.pack(side=tk.LEFT, padx=(10, 0))

        # --- Info frame ---
        info_frame = ttk.LabelFrame(self.root, text="Configuration", padding=10)
        info_frame.pack(fill=tk.X, padx=10, pady=(0, 5))

        ttk.Label(info_frame, text=f"Source: {SOURCE_DIR}", wraplength=650).pack(anchor=tk.W)
        self.dest_label = ttk.Label(info_frame, text=f"Destination: {DEST_BASE}\\<part_number>", wraplength=650)
        self.dest_label.pack(anchor=tk.W)
        ttk.Label(info_frame, text=f"Delay between moves: {DELAY_SECONDS}s").pack(anchor=tk.W)

        # Update destination label when part number changes
        self.part_number_var.trace_add("write", self._update_dest_label)

        # --- Controls frame ---
        ctrl_frame = ttk.Frame(self.root, padding=10)
        ctrl_frame.pack(fill=tk.X, padx=10)

        self.start_btn = ttk.Button(ctrl_frame, text="Start", command=self.start_moving)
        self.start_btn.pack(side=tk.LEFT, padx=(0, 5))

        self.pause_btn = ttk.Button(ctrl_frame, text="Pause", command=self.toggle_pause, state=tk.DISABLED)
        self.pause_btn.pack(side=tk.LEFT, padx=(0, 5))

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

    def _update_dest_label(self, *args):
        pn = self.part_number_var.get().strip()
        if pn:
            self.dest_label.configure(text=f"Destination: {DEST_BASE}\\{pn}")
        else:
            self.dest_label.configure(text=f"Destination: {DEST_BASE}\\<part_number>")

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

        part_number = self.part_number_var.get().strip()
        if not part_number:
            self.log("ERROR: Please enter a part number.")
            return

        self.running = True
        self.paused = False
        self.pause_event.set()
        self.start_btn.configure(state=tk.DISABLED)
        self.pause_btn.configure(state=tk.NORMAL)
        self.part_entry.configure(state=tk.DISABLED)
        self.status_label.configure(text="Running...", foreground="green")

        thread = threading.Thread(target=self._move_worker, args=(part_number,), daemon=True)
        thread.start()

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

    def _move_worker(self, part_number):
        """Background worker that moves folders one at a time."""
        dest_dir = os.path.join(DEST_BASE, part_number)

        if not os.path.isdir(SOURCE_DIR):
            self.log(f"ERROR: Source directory does not exist:\n  {SOURCE_DIR}")
            self._finish()
            return

        # Create destination folder if it doesn't exist
        os.makedirs(dest_dir, exist_ok=True)
        self.log(f"Destination: {dest_dir}")

        subfolders = get_sorted_subfolders(SOURCE_DIR)
        total = len(subfolders)

        if total == 0:
            self.log("No subfolders to move.")
            self._finish()
            return

        self.log(f"Found {total} subfolders to move.")
        self.root.after(0, self.progress_bar.configure, {"maximum": total})

        for i, folder_name in enumerate(subfolders, start=1):
            # Wait if paused
            self.pause_event.wait()

            src_path = os.path.join(SOURCE_DIR, folder_name)
            dst_path = os.path.join(dest_dir, folder_name)

            if os.path.exists(dst_path):
                self.log(f"[{i}/{total}] SKIP (already exists): {folder_name}")
            else:
                shutil.move(src_path, dst_path)
                self.log(f"[{i}/{total}] MOVED: {folder_name}")

            # Update progress
            self.root.after(0, self._update_progress, i, total)

            # Delay between moves (interruptible by pause check)
            if i < total:
                self._interruptible_sleep(DELAY_SECONDS)

        self.log("Done. All subfolders moved.")
        self._finish()

    def _interruptible_sleep(self, seconds):
        """Sleep in small increments so pause takes effect quickly."""
        elapsed = 0.0
        increment = 0.1
        while elapsed < seconds:
            self.pause_event.wait()
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
        self.part_entry.configure(state=tk.NORMAL)
        self.status_label.configure(text="Done", foreground="gray")


if __name__ == "__main__":
    root = tk.Tk()
    app = MoveDataApp(root)
    root.mainloop()
