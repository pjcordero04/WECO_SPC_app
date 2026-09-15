"""
ETEST WECO SPC App — Real-time Statistical Process Control for electrical test (WIRE/4WIRE) data.

Monitors a folder of ETEST CSV files and applies WECO rules to detect process deviations
in wire resistance measurements.

WECO Rules:
  1. One point is more than 3 standard deviations from the centerline.
  2. Eight or more consecutive points are on the same side of the centerline.
  3. Two out of three consecutive points are more than 2σ from the centerline (same side).
  4. Four out of five consecutive points are more than 1σ from the centerline (same side).

Features:
  - Real-time polling (1 second default) for new CSV files
  - Per wire-pair SPC evaluation (WIRE and 4WIRE independently)
  - Full-screen flashing red alarm banner
  - SPC chart saved to disk on violation
  - CSV violation log
"""

import matplotlib
matplotlib.use("Agg")  # Non-interactive backend — no plot windows
import matplotlib.pyplot as plt
from datetime import datetime
import os
import re
import tkinter as tk
from tkinter import filedialog, messagebox
import pandas as pd
import numpy as np
import csv

from etest_csv_parser import parse_etest_csv, load_folder


# --- SPC Output directory (subfolder per test name) ---
SPC_OUTPUT_BASE = r"C:\TESTDATA\ETEST_SPC"


# ═══════════════════════════════════════════════════════════════════════════════
# WECO Rule Evaluation Functions (stateless, reusable)
# ═══════════════════════════════════════════════════════════════════════════════

RULE_TRIGGER_LOOKBACK = {1: 1, 2: 8, 3: 3, 4: 5}


def compute_centerline_and_sigma(results, window, mode, fixed_mean=None, fixed_std=None):
    """
    Compute centerline and standard deviation line for a results Series.

    Parameters
    ----------
    results : pd.Series of numeric values
    window : int — rolling window size
    mode : str — 'moving_average', 'grand_mean', or 'fixed_limit'
    fixed_mean : float, optional — pre-computed mean (required for 'fixed_limit')
    fixed_std : float, optional — pre-computed std dev (required for 'fixed_limit')

    Returns
    -------
    (centerline: pd.Series, std_line: pd.Series)
    """
    results = pd.to_numeric(results, errors="coerce").dropna().reset_index(drop=True)
    if mode == "moving_average":
        centerline = results.rolling(window=window).mean()
        std_line = results.rolling(window=window).std()
    elif mode == "fixed_limit":
        centerline = pd.Series([fixed_mean] * len(results))
        std_line = pd.Series([fixed_std] * len(results))
    else:
        # Grand Mean mode
        mean = results.mean()
        std = results.std()
        centerline = pd.Series([mean] * len(results))
        std_line = pd.Series([std] * len(results))
    return centerline, std_line


def evaluate_spc_rules(results, window, rules_enabled, mode):
    """
    Evaluate all 4 WECO rules on a results Series.

    Parameters
    ----------
    results : pd.Series of numeric values
    window : int — baseline window size
    rules_enabled : list of 4 booleans
    mode : str — 'moving_average' or 'grand_mean'

    Returns
    -------
    (violated: list[str], violating_indices: list[int])
    """
    results = pd.to_numeric(results, errors="coerce").dropna().reset_index(drop=True)
    n = len(results)

    if n < window:
        return [], []

    centerline, std_line = compute_centerline_and_sigma(results, window, mode)

    violated = []
    violating_indices = []

    # Moving Average: skip first (window) points where rolling stats are NaN
    # Grand Mean: stats valid from point 0, evaluate everything
    start_idx = window if mode == "moving_average" else 0
    to_check = results.iloc[start_idx:]
    cl_check = centerline.iloc[start_idx:]
    std_check = std_line.iloc[start_idx:]

    valid = (~cl_check.isna()) & (~std_check.isna()) & (std_check > 0)

    upper_3s = cl_check + 3 * std_check
    lower_3s = cl_check - 3 * std_check
    upper_2s = cl_check + 2 * std_check
    lower_2s = cl_check - 2 * std_check
    upper_1s = cl_check + 1 * std_check
    lower_1s = cl_check - 1 * std_check

    # WECO Rule 1: One point beyond 3σ
    if rules_enabled[0]:
        beyond_3s = valid & ((to_check > upper_3s) | (to_check < lower_3s))
        if beyond_3s.any():
            violated.append("WECO Rule 1: One point beyond 3σ from centerline")
            violating_indices.extend([start_idx + i for i, v in enumerate(beyond_3s) if v])

    # WECO Rule 2: 8+ consecutive points on the same side
    if rules_enabled[1]:
        above = valid & (to_check > cl_check)
        below = valid & (to_check < cl_check)
        run_above = 0
        run_below = 0
        hit = False
        for i in range(len(to_check)):
            if not valid.iloc[i]:
                run_above = 0
                run_below = 0
                continue
            if above.iloc[i]:
                run_above += 1
                run_below = 0
            elif below.iloc[i]:
                run_below += 1
                run_above = 0
            else:
                run_above = 0
                run_below = 0

            if run_above >= 8:
                hit = True
                violating_indices.extend(range(start_idx + i - 7, start_idx + i + 1))
            elif run_below >= 8:
                hit = True
                violating_indices.extend(range(start_idx + i - 7, start_idx + i + 1))
        if hit:
            violated.append("WECO Rule 2: 8+ consecutive points on same side of centerline")

    # WECO Rule 3: 2 out of 3 consecutive points beyond 2σ (same side)
    if rules_enabled[2]:
        beyond_2s_upper = valid & (to_check > upper_2s)
        beyond_2s_lower = valid & (to_check < lower_2s)
        hit = False
        for j in range(len(to_check) - 2):
            if beyond_2s_upper.iloc[j:j+3].sum() >= 2:
                hit = True
                violating_indices.extend(range(start_idx + j, start_idx + j + 3))
            elif beyond_2s_lower.iloc[j:j+3].sum() >= 2:
                hit = True
                violating_indices.extend(range(start_idx + j, start_idx + j + 3))
        if hit:
            violated.append("WECO Rule 3: 2/3 consecutive points beyond 2σ (same side)")

    # WECO Rule 4: 4 out of 5 consecutive points beyond 1σ (same side)
    if rules_enabled[3]:
        beyond_1s_upper = valid & (to_check > upper_1s)
        beyond_1s_lower = valid & (to_check < lower_1s)
        hit = False
        for j in range(len(to_check) - 4):
            if beyond_1s_upper.iloc[j:j+5].sum() >= 4:
                hit = True
                violating_indices.extend(range(start_idx + j, start_idx + j + 5))
            elif beyond_1s_lower.iloc[j:j+5].sum() >= 4:
                hit = True
                violating_indices.extend(range(start_idx + j, start_idx + j + 5))
        if hit:
            violated.append("WECO Rule 4: 4/5 consecutive points beyond 1σ (same side)")

    return violated, sorted(set(violating_indices))


