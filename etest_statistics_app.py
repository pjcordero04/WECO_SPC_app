"""
ETEST Statistics App — View distribution and statistical data for WIRE/4WIRE measurements.

Features:
  - Load a folder of ETEST CSV files
  - Compute stats per wire pair: count, mean, min, max, std dev, Cpk
  - Toggle between Scatter Plot and Histogram
  - Scatter plot: X=Cable Number, Y=Value, with mean/sigma/limit lines
  - Histogram: value distribution with normal curve overlay and limit line
"""

import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import pandas as pd
import numpy as np
import os

import matplotlib
matplotlib.use("TkAgg")
import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
from scipy import stats as scipy_stats

from etest_csv_parser import load_folder


class EtestStatsApp:
    def __init__(self, root):
        self.root = root
        self.root.title("ETEST Statistics Viewer")
        self.root.geometry("1100x750")

        self.df = pd.DataFrame()
        self.folder_path = tk.StringVar()
        self.selected_param = tk.StringVar()
        self.plot_type = tk.StringVar(value="scatter")

        self.create_widgets()

    def create_widgets(self):
        # --- Top frame: folder selection ---
        top_frame = tk.Frame(self.root, padx=10, pady=5)
        top_frame.pack(fill="x")

        tk.Label(top_frame, text="Data Folder:").pack(side="left")
        tk.Entry(top_frame, textvariable=self.folder_path, width=60).pack(side="left", padx=(5, 5))
        tk.Button(top_frame, text="Browse...", command=self.browse_folder).pack(side="left")
        tk.Button(top_frame, text="Load Data", command=self.load_data, bg="#4CAF50", fg="white").pack(side="left", padx=(10, 0))
        self.download_btn = tk.Button(top_frame, text="Download CSV", command=self.download_csv,
                                      bg="#2196F3", fg="white", state="disabled")
        self.download_btn.pack(side="left", padx=(10, 0))

        # --- Control frame: parameter selection + plot type ---
        ctrl_frame = tk.Frame(self.root, padx=10, pady=5)
        ctrl_frame.pack(fill="x")

        tk.Label(ctrl_frame, text="Parameter:").pack(side="left")
        self.param_combo = ttk.Combobox(ctrl_frame, textvariable=self.selected_param, width=30, state="readonly")
        self.param_combo.pack(side="left", padx=(5, 20))
        self.param_combo.bind("<<ComboboxSelected>>", lambda e: self.update_plot())

        tk.Label(ctrl_frame, text="Plot Type:").pack(side="left")
        tk.Radiobutton(ctrl_frame, text="Scatter Plot", variable=self.plot_type, value="scatter",
                       command=self.update_plot).pack(side="left")
        tk.Radiobutton(ctrl_frame, text="Histogram", variable=self.plot_type, value="histogram",
                       command=self.update_plot).pack(side="left")

        # --- Main content: stats panel + plot ---
        content_frame = tk.Frame(self.root, padx=10, pady=5)
        content_frame.pack(fill="both", expand=True)

        # Stats panel (left side)
        stats_frame = tk.LabelFrame(content_frame, text="Statistics", padx=10, pady=5, width=250)
        stats_frame.pack(side="left", fill="y", padx=(0, 10))
        stats_frame.pack_propagate(False)

        self.stats_text = tk.Text(stats_frame, width=28, height=20, font=("Consolas", 10),
                                  state="disabled", wrap="word")
        self.stats_text.pack(fill="both", expand=True)

        # Plot panel (right side)
        plot_frame = tk.Frame(content_frame)
        plot_frame.pack(side="left", fill="both", expand=True)

        self.fig, self.ax = plt.subplots(figsize=(8, 5))
        self.fig.tight_layout(pad=3)
        self.canvas = FigureCanvasTkAgg(self.fig, master=plot_frame)
        self.canvas.get_tk_widget().pack(fill="both", expand=True)

        toolbar_frame = tk.Frame(plot_frame)
        toolbar_frame.pack(fill="x")
        self.toolbar = NavigationToolbar2Tk(self.canvas, toolbar_frame)
        self.toolbar.update()

        # Status bar
        self.status_var = tk.StringVar(value="Select a folder and click Load Data.")
        tk.Label(self.root, textvariable=self.status_var, anchor="w", relief="sunken",
                 padx=5).pack(fill="x", side="bottom")

    def browse_folder(self):
        path = filedialog.askdirectory(title="Select ETEST Data Folder")
        if path:
            self.folder_path.set(path)

    def load_data(self):
        folder = self.folder_path.get().strip()
        if not folder or not os.path.isdir(folder):
            messagebox.showerror("Error", "Please select a valid folder.")
            return

        self.status_var.set("Loading data...")
        self.root.update_idletasks()

        self.df = load_folder(folder, include_failed=False)

        if self.df.empty:
            messagebox.showwarning("No Data", "No valid measurement data found in the selected folder.")
            self.status_var.set("No data loaded.")
            return

        # Populate parameter dropdown
        params = sorted(self.df['Parameter'].unique())
        self.param_combo['values'] = params
        if params:
            self.selected_param.set(params[0])

        n_files = self.df['File'].nunique()
        n_params = len(params)
        self.status_var.set(f"Loaded {len(self.df)} measurements from {n_files} files ({n_params} parameters)")

        self.download_btn.config(state="normal")
        self.update_plot()

    def download_csv(self):
        """Export statistical summary of all parameters to CSV in wide format."""
        if self.df.empty:
            messagebox.showwarning("No Data", "Load data first before downloading.")
            return

        # Compute stats for every parameter
        params = sorted(self.df['Parameter'].unique())
        stats_rows = []
        for param in params:
            subset = self.df[self.df['Parameter'] == param]
            values = subset['Value'].dropna()
            if len(values) == 0:
                continue

            upper_limit = subset['UpperLimit'].iloc[0] if not pd.isna(subset['UpperLimit'].iloc[0]) else None
            unit = subset['Unit'].iloc[0]
            mean_val = values.mean()
            std_val = values.std()
            cpk = (upper_limit - mean_val) / (3 * std_val) if (upper_limit and std_val > 0) else None

            lower_limit = subset['LowerLimit'].iloc[0] if 'LowerLimit' in subset.columns and not pd.isna(subset['LowerLimit'].iloc[0]) else None
            cpk_lower = (mean_val - lower_limit) / (3 * std_val) if (lower_limit is not None and std_val > 0) else None

            stats_rows.append({
                'Parameter': param,
                'Unit': unit,
                'Count': len(values),
                'Mean': round(mean_val, 4),
                'Std Dev': round(std_val, 4),
                'Min': round(values.min(), 4),
                'Max': round(values.max(), 4),
                'Range': round(values.max() - values.min(), 4),
                'Lower Limit': lower_limit,
                'Upper Limit': upper_limit,
                'Cpk (lower)': round(cpk_lower, 3) if cpk_lower is not None else None,
                'Cpk (upper)': round(cpk, 3) if cpk is not None else None,
            })

        summary_df = pd.DataFrame(stats_rows)

        # Pivot to wide format: one row per statistic, one column per parameter
        wide_df = summary_df.set_index('Parameter').T

        # Ask user where to save
        save_path = filedialog.asksaveasfilename(
            title="Save Statistics CSV",
            defaultextension=".csv",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
            initialfile="etest_statistics_summary.csv"
        )
        if not save_path:
            return

        try:
            wide_df.to_csv(save_path, encoding='utf-8')
            self.status_var.set(f"Statistics saved to: {save_path}")
            messagebox.showinfo("Saved", f"Statistics exported to:\n{save_path}")
        except Exception as e:
            messagebox.showerror("Error", f"Failed to save CSV:\n{e}")

    def get_param_data(self):
        """Get the data for the currently selected parameter."""
        param = self.selected_param.get()
        if not param or self.df.empty:
            return None
        return self.df[self.df['Parameter'] == param].copy()

    def compute_stats(self, subset):
        """Compute statistics for a parameter subset."""
        values = subset['Value'].dropna()
        if len(values) == 0:
            return {}

        upper_limit = subset['UpperLimit'].iloc[0] if not pd.isna(subset['UpperLimit'].iloc[0]) else None
        unit = subset['Unit'].iloc[0]

        mean_val = values.mean()
        std_val = values.std()

        stats = {
            'Count': len(values),
            'Mean': mean_val,
            'Std Dev': std_val,
            'Min': values.min(),
            'Max': values.max(),
            'Range': values.max() - values.min(),
            'Upper Limit': upper_limit,
            'Unit': unit,
        }

        # Cpk (one-sided, upper only): (USL - mean) / (3σ)
        if upper_limit is not None and std_val > 0:
            stats['Cpk (upper)'] = (upper_limit - mean_val) / (3 * std_val)

        return stats

    def update_stats_panel(self, stats):
        """Update the statistics text display."""
        self.stats_text.config(state="normal")
        self.stats_text.delete("1.0", "end")

        if not stats:
            self.stats_text.insert("end", "No data available.")
            self.stats_text.config(state="disabled")
            return

        unit = stats.get('Unit', '')
        lines = [
            f"Parameter:\n  {self.selected_param.get()}\n",
            f"Count:     {stats['Count']}",
            f"Mean:      {stats['Mean']:.4f} {unit}",
            f"Std Dev:   {stats['Std Dev']:.4f} {unit}",
            f"Min:       {stats['Min']:.4f} {unit}",
            f"Max:       {stats['Max']:.4f} {unit}",
            f"Range:     {stats['Range']:.4f} {unit}",
            f"",
            f"Upper Limit: {stats.get('Upper Limit', 'N/A')} {unit}",
        ]

        if 'Cpk (upper)' in stats:
            cpk = stats['Cpk (upper)']
            lines.append(f"Cpk (upper): {cpk:.3f}")
            if cpk >= 1.33:
                lines.append(f"  → Capable ✓")
            elif cpk >= 1.0:
                lines.append(f"  → Marginal ⚠")
            else:
                lines.append(f"  → Not Capable ✗")

        self.stats_text.insert("end", "\n".join(lines))
        self.stats_text.config(state="disabled")

    def update_plot(self):
        """Redraw the plot based on current selections."""
        subset = self.get_param_data()
        if subset is None or subset.empty:
            return

        stats = self.compute_stats(subset)
        self.update_stats_panel(stats)

        self.ax.clear()

        if self.plot_type.get() == "scatter":
            self.draw_scatter(subset, stats)
        else:
            self.draw_histogram(subset, stats)

        self.fig.tight_layout(pad=3)
        self.canvas.draw()

    def draw_scatter(self, subset, stats):
        """Draw scatter plot: X=sequence, Y=value with control lines."""
        values = subset['Value'].values
        x = range(1, len(values) + 1)

        unit = stats.get('Unit', '')
        mean_val = stats['Mean']
        std_val = stats['Std Dev']
        upper_limit = stats.get('Upper Limit')

        # Data points
        self.ax.scatter(x, values, color='#2196F3', s=12, alpha=0.7, zorder=5, label='Measured')

        # Mean line
        self.ax.axhline(mean_val, color='#FF9800', linewidth=1.5, linestyle='-', label=f'Mean ({mean_val:.3f})')

        # Sigma bands
        if std_val > 0:
            for mult, color, style, lbl in [
                (1, '#FFD700', ':', '±1σ'),
                (2, '#FF6F00', '--', '±2σ'),
                (3, '#F44336', '--', '±3σ'),
            ]:
                self.ax.axhline(mean_val + mult * std_val, color=color, linewidth=1, linestyle=style,
                                alpha=0.8, label=f'+{mult}σ ({mean_val + mult * std_val:.3f})')
                self.ax.axhline(mean_val - mult * std_val, color=color, linewidth=1, linestyle=style,
                                alpha=0.8, label=f'-{mult}σ ({mean_val - mult * std_val:.3f})')

        # Upper spec limit
        if upper_limit is not None:
            self.ax.axhline(upper_limit, color='red', linewidth=2, linestyle='-.',
                            label=f'Upper Limit ({upper_limit} {unit})')

        self.ax.set_xlabel("Cable Sequence")
        self.ax.set_ylabel(f"Value ({unit})")
        self.ax.set_title(f"Scatter Plot — {self.selected_param.get()}")
        self.ax.legend(loc='upper right', fontsize=7, ncol=2)
        self.ax.grid(True, alpha=0.3)

    def draw_histogram(self, subset, stats):
        """Draw histogram with normal curve overlay."""
        values = subset['Value'].dropna().values
        unit = stats.get('Unit', '')
        mean_val = stats['Mean']
        std_val = stats['Std Dev']
        upper_limit = stats.get('Upper Limit')

        # Histogram
        n_bins = min(50, max(10, int(np.sqrt(len(values)))))
        n, bins, patches = self.ax.hist(values, bins=n_bins, color='#2196F3', alpha=0.7,
                                         edgecolor='white', density=True, label='Distribution')

        # Normal curve overlay
        if std_val > 0:
            x_range = np.linspace(values.min() - std_val, values.max() + std_val, 200)
            normal_curve = scipy_stats.norm.pdf(x_range, mean_val, std_val)
            self.ax.plot(x_range, normal_curve, color='#FF9800', linewidth=2, label='Normal fit')

        # Mean line
        self.ax.axvline(mean_val, color='#FF9800', linewidth=1.5, linestyle='-',
                        label=f'Mean ({mean_val:.3f})')

        # Upper spec limit
        if upper_limit is not None:
            self.ax.axvline(upper_limit, color='red', linewidth=2, linestyle='-.',
                            label=f'Upper Limit ({upper_limit} {unit})')

        # Sigma lines
        if std_val > 0:
            for mult, color, style in [(1, '#FFD700', ':'), (2, '#FF6F00', '--'), (3, '#F44336', '--')]:
                self.ax.axvline(mean_val + mult * std_val, color=color, linewidth=1, linestyle=style, alpha=0.8)
                self.ax.axvline(mean_val - mult * std_val, color=color, linewidth=1, linestyle=style, alpha=0.8)

        self.ax.set_xlabel(f"Value ({unit})")
        self.ax.set_ylabel("Density")
        self.ax.set_title(f"Histogram — {self.selected_param.get()}")
        self.ax.legend(loc='upper right', fontsize=8)
        self.ax.grid(True, alpha=0.3)


def main():
    root = tk.Tk()
    app = EtestStatsApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