def evaluate_latest_rule_triggers(results, centerline, std_line, window, rules_enabled, mode="moving_average"):
    """
    Check if the LATEST point participates in any WECO rule violation.

    Returns (violated_rules: list[str], violating_indices: list[int])
    Only fires if the latest data point is part of the violating pattern.

    For Moving Average: rules evaluate only on points AFTER the window (index >= window),
    because the rolling centerline/std are NaN before that.

    For Grand Mean: centerline/std are valid for all points, so start_idx=0 once we
    have enough total data (the caller's min-data guard handles that).
    """
    results = pd.to_numeric(results, errors="coerce").dropna().reset_index(drop=True)
    n = len(results)
    latest_idx = n - 1

    if n < 2:
        return [], []

    violated = []
    violating_indices = []

    # Moving Average: rolling stats are NaN for the first (window-1) points
    # Grand Mean: stats are valid from point 0 (all data used for mean/std)
    start_idx = window if mode == "moving_average" else 0
    if latest_idx < start_idx:
        return [], []

    cl = centerline.iloc[latest_idx] if latest_idx < len(centerline) else None
    sd = std_line.iloc[latest_idx] if latest_idx < len(std_line) else None

    if cl is None or sd is None or pd.isna(cl) or pd.isna(sd) or sd <= 0:
        return [], []

    val = results.iloc[latest_idx]

    # Rule 1: latest point beyond 3σ
    if rules_enabled[0]:
        if val > cl + 3 * sd or val < cl - 3 * sd:
            violated.append("WECO Rule 1: One point beyond 3σ from centerline")
            violating_indices.append(latest_idx)

    # Rule 2: latest extends a run of 8+ same side
    if rules_enabled[1]:
        lookback = min(8, latest_idx - start_idx + 1)
        if lookback >= 8:
            segment = results.iloc[latest_idx - 7:latest_idx + 1]
            cl_seg = centerline.iloc[latest_idx - 7:latest_idx + 1]
            above = (segment > cl_seg).all()
            below = (segment < cl_seg).all()
            if above or below:
                violated.append("WECO Rule 2: 8+ consecutive points on same side of centerline")
                violating_indices.extend(range(latest_idx - 7, latest_idx + 1))

    # Rule 3: latest is part of 2-of-3 beyond 2σ (same side)
    if rules_enabled[2]:
        if latest_idx >= start_idx + 2:
            seg = results.iloc[latest_idx - 2:latest_idx + 1]
            cl_seg = centerline.iloc[latest_idx - 2:latest_idx + 1]
            sd_seg = std_line.iloc[latest_idx - 2:latest_idx + 1]
            upper_2s = cl_seg + 2 * sd_seg
            lower_2s = cl_seg - 2 * sd_seg
            above_2s = (seg > upper_2s).sum() >= 2
            below_2s = (seg < lower_2s).sum() >= 2
            if above_2s or below_2s:
                violated.append("WECO Rule 3: 2/3 consecutive points beyond 2σ (same side)")
                violating_indices.extend(range(latest_idx - 2, latest_idx + 1))

    # Rule 4: latest is part of 4-of-5 beyond 1σ (same side)
    if rules_enabled[3]:
        if latest_idx >= start_idx + 4:
            seg = results.iloc[latest_idx - 4:latest_idx + 1]
            cl_seg = centerline.iloc[latest_idx - 4:latest_idx + 1]
            sd_seg = std_line.iloc[latest_idx - 4:latest_idx + 1]
            upper_1s = cl_seg + 1 * sd_seg
            lower_1s = cl_seg - 1 * sd_seg
            above_1s = (seg > upper_1s).sum() >= 4
            below_1s = (seg < lower_1s).sum() >= 4
            if above_1s or below_1s:
                violated.append("WECO Rule 4: 4/5 consecutive points beyond 1σ (same side)")
                violating_indices.extend(range(latest_idx - 4, latest_idx + 1))

    return violated, sorted(set(violating_indices))


# ═══════════════════════════════════════════════════════════════════════════════
# Utility Functions
# ═══════════════════════════════════════════════════════════════════════════════

def parse_summary_csv(file_path):
    """
    Parse an ETEST summary CSV file to extract Mean and Std Dev per parameter.

    The CSV has rows as statistics (Mean, Std Dev, etc.) and columns as parameter names.
    First column is the statistic label (index).

    Returns
    -------
    dict : {parameter_name: {"mean": float, "std": float}}
           Parameters with invalid (None/NaN) values are omitted.
    """
    df = pd.read_csv(file_path, index_col=0)
    limits = {}
    for param in df.columns:
        mean_val = df.loc["Mean", param] if "Mean" in df.index else None
        std_val = df.loc["Std Dev", param] if "Std Dev" in df.index else None
        try:
            mean_f = float(mean_val)
            std_f = float(std_val)
        except (ValueError, TypeError):
            continue
        if pd.isna(mean_f) or pd.isna(std_f):
            continue
        limits[param] = {"mean": mean_f, "std": std_f}
    return limits


def sanitize_filename_component(value):
    """Return a Windows-safe filename fragment."""
    text = str(value) if value is not None else ""
    text = re.sub(r'[<>:"/\\|?*]', '_', text).strip().rstrip('.')
    return text or "UNKNOWN"


def log_violation_to_csv(cable_number, sn, parameter, rule_label, output_dir):
    """Append a violation record to the CSV log."""
    os.makedirs(output_dir, exist_ok=True)
    csv_path = os.path.join(output_dir, "etest_spc_violations.csv")
    is_new = not os.path.exists(csv_path)
    try:
        with open(csv_path, 'a', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            if is_new:
                writer.writerow(["Cable Number", "S/N", "Parameter", "Rule", "Timestamp"])
            writer.writerow([cable_number, sn, parameter, rule_label,
                             datetime.now().strftime('%Y-%m-%d %H:%M:%S')])
    except PermissionError:
        print(f"Warning: Cannot write to {csv_path} (file is open in another program)")


# ═══════════════════════════════════════════════════════════════════════════════
# Main Application Class
# ═══════════════════════════════════════════════════════════════════════════════

class EtestSPCApp:
    def __init__(self, root):
        self.root = root
        self.root.title("ETEST WECO SPC Control")
        self.rules = [tk.BooleanVar(value=True) for _ in range(4)]
        self.window_size = tk.IntVar(value=30)
        self.centerline_mode = tk.StringVar(value="moving_average")
        self.exclude_failed = tk.BooleanVar(value=True)

        self.folder_path = tk.StringVar()
        self.fixed_limit_path = tk.StringVar()
        self.fixed_limits = {}  # {parameter_name: {"mean": float, "std": float}}
        self.violated_text = tk.StringVar()

        # Polling state
        self.poll_interval_ms = 1000
        self.poll_job = None
        self.started = False
        self.paused = False
        self.seen_files = {}  # path -> (mtime, size)
        self.history_data = []  # list of parsed file dicts (ordered)
        self.last_violations = {}

        # Track which (parameter, rule) combos have already alarmed — don't re-trigger
        # until new data arrives AND the latest point is still violating.
        self.alarmed_param_rules = {}  # param -> set of rule descriptions
        self.data_count_at_alarm = {}  # param -> number of data points when alarm fired

        # Banner state
        self.banner_window = None
        self.banner_visible = False
        self.banner_flash_job = None

        # Parameter selection
        self.available_parameters = []
        self.selected_parameters = set()
        self.param_selector_window = None

        self.create_widgets()

    def create_widgets(self):
        self.root.columnconfigure(0, weight=1)

        row = 0
        tk.Label(self.root, text="ETEST WECO SPC Rules", font=("Arial", 16, "bold")).grid(
            row=row, column=0, columnspan=2, pady=(10, 5))
        row += 1

        # Rule checkboxes
        rule_descriptions = [
            "Rule 1 – One point is more than 3σ from the centerline",
            "Rule 2 – Eight+ consecutive points on the same side of the centerline",
            "Rule 3 – Two out of three consecutive points are more than 2σ (same side)",
            "Rule 4 – Four out of five consecutive points are more than 1σ (same side)",
        ]
        for i, desc in enumerate(rule_descriptions):
            tk.Checkbutton(self.root, text=desc, variable=self.rules[i]).grid(
                row=row, column=0, columnspan=2, sticky="w", padx=20)
            row += 1

        # Window size
        window_frame = tk.Frame(self.root)
        window_frame.grid(row=row, column=0, columnspan=2, sticky="w", padx=20, pady=(10, 0))
        tk.Label(window_frame, text="SPC Window Size:").pack(side="left")
        self.window_size_entry = tk.Entry(window_frame, textvariable=self.window_size, width=6)
        self.window_size_entry.pack(side="left", padx=(10, 0))
        row += 1

        # Centerline mode
        mode_frame = tk.LabelFrame(self.root, text="Centerline Mode")
        mode_frame.grid(row=row, column=0, columnspan=2, sticky="w", padx=20, pady=(10, 0))
        tk.Radiobutton(mode_frame, text="Moving Average", variable=self.centerline_mode,
                       value="moving_average").pack(side="left", padx=5)
        tk.Radiobutton(mode_frame, text="Grand Mean", variable=self.centerline_mode,
                       value="grand_mean").pack(side="left", padx=5)
        tk.Radiobutton(mode_frame, text="Fixed Limit", variable=self.centerline_mode,
                       value="fixed_limit").pack(side="left", padx=5)
        row += 1

        # Fixed limit file selection
        self.fixed_limit_frame = tk.Frame(self.root)
        self.fixed_limit_frame.grid(row=row, column=0, columnspan=2, sticky="w", padx=20, pady=(5, 0))
        tk.Label(self.fixed_limit_frame, text="Limit File:").pack(side="left")
        self.fixed_limit_entry = tk.Entry(self.fixed_limit_frame, textvariable=self.fixed_limit_path, width=45)
        self.fixed_limit_entry.pack(side="left", padx=(5, 5))
        self.fixed_limit_browse_btn = tk.Button(self.fixed_limit_frame, text="Browse...",
                                                 command=self.browse_fixed_limit_file)
        self.fixed_limit_browse_btn.pack(side="left")
        self.fixed_limit_status = tk.Label(self.fixed_limit_frame, text="", fg="green", font=("Arial", 8))
        self.fixed_limit_status.pack(side="left", padx=(10, 0))
        row += 1

        # Exclude failed
        self.exclude_failed_cb = tk.Checkbutton(self.root, text="Exclude Failed Units from Calculations",
                       variable=self.exclude_failed)
        self.exclude_failed_cb.grid(
            row=row, column=0, columnspan=2, sticky="w", padx=20, pady=(5, 0))
        row += 1

        # Enable/disable widgets based on mode (must be after all widgets are created)
        self.centerline_mode.trace_add("write", self._on_mode_change)
        self._on_mode_change()  # Initial state

        # Folder selection
        folder_frame = tk.Frame(self.root)
        folder_frame.grid(row=row, column=0, columnspan=2, sticky="w", padx=20, pady=(10, 0))
        tk.Label(folder_frame, text="Data Folder:").pack(side="left")
        tk.Entry(folder_frame, textvariable=self.folder_path, width=50).pack(side="left", padx=(5, 5))
        tk.Button(folder_frame, text="Browse...", command=self.browse_folder).pack(side="left")
        row += 1

        # Parameter Filter button
        self.param_filter_btn = tk.Button(self.root, text="PARAMETER FILTER",
                                          command=self.open_parameter_selector,
                                          bg="#2196F3", fg="white", width=20)
        self.param_filter_btn.grid(row=row, column=0, columnspan=2, pady=(10, 5))
        row += 1

        # START / PAUSE button (toggles between start, pause, resume)
        self.start_btn = tk.Button(self.root, text="START", command=self.toggle_start_pause,
                                   bg="#4CAF50", fg="white", width=20, font=("Arial", 12, "bold"))
        self.start_btn.grid(row=row, column=0, columnspan=2, pady=5)
        row += 1

        # DONE TESTING button
        self.done_btn = tk.Button(self.root, text="DONE TESTING", command=self.done_testing,
                                  bg="#f44336", fg="white", width=20, font=("Arial", 12, "bold"),
                                  state="disabled")
        self.done_btn.grid(row=row, column=0, columnspan=2, pady=5)
        row += 1

        # Status label
        tk.Label(self.root, textvariable=self.violated_text, font=("Arial", 10),
                 wraplength=500, justify="center", fg="blue").grid(
            row=row, column=0, columnspan=2, pady=(10, 10))

    def _on_mode_change(self, *args):
        """Enable/disable widgets based on centerline mode."""
        is_fixed = self.centerline_mode.get() == "fixed_limit"
        # Fixed Limit: enable limit file, disable window size and exclude-failed
        self.fixed_limit_entry.config(state="normal" if is_fixed else "disabled")
        self.fixed_limit_browse_btn.config(state="normal" if is_fixed else "disabled")
        self.window_size_entry.config(state="disabled" if is_fixed else "normal")
        self.exclude_failed_cb.config(state="disabled" if is_fixed else "normal")

    def browse_fixed_limit_file(self):
        """Open a file dialog to select a summary CSV for fixed limits."""
        path = filedialog.askopenfilename(
            title="Select ETEST Summary CSV",
            filetypes=[("CSV Files", "*.csv"), ("All Files", "*.*")]
        )
        if path:
            self.fixed_limit_path.set(path)
            try:
                self.fixed_limits = parse_summary_csv(path)
                count = len(self.fixed_limits)
                self.fixed_limit_status.config(
                    text=f"✓ {count} parameters loaded", fg="green")
                # Populate the parameter filter list from the loaded CSV
                self.available_parameters = sorted(self.fixed_limits.keys())
                # Only default to all selected if the user hasn't set a filter yet
                if not self.selected_parameters:
                    self.selected_parameters = set(self.available_parameters)
            except Exception as e:
                self.fixed_limits = {}
                self.fixed_limit_status.config(
                    text=f"✗ Error: {e}", fg="red")

    def browse_folder(self):
        path = filedialog.askdirectory(title="Select ETEST Data Folder")
        if path:
            self.folder_path.set(path)
            # Scan the folder immediately to discover parameters for the filter
            self._scan_folder_for_parameters(path)

    def _scan_folder_for_parameters(self, folder):
        """Scan all CSV files in the folder to discover available parameters."""
        discovered = set()
        try:
            for fname in os.listdir(folder):
                if fname.lower().endswith('.csv'):
                    fpath = os.path.join(folder, fname)
                    if os.path.isfile(fpath):
                        parsed = parse_etest_csv(fpath)
                        for m in parsed['measurements']:
                            if m['value'] is not None:
                                discovered.add(m['parameter'])
        except OSError:
            pass
        if discovered:
            self.available_parameters = sorted(discovered)
            # Only reset selection if no selection has been made yet
            if not self.selected_parameters:
                self.selected_parameters = set(self.available_parameters)

    # ─── Alarm Banner ─────────────────────────────────────────────────────────

    def show_banner(self, cable_num, sn, param, violated_rules):
        """Show a full-screen flashing red alarm banner."""
        if self.banner_window:
            self.banner_window.destroy()

        self.banner_visible = True
        self.banner_window = tk.Toplevel(self.root)
        self.banner_window.overrideredirect(True)
        self.banner_window.attributes("-topmost", True)

        screen_w = self.root.winfo_screenwidth()
        banner_h = 200
        self.banner_window.geometry(f"{screen_w}x{banner_h}+0+0")
        self.banner_window.configure(bg="red")

        # Content — compact top banner layout
        tk.Label(self.banner_window, text="⚠ SPC VIOLATION DETECTED ⚠",
                 font=("Arial", 18, "bold"), bg="red", fg="white").pack(pady=(10, 5))

        info_text = f"Cable #{cable_num}  (S/N: {sn})  —  Parameter: {param}"
        tk.Label(self.banner_window, text=info_text,
                 font=("Arial", 12), bg="red", fg="white").pack(pady=2)

        rules_text = " | ".join(violated_rules)
        tk.Label(self.banner_window, text=rules_text,
                 font=("Arial", 10), bg="red", fg="yellow").pack(pady=2)

        tk.Button(self.banner_window, text="ACKNOWLEDGE",
                  font=("Arial", 12, "bold"), bg="white", fg="red",
                  command=self.acknowledge_violation,
                  width=15).pack(pady=(10, 5))

        self.flash_banner()

    def flash_banner(self):
        """Toggle banner background between red and dark red."""
        if not self.banner_window or not self.banner_visible:
            return
        current = self.banner_window.cget("bg")
        new_color = "#8B0000" if current == "red" else "red"
        self.banner_window.configure(bg=new_color)
        for widget in self.banner_window.winfo_children():
            if isinstance(widget, tk.Label):
                widget.configure(bg=new_color)
        self.banner_flash_job = self.root.after(500, self.flash_banner)

    def acknowledge_violation(self):
        """Dismiss the alarm banner and resume monitoring."""
        self.banner_visible = False
        if self.banner_flash_job:
            self.root.after_cancel(self.banner_flash_job)
            self.banner_flash_job = None
        if self.banner_window:
            self.banner_window.destroy()
            self.banner_window = None
        self.schedule_next_scan()

    # ─── Analysis Control ─────────────────────────────────────────────────────

    def toggle_start_pause(self):
        """Toggle between START, PAUSE, and RESUME states."""
        if not self.started:
            # Currently stopped → START
            self.start_analysis()
        elif self.started and not self.paused:
            # Currently running → PAUSE
            self.pause_analysis()
        else:
            # Currently paused → RESUME
            self.resume_analysis()

    def start_analysis(self):
        """Begin SPC monitoring."""
        folder = self.folder_path.get().strip()
        if not folder or not os.path.isdir(folder):
            messagebox.showerror("Error", "Please select a valid data folder.")
            return

        # Validate fixed limit mode has a loaded file
        if self.centerline_mode.get() == "fixed_limit" and not self.fixed_limits:
            messagebox.showerror("Error",
                                 "Fixed Limit mode requires a summary CSV file.\n"
                                 "Please browse and select a valid limit file.")
            return

        self.started = True
        self.paused = False
        self.start_btn.config(text="PAUSE", bg="#FF9800")  # Orange for pause
        self.done_btn.config(state="normal")
        self.param_filter_btn.config(state="disabled")
        self.violated_text.set("Monitoring started. Waiting for data...")

        # Initial load of existing data
        self.load_all_data()

        # Run initial SPC evaluation on the loaded data
        self.run_spc(initial=True)

        # Start polling for new files
        self.schedule_next_scan()

    def pause_analysis(self):
        """Pause monitoring — keeps all data/state, allows changing parameter filter."""
        self.paused = True
        if self.poll_job:
            self.root.after_cancel(self.poll_job)
            self.poll_job = None

        self.start_btn.config(text="RESUME", bg="#2196F3")  # Blue for resume
        self.param_filter_btn.config(state="normal")
        self.violated_text.set("Monitoring PAUSED. You can change the parameter filter.")

    def resume_analysis(self):
        """Resume monitoring after a pause."""
        self.paused = False
        self.start_btn.config(text="PAUSE", bg="#FF9800")  # Orange for pause
        self.param_filter_btn.config(state="disabled")
        self.violated_text.set("Monitoring resumed.")

        # Resume polling
        self.schedule_next_scan()

    def done_testing(self):
        """Stop monitoring and reset."""
        self.started = False
        self.paused = False
        if self.poll_job:
            self.root.after_cancel(self.poll_job)
            self.poll_job = None
        if self.banner_flash_job:
            self.root.after_cancel(self.banner_flash_job)
            self.banner_flash_job = None
        if self.banner_window:
            self.banner_window.destroy()
            self.banner_window = None
        self.banner_visible = False

        self.seen_files.clear()
        self.history_data.clear()
        self.last_violations.clear()
        self.alarmed_param_rules.clear()
        self.data_count_at_alarm.clear()
        self.start_btn.config(text="START", bg="#4CAF50")
        self.done_btn.config(state="disabled")
        self.param_filter_btn.config(state="normal")
        self.violated_text.set("")

    def schedule_next_scan(self):
        """Schedule the next polling cycle."""
        if self.started and not self.banner_visible and not self.paused:
            self.poll_job = self.root.after(self.poll_interval_ms, self.monitor_spc)

    def monitor_spc(self):
        """Poll callback — check for new files and run SPC."""
        if not self.started or self.banner_visible or self.paused:
            return
        self.run_spc()
        self.schedule_next_scan()

    # ─── Data Loading ─────────────────────────────────────────────────────────

    def load_all_data(self):
        """Load all existing CSV files in the folder."""
        folder = self.folder_path.get().strip()
        if not folder:
            return

        csv_files = sorted([
            f for f in os.listdir(folder)
            if f.lower().endswith('.csv') and os.path.isfile(os.path.join(folder, f))
        ], key=lambda f: os.path.getmtime(os.path.join(folder, f)))

        self.history_data.clear()
        self.seen_files.clear()

        for fname in csv_files:
            fpath = os.path.join(folder, fname)
            stat = os.stat(fpath)
            self.seen_files[fpath] = (stat.st_mtime_ns, stat.st_size)

            parsed = parse_etest_csv(fpath)
            if self.exclude_failed.get() and parsed['final_result'].lower() == 'failed':
                continue
            if parsed['measurements']:
                self.history_data.append(parsed)

        # Auto-discover parameters (adds to available list, never resets user's selection)
        all_params = set()
        for parsed in self.history_data:
            for m in parsed['measurements']:
                if m['value'] is not None:
                    all_params.add(m['parameter'])

        self.available_parameters = sorted(all_params)

    def check_for_new_files(self):
        """Check if any new CSV files appeared. Returns list of new file paths."""
        folder = self.folder_path.get().strip()
        if not folder:
            return []

        new_files = []
        try:
            for fname in os.listdir(folder):
                if not fname.lower().endswith('.csv'):
                    continue
                fpath = os.path.join(folder, fname)
                if not os.path.isfile(fpath):
                    continue
                stat = os.stat(fpath)
                key = (stat.st_mtime_ns, stat.st_size)
                if fpath not in self.seen_files or self.seen_files[fpath] != key:
                    new_files.append(fpath)
                    self.seen_files[fpath] = key
        except OSError:
            pass

        return sorted(new_files, key=lambda p: os.path.getmtime(p))

    # ─── SPC Evaluation ───────────────────────────────────────────────────────

    def run_spc(self, initial=False):
        """Main SPC evaluation loop."""
        new_files = self.check_for_new_files()

        if not new_files and self.history_data and not initial:
            # No new data (skip unless this is the initial evaluation)
            return

        # Parse new files and add to history
        for fpath in new_files:
            parsed = parse_etest_csv(fpath)
            if self.exclude_failed.get() and parsed['final_result'].lower() == 'failed':
                continue
            if parsed['measurements']:
                self.history_data.append(parsed)
                # Update available parameters (but don't override user's filter)
                for m in parsed['measurements']:
                    if m['value'] is not None and m['parameter'] not in self.available_parameters:
                        self.available_parameters.append(m['parameter'])
                        self.available_parameters.sort()

        if not self.history_data:
            self.violated_text.set("Waiting for data...")
            return

        window = self.window_size.get()
        mode = self.centerline_mode.get()
        rules_enabled = [r.get() for r in self.rules]

        # Build per-parameter series
        for param in self.selected_parameters:
            values = []
            cable_numbers = []
            sns = []

            for parsed in self.history_data:
                for m in parsed['measurements']:
                    if m['parameter'] == param and m['value'] is not None:
                        values.append(m['value'])
                        cable_numbers.append(parsed['cable_number'])
                        sns.append(parsed['sn'])
                        break  # one value per file per parameter

            # Fixed Limit: evaluate from the 1st point (mean/std are pre-defined)
            # Other modes: need at least 'window' data points for baseline
            if mode == "fixed_limit":
                if len(values) < 1:
                    continue
                # Skip parameters not present in the loaded summary CSV
                if param not in self.fixed_limits:
                    continue
            else:
                if len(values) < window:
                    continue

            results = pd.Series(values, dtype=float)

            # Pass fixed mean/std when in fixed_limit mode
            fixed_mean = self.fixed_limits[param]["mean"] if mode == "fixed_limit" else None
            fixed_std = self.fixed_limits[param]["std"] if mode == "fixed_limit" else None
            centerline, std_line = compute_centerline_and_sigma(
                results, window, mode, fixed_mean=fixed_mean, fixed_std=fixed_std
            )

            # Evaluate if the latest point triggers any rule
            violated, violating_indices = evaluate_latest_rule_triggers(
                results, centerline, std_line, window, rules_enabled, mode
            )

            if violated and len(violating_indices) > 0:
                # --- Suppress re-trigger: only alarm if this is a NEW violation ---
                # A violation is "new" if:
                #   (a) This param+rule combo hasn't triggered before, OR
                #   (b) New data has arrived since the last alarm for this param
                current_count = len(values)
                prev_alarmed_rules = self.alarmed_param_rules.get(param, set())
                prev_count = self.data_count_at_alarm.get(param, 0)

                new_rules = set(violated) - prev_alarmed_rules
                has_new_data = current_count > prev_count

                if not new_rules and not has_new_data:
                    # Same rules, no new data — suppress (already alarmed on this)
                    continue

                latest_cable = cable_numbers[-1] if cable_numbers else "?"
                latest_sn = sns[-1] if sns else "UNKNOWN"

                self.violated_text.set(f"{param}\n" + "\n".join(violated))

                # Update alarm tracking
                self.alarmed_param_rules[param] = set(violated)
                self.data_count_at_alarm[param] = current_count

                # Log violations (only log newly triggered rules)
                rules_to_log = new_rules if new_rules else set(violated)
                output_dir = os.path.join(SPC_OUTPUT_BASE, sanitize_filename_component(
                    self.history_data[0]['test_name'] if self.history_data else "ETEST"))
                for rule_desc in rules_to_log:
                    rule_num = re.search(r"Rule (\d+)", rule_desc)
                    rule_label = f"Rule {rule_num.group(1)}" if rule_num else rule_desc
                    log_violation_to_csv(latest_cable, latest_sn, param, rule_label, output_dir)

                # Save SPC plot (wrapped so a plot error never blocks the alarm)
                try:
                    self.save_spc_plot(results, centerline, std_line, param, violating_indices,
                                       cable_numbers, latest_cable, latest_sn, violated)
                except Exception as e:
                    print(f"Error generating SPC plot: {e}")

                # Show alarm
                self.show_banner(latest_cable, latest_sn, param, violated)
                return  # Stop after first violation (process one at a time)
            else:
                # Violation cleared — reset tracking so it can re-alarm if it returns
                if param in self.alarmed_param_rules:
                    del self.alarmed_param_rules[param]
                if param in self.data_count_at_alarm:
                    del self.data_count_at_alarm[param]

        self.violated_text.set("No SPC rule violated.")

    # ─── SPC Plot Generation ──────────────────────────────────────────────────

    def save_spc_plot(self, results, centerline, std_line, param, violating_indices,
                      cable_numbers, latest_cable, latest_sn, violated):
        """Generate and save the SPC chart to disk."""
        window = self.window_size.get()

        plt.close('all')
        fig, ax = plt.subplots(figsize=(12, 5))

        x = range(len(results))

        # Normal points
        normal_mask = [i not in violating_indices for i in range(len(results))]
        ax.plot(x, results, marker='o', markersize=3, linestyle='None', color='blue',
                label='Measured', zorder=3)

        # Violating points — get the latest failure value for the legend
        failure_value = None
        for idx in violating_indices:
            if idx < len(results):
                ax.plot(idx, results.iloc[idx], marker='o', markersize=3, color='red', zorder=5)
                failure_value = results.iloc[idx]
        # Add SPC Failure legend entry (with measured value)
        if failure_value is not None:
            ax.plot([], [], marker='o', markersize=3, linestyle='None', color='red',
                    label=f'SPC Failure ({failure_value:.4f})')

        # Centerline
        mode_label = self.centerline_mode.get().replace('_', ' ').title()
        # Get the mean value for annotation (use last valid centerline value)
        cl_val = centerline.dropna().iloc[-1] if not centerline.dropna().empty else None
        std_val = std_line.dropna().iloc[-1] if not std_line.dropna().empty else None

        cl_label = f'Centerline ({mode_label}) X̄={cl_val:.4f}' if cl_val is not None else f'Centerline ({mode_label})'
        ax.plot(x, centerline, color='orange', linewidth=1.5, label=cl_label)

        # Sigma bands
        valid = (~centerline.isna()) & (~std_line.isna())
        upper_3s = centerline + 3 * std_line
        lower_3s = centerline - 3 * std_line
        upper_2s = centerline + 2 * std_line
        lower_2s = centerline - 2 * std_line
        upper_1s = centerline + 1 * std_line
        lower_1s = centerline - 1 * std_line

        # Compute display values for sigma annotations
        u3 = upper_3s.dropna().iloc[-1] if not upper_3s.dropna().empty else None
        l3 = lower_3s.dropna().iloc[-1] if not lower_3s.dropna().empty else None
        u2 = upper_2s.dropna().iloc[-1] if not upper_2s.dropna().empty else None
        l2 = lower_2s.dropna().iloc[-1] if not lower_2s.dropna().empty else None
        u1 = upper_1s.dropna().iloc[-1] if not upper_1s.dropna().empty else None
        l1 = lower_1s.dropna().iloc[-1] if not lower_1s.dropna().empty else None

        ax.plot(x, upper_3s, color='red', linewidth=1, linestyle='--',
                label=f'+3σ = {u3:.4f}' if u3 is not None else '+3σ')
        ax.plot(x, lower_3s, color='red', linewidth=1, linestyle='--',
                label=f'-3σ = {l3:.4f}' if l3 is not None else '-3σ')
        ax.plot(x, upper_2s, color='darkorange', linewidth=0.8, linestyle=':',
                label=f'+2σ = {u2:.4f}' if u2 is not None else '+2σ')
        ax.plot(x, lower_2s, color='darkorange', linewidth=0.8, linestyle=':',
                label=f'-2σ = {l2:.4f}' if l2 is not None else '-2σ')
        ax.plot(x, upper_1s, color='gold', linewidth=0.8, linestyle=':',
                label=f'+1σ = {u1:.4f}' if u1 is not None else '+1σ')
        ax.plot(x, lower_1s, color='gold', linewidth=0.8, linestyle=':',
                label=f'-1σ = {l1:.4f}' if l1 is not None else '-1σ')

        # Upper spec limit (from data)
        for parsed in self.history_data:
            for m in parsed['measurements']:
                if m['parameter'] == param and m['upper_limit'] is not None:
                    ax.axhline(m['upper_limit'], color='purple', linewidth=1.5, linestyle='-.',
                               label=f"Upper Limit ({m['upper_limit']} {m['limit_unit']})")
                    break
            break

        ax.set_xlabel("Cable Sequence")
        ax.set_ylabel(f"Value")
        ax.set_title(f"ETEST SPC — {param}")
        ax.legend(loc='upper left', fontsize=7, bbox_to_anchor=(1.01, 1))
        ax.grid(True, alpha=0.3)

        fig.suptitle(f"Cable #{latest_cable} (S/N: {latest_sn}) — {', '.join(violated)}",
                     fontsize=9, y=0.98)
        fig.subplots_adjust(top=0.88, right=0.78)

        # Save
        now = datetime.now().strftime('%m-%d-%Y--%H-%M-%S')
        rule_number = "RuleX"
        for i in range(1, 5):
            if f'Rule {i}' in str(violated):
                rule_number = f'Rule{i}'
                break

        sn_safe = sanitize_filename_component(latest_sn)
        cable_safe = sanitize_filename_component(str(latest_cable))
        filename = f"Cable{cable_safe}_SN{sn_safe}-{now}--{rule_number}.png"

        output_dir = os.path.join(SPC_OUTPUT_BASE, sanitize_filename_component(
            self.history_data[0]['test_name'] if self.history_data else "ETEST"))
        os.makedirs(output_dir, exist_ok=True)
        save_path = os.path.join(output_dir, filename)

        try:
            fig.savefig(save_path, dpi=100, bbox_inches='tight')
            print(f"SPC chart saved: {save_path}")
        except Exception as e:
            print(f"Error saving chart: {e}")
        finally:
            plt.close(fig)

    # ─── Parameter Selector ───────────────────────────────────────────────────

    def open_parameter_selector(self):
        """Open a window to select which parameters to monitor."""
        if self.param_selector_window and self.param_selector_window.winfo_exists():
            self.param_selector_window.lift()
            return

        # If no parameters yet, try to discover them from the data folder
        if not self.available_parameters:
            folder = self.folder_path.get().strip()
            if folder and os.path.isdir(folder):
                self._scan_folder_for_parameters(folder)

        self.param_selector_window = tk.Toplevel(self.root)
        self.param_selector_window.title("Parameter Filter")
        self.param_selector_window.geometry("400x500")

        tk.Label(self.param_selector_window, text="Select parameters to monitor:",
                 font=("Arial", 11, "bold")).pack(pady=(10, 5))

        if not self.available_parameters:
            tk.Label(self.param_selector_window,
                     text="No parameters found.\n\n"
                          "• For Fixed Limit: load a summary CSV first.\n"
                          "• For Moving Avg / Grand Mean: select a data folder first.",
                     justify="left").pack(pady=20, padx=20)
            tk.Button(self.param_selector_window, text="Close",
                      command=self.param_selector_window.destroy).pack(pady=10)
            return

        param_vars = {}
        group_vars = {}

        def update_status():
            selected_count = sum(1 for v in param_vars.values() if v.get())
            status_text.set(f"Selected: {selected_count} / {len(self.available_parameters)}")

        def select_all():
            for v in param_vars.values():
                v.set(True)
            for v in group_vars.values():
                v.set(True)
            update_status()

        def deselect_all():
            for v in param_vars.values():
                v.set(False)
            for v in group_vars.values():
                v.set(False)
            update_status()

        # Select All / Deselect All + status
        btn_frame = tk.Frame(self.param_selector_window)
        btn_frame.pack(fill="x", padx=10)
        tk.Button(btn_frame, text="Select All", command=select_all).pack(side="left", padx=5)
        tk.Button(btn_frame, text="Deselect All", command=deselect_all).pack(side="left", padx=5)
        status_text = tk.StringVar()
        tk.Label(btn_frame, textvariable=status_text, fg="#1f6aa5").pack(side="right")

        # --- Bottom buttons FIRST (pack side="bottom" so they stay visible) ---
        bottom_frame = tk.Frame(self.param_selector_window)
        bottom_frame.pack(side="bottom", fill="x", pady=10, padx=10)

        def apply_selection():
            self.selected_parameters = {p for p, v in param_vars.items() if v.get()}
            self.param_selector_window.destroy()

        def cancel_selection():
            self.param_selector_window.destroy()

        tk.Button(bottom_frame, text="APPLY", command=apply_selection,
                  bg="#4CAF50", fg="white", font=("Arial", 10, "bold"),
                  width=12).pack(side="left", padx=(50, 10))
        tk.Button(bottom_frame, text="Cancel", command=cancel_selection,
                  width=12).pack(side="left", padx=10)

        # Intercept the window close (X) button — treat as Cancel (keep previous selection)
        self.param_selector_window.protocol("WM_DELETE_WINDOW", cancel_selection)

        # --- Scrollable checkbox list (fills remaining space) ---
        canvas = tk.Canvas(self.param_selector_window)
        scrollbar = tk.Scrollbar(self.param_selector_window, orient="vertical", command=canvas.yview)
        scroll_frame = tk.Frame(canvas)

        scroll_frame.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=scroll_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)

        scrollbar.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True, padx=10, pady=10)

        # Group parameters by prefix (text before the first "_")
        # e.g. "4WIRE_END1_1_END2_1" → group "4WIRE", "WIRE_END1_S_END2_S" → group "WIRE"
        from collections import OrderedDict
        groups = OrderedDict()
        for p in self.available_parameters:
            prefix = p.split("_")[0] if "_" in p else p
            if prefix not in groups:
                groups[prefix] = []
            groups[prefix].append(p)

        def toggle_group(group_name):
            state = group_vars[group_name].get()
            for p in groups[group_name]:
                param_vars[p].set(state)
            update_status()

        def update_group_var(group_name):
            all_checked = all(param_vars[p].get() for p in groups[group_name])
            group_vars[group_name].set(all_checked)
            update_status()

        for group_name, group_params in groups.items():
            # Group header checkbox (bold)
            all_checked = all(p in self.selected_parameters for p in group_params)
            group_vars[group_name] = tk.BooleanVar(value=all_checked)
            tk.Checkbutton(
                scroll_frame,
                text=f"▸ {group_name}  ({len(group_params)})",
                variable=group_vars[group_name],
                command=lambda g=group_name: toggle_group(g),
                font=("Arial", 10, "bold"),
                fg="#1f6aa5"
            ).pack(anchor="w", pady=(6, 0))

            # Individual parameter checkboxes (indented)
            for p in group_params:
                checked = p in self.selected_parameters
                param_vars[p] = tk.BooleanVar(value=checked)
                tk.Checkbutton(
                    scroll_frame,
                    text=p,
                    variable=param_vars[p],
                    command=lambda g=group_name: update_group_var(g)
                ).pack(anchor="w", padx=(20, 0))

        update_status()


# ═══════════════════════════════════════════════════════════════════════════════
# Main Entry Point
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    root = tk.Tk()
    app = EtestSPCApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
