import os
import queue
import json
import re
import threading
import time
import subprocess
from datetime import datetime
from tkinter import BooleanVar, Menu, filedialog, messagebox

import sys
import customtkinter as ctk
import serial
import serial.tools.list_ports


ctk.set_appearance_mode("system")
ctk.set_default_color_theme("dark-blue")


class BridgeGui(ctk.CTk):
    XON = 17
    XOFF = 19
    MAX_FLASH_BYTES = 524288
    CARD_BORDER = ("#c7d2df", "#2e3742")
    DEFAULT_PORT_BAUD = "19200"
    DEFAULT_RS232_BAUD = "19200"
    PARAM_AUTOSEND_DEBOUNCE_MS = 90
    PARAM_SET_READBACK_DELAY_S = 0.12
    GET_TIMEOUT_DEFAULT = 1.5
    GET_TIMEOUT_MIN = 0.1
    GET_TIMEOUT_MAX = 10.0
    APP_VERSION = "1.0.0"
    APP_CHANNEL = ""
    BOOT_CONNECT_MAX_RETRIES = 3
    BOOT_CONNECT_RETRY_DELAY_S = 0.8
    BOOT_HANDSHAKE_TIMEOUT_S = 8.0

    def __init__(self):
        super().__init__()
        self.title("RS232-KLine Bridge Suite")
        self.geometry("1060x760")
        self.minsize(940, 680)

        self.serial_port = None
        self.reader_thread = None
        self.reader_stop_event = threading.Event()
        self.log_queue = queue.Queue()
        self.bootloader_serial = None
        self.bootloader_version = ""
        self.serial_baud_values = ["10400", "9600", "19200", "38400", "57600", "115200", "230400", "500000", "1000000"]
        self.param_baud_values = list(self.serial_baud_values)
        self.buffer_allowed_values = [16, 32, 64, 128, 256, 512, 1024]
        self.buffer_labels, self.buffer_value_map = self._build_buffer_labels()
        self.fwd_labels = ["0 (aus)", "1 (ein)"]
        self.bootloader_ready = False
        self.log_autoscroll_var = BooleanVar(value=True)
        self.debug_logging_var = BooleanVar(value=True)
        self.debug_logging_enabled = True
        self.bridge_fw_version = "-"
        self.awaiting_version_response = False
        self.app_start_time = time.time()
        self.tx_count = 0
        self.rx_count = 0
        self.warn_count = 0
        self.error_count = 0
        self.last_tx = "-"
        self.last_rx = "-"
        self.stats_value_labels = {}
        self.bridge_stats_labels = {}
        self.ui_mode_map = {"Hell": "Light", "Dunkel": "Dark", "Automatisch": "System"}
        self.terminal_mode_values = ["String", "Character", "Bytes (Hex)"]
        self.config_path = os.path.join(os.path.dirname(__file__), "app_config.json")
        self.selected_ui_mode = "Automatisch"
        self.log_file_path = os.path.join(os.path.dirname(__file__), "debug_log.txt")
        self._init_log_file()
        self.selected_port_baud = self.DEFAULT_PORT_BAUD
        self.selected_rs232_baud = self.DEFAULT_RS232_BAUD
        self.build_info = self._detect_build_info()
        self.active_tab_name = ""

        # Central command registry: keep all bridge/bootloader commands in one place.
        self.commands = self._build_command_registry()

        # Statistics use bridge short codes directly so return values map 1:1 to UI labels.
        self.bridge_stat_request_commands = [
            (self.commands["bridge_get"]["rs232rs"], "rrs"),
            (self.commands["bridge_get"]["rs232ts"], "rts"),
            (self.commands["bridge_get"]["kliners"], "krs"),
            (self.commands["bridge_get"]["klinets"], "kts"),
            (self.commands["bridge_get"]["rs232re"], "rre"),
            (self.commands["bridge_get"]["klinere"], "kre"),
        ]
        self.config_upload_commands = [
            (self.commands["bridge_get"]["rs232rx"], "rs232rx"),
            (self.commands["bridge_get"]["rs232tx"], "rs232tx"),
            (self.commands["bridge_get"]["rs232br"], "rs232br"),
            (self.commands["bridge_get"]["klinerx"], "klinerx"),
            (self.commands["bridge_get"]["klinetx"], "klinetx"),
            (self.commands["bridge_get"]["klinebr"], "klinebr"),
            (self.commands["bridge_get"]["dtr_fwd"], "dtr_fwd"),
            (self.commands["bridge_get"]["buffmax"], "bbm"),
        ]
        self.get_command_timeouts = {
            self.commands["bridge_get"]["version"]: 0.02,
            self.commands["bridge_get"]["rs232rs"]: 0.03,
            self.commands["bridge_get"]["rs232ts"]: 0.03,
            self.commands["bridge_get"]["kliners"]: 0.03,
            self.commands["bridge_get"]["klinets"]: 0.03,
            self.commands["bridge_get"]["rs232re"]: 0.03,
            self.commands["bridge_get"]["klinere"]: 0.03,
            self.commands["bridge_get"]["rs232rx"]: 0.05,
            self.commands["bridge_get"]["rs232tx"]: 0.05,
            self.commands["bridge_get"]["rs232br"]: 0.05,
            self.commands["bridge_get"]["klinerx"]: 0.05,
            self.commands["bridge_get"]["klinetx"]: 0.05,
            self.commands["bridge_get"]["klinebr"]: 0.05,
            self.commands["bridge_get"]["dtr_fwd"]: 0.05,
            self.commands["bridge_get"]["buffmax"]: 0.05,
        }
        self.bridge_stats_values = {key: "-" for _, key in self.bridge_stat_request_commands}
        self.bridge_stat_bit_width = {
            "rre": 8,
            "kre": 8,
        }
        self.uart_error_flags = [
            (0x01, "UART_FRAME_ERROR"),
            (0x02, "UART_OVERRUN_ERROR"),
            (0x04, "UART_BUFFER_OVERFLOW"),
            (0x08, "UART_PARITY_ERROR"),
        ]
        self.awaiting_response_key = None
        self.awaiting_response_event = None
        self.awaiting_response_value = ""
        self.awaiting_response_lock = threading.Lock()
        self.bridge_query_lock = threading.Lock()
        self.suspend_param_autosend = False
        self.param_autosend_jobs = {}
        self.param_confirmed_values = {}
        self.param_refresh_after_id = None
        self.param_refresh_running = False
        self.tooltip_window = None
        self.tooltip_label = None
        self.tooltip_after_id = None
        self.tooltip_pending = None
        self.tooltip_widget = None
        self.is_processing = False
        self.processing_spinner_chars = ['⠋', '⠙', '⠹', '⠸', '⠼', '⠴', '⠦', '⠧', '⠇', '⠏']
        self.processing_spinner_index = 0
        self.processing_animator_id = None
        self.log_boxes = {}
        self.version_timeout_after_id = None
        self.bridge_max_buffer_size = None
        self.buffer_usage_bar = None
        self.buffer_usage_segments = {}
        self.buffer_fill_status_label = None
        self.buffer_segment_specs = [
            ("RS232 RX Buffer", "rs232rx", ("#2563eb", "#3b82f6")),
            ("RS232 TX Buffer", "rs232tx", ("#0f766e", "#14b8a6")),
            ("KLine RX Buffer", "klinerx", ("#ca8a04", "#f59e0b")),
            ("KLine TX Buffer", "klinetx", ("#b91c1c", "#ef4444")),
        ]

        self._load_app_config()

        self._build_ui()
        self._refresh_ports()
        self.after(100, self._drain_log_queue)
        self.after(150, self._watch_active_tab)

    def _build_menu(self):
        menubar = Menu(self)

        file_menu = Menu(menubar, tearoff=0)
        file_menu.add_command(label="Verbinden / Trennen", command=self._toggle_connection)
        file_menu.add_command(label="Ports aktualisieren", command=self._refresh_ports)
        file_menu.add_separator()
        file_menu.add_command(label="Beenden", command=self._on_close)
        menubar.add_cascade(label="Datei", menu=file_menu)

        view_menu = Menu(menubar, tearoff=0)
        view_menu.add_command(label="Log leeren", command=self._clear_log)
        view_menu.add_checkbutton(label="Auto-Scroll Log", variable=self.log_autoscroll_var)
        view_menu.add_checkbutton(
            label="Debug-Ausgaben",
            variable=self.debug_logging_var,
            command=self._on_debug_logging_toggle,
        )
        menubar.add_cascade(label="Ansicht", menu=view_menu)

        tools_menu = Menu(menubar, tearoff=0)
        tools_menu.add_command(label="Bootloader verbinden", command=self._connect_to_bootloader)
        tools_menu.add_command(label="Firmware flashen", command=self._flash_firmware)
        tools_menu.add_command(label="EEPROM flashen", command=self._flash_eeprom)
        tools_menu.add_separator()
        tools_menu.add_command(label="Start Application (Bootloader)", command=self._bootloader_start_application)
        menubar.add_cascade(label="Tools", menu=tools_menu)

        help_menu = Menu(menubar, tearoff=0)
        help_menu.add_command(label="Ueber", command=self._show_about)
        menubar.add_cascade(label="Hilfe", menu=help_menu)

        help_menu.add_separator()
        help_menu.add_command(label="Debug-Log oeffnen", command=self._open_log_file)

        self.configure(menu=menubar)

    def _build_buffer_labels(self):
        labels = []
        value_map = {}
        for value in self.buffer_allowed_values:
            label = f"{value} Bytes"
            labels.append(label)
            value_map[label] = str(value)
        return labels, value_map

    def _build_command_registry(self) -> dict:
        return {
            "bridge_get": {
                "version": "-get ver",
                "rs232rs": "-get rrs",
                "rs232ts": "-get rts",
                "kliners": "-get krs",
                "klinets": "-get kts",
                "rs232re": "-get rre",
                "klinere": "-get kre",
                "rs232rx": "-get rrx",
                "rs232tx": "-get rtx",
                "rs232br": "-get rbr",
                "klinerx": "-get krx",
                "klinetx": "-get ktx",
                "klinebr": "-get kbr",
                "dtr_fwd": "-get fwd",
                "buffmax": "-get bbm",
                "bridgem": "-get brm",
            },
            "bridge_set": {
                "reset": "-set rsb 1",
                "savecfg": "-set scg",
                "kline_high": "-set ksh",
                "kline_low": "-set ksl",
                "kline_pulse_prefix": "-set ksp",
                "rs232rx": "-set rrx",
                "rs232tx": "-set rtx",
                "rs232br": "-set rbr",
                "klinerx": "-set krx",
                "klinetx": "-set ktx",
                "klinebr": "-set kbr",
                "dtr_fwd": "-set fwd",
                "bridgem": "-set brm",
            },
            "bootloader": {
                "handshake": b"U",
                "program_firmware": b"pf\n",
                "program_eeprom": b"pe\n",
                "start_application": b"g\n",
            },
        }

    def _detect_build_info(self) -> str:
        base_version = f"v{self.APP_VERSION}"
        channel = (self.APP_CHANNEL or "").strip().lower()
        if channel:
            base_version = f"{base_version}-{channel}"
        # Timestamp represents build date/time in UTC for reproducible version labels.
        build_stamp = datetime.utcnow().strftime("%Y%m%d-%H%M%SZ")
        repo_dir = os.path.dirname(__file__)
        try:
            short_hash = subprocess.check_output(
                ["git", "rev-parse", "--short", "HEAD"],
                cwd=repo_dir,
                stderr=subprocess.DEVNULL,
                text=True,
            ).strip()
            dirty_state = subprocess.check_output(
                ["git", "status", "--porcelain"],
                cwd=repo_dir,
                stderr=subprocess.DEVNULL,
                text=True,
            ).strip()
            dirty_suffix = ".dirty" if dirty_state else ""
            return f"{base_version}+{build_stamp}.{short_hash}{dirty_suffix}"
        except Exception:
            return f"{base_version}+{build_stamp}"

    def _install_tooltip(self, widget, text: str):
        widget.bind("<Enter>", lambda e, w=widget, t=text: self._schedule_tooltip(w, t, e.x_root, e.y_root), add="+")
        widget.bind("<Leave>", lambda _e: self._clear_tooltip(), add="+")
        widget.bind("<Motion>", lambda e, w=widget: self._update_tooltip_position(w, e.x_root, e.y_root), add="+")

    def _schedule_tooltip(self, widget, text: str, x_root: int, y_root: int):
        self._cancel_tooltip_timer()
        self.tooltip_widget = widget
        self.tooltip_pending = (text, x_root, y_root)
        self.tooltip_after_id = self.after(200, self._show_scheduled_tooltip)

    def _cancel_tooltip_timer(self):
        if self.tooltip_after_id is not None:
            try:
                self.after_cancel(self.tooltip_after_id)
            except Exception:
                pass
            self.tooltip_after_id = None

    def _show_scheduled_tooltip(self):
        self.tooltip_after_id = None
        if not self.tooltip_pending or self.tooltip_widget is None:
            return
        if not self.winfo_exists() or not self.tooltip_widget.winfo_exists():
            return

        text, x_root, y_root = self.tooltip_pending
        if self.tooltip_window is None or not self.tooltip_window.winfo_exists():
            self.tooltip_window = ctk.CTkToplevel(self)
            self.tooltip_window.overrideredirect(True)
            self.tooltip_window.attributes("-topmost", True)
            self.tooltip_label = ctk.CTkLabel(
                self.tooltip_window,
                text=text,
                corner_radius=6,
                fg_color=("#f3f6fa", "#1f2630"),
                text_color=("#111827", "#e6edf3"),
            )
            self.tooltip_label.pack(padx=8, pady=4)
        elif self.tooltip_label is not None:
            self.tooltip_label.configure(text=text)

        self._place_tooltip(x_root, y_root)

    def _place_tooltip(self, x_root: int, y_root: int):
        if self.tooltip_window is None or not self.tooltip_window.winfo_exists():
            return
        self.tooltip_window.geometry(f"+{x_root + 14}+{y_root + 16}")

    def _update_tooltip_position(self, widget, x_root: int, y_root: int):
        if widget is not self.tooltip_widget:
            return
        if self.tooltip_window is not None and self.tooltip_window.winfo_exists():
            self._place_tooltip(x_root, y_root)
        elif self.tooltip_pending is not None:
            text, _, _ = self.tooltip_pending
            self.tooltip_pending = (text, x_root, y_root)

    def _hide_tooltip(self):
        if self.tooltip_window is not None and self.tooltip_window.winfo_exists():
            self.tooltip_window.destroy()
        self.tooltip_window = None
        self.tooltip_label = None

    def _clear_tooltip(self):
        self._cancel_tooltip_timer()
        self.tooltip_pending = None
        self.tooltip_widget = None
        self._hide_tooltip()

    def _set_processing(self, is_processing: bool):
        """Enable or disable the processing indicator."""
        self.is_processing = is_processing
        if is_processing:
            self.processing_spinner_index = 0
            self._animate_processing_spinner()
        else:
            if self.processing_animator_id is not None:
                self.after_cancel(self.processing_animator_id)
                self.processing_animator_id = None
            self.processing_label.configure(text="")

    def _animate_processing_spinner(self):
        """Animate the processing spinner."""
        if not self.is_processing:
            return
        
        char = self.processing_spinner_chars[self.processing_spinner_index]
        self.processing_label.configure(text=char)
        self.processing_spinner_index = (self.processing_spinner_index + 1) % len(self.processing_spinner_chars)
        self.processing_animator_id = self.after(80, self._animate_processing_spinner)

    def _build_ui(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        title_frame = ctk.CTkFrame(self, fg_color="transparent")
        title_frame.grid(row=0, column=0, sticky="ew", padx=12, pady=(12, 4))
        title_frame.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(title_frame, text="RS232-KLine Bridge Suite", font=ctk.CTkFont(size=24, weight="bold")).grid(
            row=0, column=0, sticky="w"
        )
        ctk.CTkLabel(
            title_frame,
            text=f"Build: {self.build_info} | ASiKS-Engineering",
            text_color=("#4b5563", "#9ca3af"),
        ).grid(row=1, column=0, sticky="w", pady=(2, 0))

        header = ctk.CTkFrame(self, corner_radius=12, border_width=1, border_color=self.CARD_BORDER)
        header.grid(row=1, column=0, sticky="ew", padx=12, pady=(4, 8))
        header.grid_columnconfigure(10, weight=1)

        ctk.CTkLabel(header, text="Serial Port").grid(row=0, column=0, padx=(10, 6), pady=10)
        self.port_option = ctk.CTkOptionMenu(header, values=["-"])
        self.port_option.grid(row=0, column=1, padx=6, pady=10)

        refresh_btn = ctk.CTkButton(header, text="Refresh", width=90, command=self._refresh_ports)
        refresh_btn.grid(row=0, column=2, padx=6, pady=10)
        self._install_tooltip(refresh_btn, "Verfuegbare COM-Ports neu einlesen")

        ctk.CTkLabel(header, text="Port Baud").grid(row=0, column=3, padx=(14, 6), pady=10)
        self.baud_combo = ctk.CTkComboBox(header, values=self.serial_baud_values, width=120)
        self.baud_combo.set(self.selected_port_baud)
        self.baud_combo.grid(row=0, column=4, padx=6, pady=10)

        self.connect_btn = ctk.CTkButton(header, text="Connect", width=110, command=self._toggle_connection)
        self.connect_btn.grid(row=0, column=5, padx=(14, 6), pady=10)
        self._install_tooltip(self.connect_btn, "Serielle Verbindung aufbauen oder trennen")

        self.dtr_switch = ctk.CTkSwitch(header, text="DTR aktiv", command=self._toggle_dtr, state="disabled")
        self.dtr_switch.grid(row=0, column=6, padx=(14, 6), pady=10)

        self.dtr_status_bubble = ctk.CTkFrame(
            header,
            width=22,
            height=22,
            corner_radius=11,
            fg_color="#9ca3af",
        )
        self.dtr_status_bubble.grid(row=0, column=7, padx=(4, 10), pady=10)
        self.dtr_status_bubble.grid_propagate(False)

        self.reset_bridge_btn = ctk.CTkButton(
            header,
            text="Reset",
            width=90,
            command=lambda: self._send_bridge_command(self.commands["bridge_set"]["reset"]),
        )
        self.reset_bridge_btn.grid(row=0, column=8, padx=(8, 6), pady=10)
        self._install_tooltip(self.reset_bridge_btn, "Bridge per -set rsb 1 zuruecksetzen")

        self.processing_label = ctk.CTkLabel(header, text="", font=ctk.CTkFont(size=16, weight="bold"), text_color=("#2f81f7", "#2f81f7"), width=20)
        self.processing_label.grid(row=0, column=9, padx=(12, 12), pady=10)

        self.main_tabs = ctk.CTkTabview(
            self,
            corner_radius=14,
            border_width=1,
            border_color=("#c9d1d9", "#2f353d"),
            segmented_button_fg_color=("#e9eef5", "#1d232a"),
            segmented_button_selected_color=("#1f6feb", "#2f81f7"),
            segmented_button_selected_hover_color=("#1a5fd0", "#3a8fff"),
            segmented_button_unselected_color=("#dce3ec", "#2a313a"),
            segmented_button_unselected_hover_color=("#cfd8e3", "#343d47"),
            text_color=("#0b1220", "#e6edf3"),
            anchor="n",
        )
        self.main_tabs.grid(row=2, column=0, sticky="nsew", padx=12, pady=(0, 12))
        self.main_tabs.add("Configuration")
        self.main_tabs.add("Statistics")
        self.main_tabs.add("Terminal")
        self.main_tabs.add("Bootloader")

        self.main_tabs._segmented_button.configure(font=ctk.CTkFont(size=14, weight="bold"), height=34)
        self.active_tab_name = self.main_tabs.get()

        bridge_tab = self.main_tabs.tab("Configuration")
        bridge_tab.grid_columnconfigure(0, weight=1)
        bridge_tab.grid_rowconfigure(2, weight=1)

        stats_tab = self.main_tabs.tab("Statistics")
        stats_tab.grid_columnconfigure(0, weight=1)
        stats_tab.grid_rowconfigure(1, weight=1)

        terminal_tab = self.main_tabs.tab("Terminal")
        terminal_tab.grid_columnconfigure(0, weight=1)
        terminal_tab.grid_rowconfigure(3, weight=1)

        boot_tab = self.main_tabs.tab("Bootloader")
        boot_tab.grid_columnconfigure(0, weight=1)
        boot_tab.grid_rowconfigure(1, weight=1)

        bridge_stats_frame = ctk.CTkFrame(stats_tab, corner_radius=12, border_width=1, border_color=self.CARD_BORDER)
        bridge_stats_frame.grid(row=0, column=0, sticky="ew", pady=(8, 8))
        bridge_stats_frame.grid_columnconfigure((1, 3), weight=1)
        self.stats_refresh_btn = ctk.CTkButton(
            bridge_stats_frame,
            text="↻",
            width=62,
            height=40,
            corner_radius=10,
            font=ctk.CTkFont(size=22, weight="bold"),
            command=self._refresh_bridge_statistics,
        )
        self.stats_refresh_btn.grid(row=0, column=3, padx=(0, 10), pady=(10, 8), sticky="e")
        self._install_tooltip(self.stats_refresh_btn, "Bridge-Statistiken abfragen und aktualisieren")

        self.stats_reset_btn = ctk.CTkButton(
            bridge_stats_frame,
            text="Reset Statistics",
            width=150,
            height=36,
            command=self._reset_runtime_statistics,
        )
        self.stats_reset_btn.grid(row=0, column=2, padx=(0, 10), pady=(10, 8), sticky="e")
        self._install_tooltip(self.stats_reset_btn, "Runtime-Statistiken zuruecksetzen")

        bridge_rows = [
            ("RS232 RX Overflows", "rrs", 1, 0),
            ("KLine RX Overflows", "krs", 1, 2),
            ("RS232 TX Overflows", "rts", 2, 0),
            ("KLine TX Overflows", "kts", 2, 2),
            ("RS232 RX Errors",   "rre", 3, 0),
            ("KLine RX Errors",   "kre", 3, 2),
        ]
        for title, key, row, col in bridge_rows:
            ctk.CTkLabel(bridge_stats_frame, text=title, font=ctk.CTkFont(weight="bold")).grid(
                row=row, column=col, padx=(10, 6), pady=4, sticky="w"
            )
            value_lbl = ctk.CTkLabel(bridge_stats_frame, text="-", font=ctk.CTkFont(family="Consolas", size=13))
            value_lbl.grid(row=row, column=col + 1, padx=(0, 10), pady=4, sticky="w")
            self.bridge_stats_labels[key] = value_lbl

        self.log_boxes["Statistics"] = ctk.CTkTextbox(
            stats_tab, wrap="word", corner_radius=12, border_width=1,
            border_color=self.CARD_BORDER, font=ctk.CTkFont(family="Consolas", size=12),
        )
        self.log_boxes["Statistics"].grid(row=1, column=0, sticky="nsew", pady=(0, 8))

        terminal_ctrl = ctk.CTkFrame(terminal_tab, corner_radius=12, border_width=1, border_color=self.CARD_BORDER)
        terminal_ctrl.grid(row=0, column=0, sticky="ew", pady=(8, 8))
        terminal_ctrl.grid_columnconfigure(3, weight=1)

        ctk.CTkLabel(terminal_ctrl, text="TX Mode", font=ctk.CTkFont(weight="bold")).grid(
            row=0, column=0, padx=(10, 6), pady=10, sticky="w"
        )
        self.terminal_mode_option = ctk.CTkOptionMenu(terminal_ctrl, values=self.terminal_mode_values)
        self.terminal_mode_option.set("String")
        self.terminal_mode_option.grid(row=0, column=1, padx=6, pady=10, sticky="w")

        self.terminal_newline_switch = ctk.CTkSwitch(terminal_ctrl, text="Append newline")
        self.terminal_newline_switch.select()
        self.terminal_newline_switch.grid(row=0, column=2, padx=8, pady=10, sticky="w")

        self.terminal_input_entry = ctk.CTkEntry(terminal_ctrl, placeholder_text="Enter text / character / hex bytes")
        self.terminal_input_entry.grid(row=0, column=3, padx=8, pady=10, sticky="ew")
        self.terminal_input_entry.bind("<Return>", lambda _e: self._send_terminal_payload())

        self.terminal_send_btn = ctk.CTkButton(terminal_ctrl, text="Send", width=90, command=self._send_terminal_payload)
        self.terminal_send_btn.grid(row=0, column=4, padx=(0, 10), pady=10)
        self._install_tooltip(self.terminal_send_btn, "Terminal-Nutzdaten senden")

        ctk.CTkLabel(
            terminal_ctrl,
            text="Hex examples: '01 A0 FF' or '0x01 0xA0 0xFF'",
            text_color=("#5f6b7a", "#95a1b1"),
        ).grid(row=1, column=0, columnspan=5, padx=10, pady=(0, 10), sticky="w")

        kline_ctrl = ctk.CTkFrame(terminal_tab, corner_radius=12, border_width=1, border_color=self.CARD_BORDER)
        kline_ctrl.grid(row=2, column=0, sticky="ew", pady=(0, 8))
        kline_ctrl.grid_columnconfigure(5, weight=1)
        ctk.CTkLabel(kline_ctrl, text="KLine Control", font=ctk.CTkFont(weight="bold")).grid(
            row=0, column=0, padx=(10, 8), pady=10, sticky="w"
        )

        self.kline_high_btn = ctk.CTkButton(
            kline_ctrl,
            text="KLine HIGH",
            width=120,
            command=self._send_kline_high,
        )
        self.kline_high_btn.grid(row=0, column=1, padx=(0, 8), pady=10)
        self._install_tooltip(self.kline_high_btn, "Sendet -set kline_h")

        self.kline_low_btn = ctk.CTkButton(
            kline_ctrl,
            text="KLine LOW",
            width=120,
            command=self._send_kline_low,
        )
        self.kline_low_btn.grid(row=0, column=2, padx=(0, 8), pady=10)
        self._install_tooltip(self.kline_low_btn, "Sendet -set kline_l")

        ctk.CTkLabel(kline_ctrl, text="Pulse (ms)").grid(row=0, column=3, padx=(6, 6), pady=10, sticky="e")
        self.kline_pulse_entry = ctk.CTkEntry(kline_ctrl, width=100, placeholder_text="0..65535")
        self.kline_pulse_entry.grid(row=0, column=4, padx=(0, 8), pady=10)
        self.kline_pulse_entry.bind("<Return>", lambda _e: self._send_kline_pulse())

        self.kline_pulse_btn = ctk.CTkButton(
            kline_ctrl,
            text="Send Pulse",
            width=120,
            command=self._send_kline_pulse,
        )
        self.kline_pulse_btn.grid(row=0, column=5, padx=(0, 10), pady=10, sticky="w")
        self._install_tooltip(self.kline_pulse_btn, "Sendet -set kline_p <ms> (16-bit)")

        self.terminal_rx_box = ctk.CTkTextbox(
            terminal_tab,
            wrap="none",
            corner_radius=12,
            border_width=1,
            border_color=self.CARD_BORDER,
            font=ctk.CTkFont(family="Consolas", size=12),
        )
        self.terminal_rx_box.grid(row=3, column=0, sticky="nsew", pady=(0, 8))
        self.log_boxes["Terminal"] = self.terminal_rx_box

        status_frame = ctk.CTkFrame(self, corner_radius=10, border_width=1, border_color=self.CARD_BORDER)
        status_frame.grid(row=3, column=0, sticky="ew", padx=12, pady=(0, 10))
        status_frame.grid_columnconfigure(2, weight=1)
        ctk.CTkLabel(status_frame, text="Status", font=ctk.CTkFont(weight="bold")).grid(
            row=0, column=0, padx=(10, 8), pady=8, sticky="w"
        )
        ctk.CTkLabel(status_frame, text="Bridge Firmware:").grid(row=0, column=1, padx=(6, 6), pady=8, sticky="w")
        self.bridge_fw_label = ctk.CTkLabel(status_frame, text=self.bridge_fw_version)
        self.bridge_fw_label.grid(row=0, column=2, padx=(0, 10), pady=8, sticky="w")
        ctk.CTkLabel(status_frame, text="Modus:").grid(row=0, column=3, padx=(6, 6), pady=8, sticky="e")
        self.mode_option = ctk.CTkOptionMenu(
            status_frame,
            values=["Hell", "Dunkel", "Automatisch"],
            width=140,
            command=self._on_mode_change,
        )
        self.mode_option.set(self.selected_ui_mode)
        self.mode_option.grid(row=0, column=4, padx=(0, 10), pady=8, sticky="e")

        settings_frame = ctk.CTkFrame(bridge_tab, corner_radius=12, border_width=1, border_color=self.CARD_BORDER)
        settings_frame.grid(row=1, column=0, sticky="ew", pady=(8, 8))
        settings_frame.grid_columnconfigure(0, minsize=190)
        settings_frame.grid_columnconfigure(1, weight=1)
        settings_frame.grid_columnconfigure(2, minsize=190)
        settings_frame.grid_columnconfigure(3, weight=1)
        settings_frame.grid_columnconfigure(4, minsize=70)
        settings_frame.grid_columnconfigure(5, minsize=70)
        ctk.CTkLabel(settings_frame, text="Parameters", font=ctk.CTkFont(size=18, weight="bold")).grid(
            row=0, column=0, padx=(14, 10), pady=(10, 14), sticky="w"
        )

        self.upload_cfg_btn = ctk.CTkButton(
            settings_frame,
            text="⭱",
            width=62,
            height=40,
            corner_radius=10,
            font=ctk.CTkFont(size=22, weight="bold"),
            command=self._upload_bridge_config,
        )
        self.upload_cfg_btn.grid(row=0, column=4, padx=(8, 6), pady=(8, 10), sticky="e")
        self._install_tooltip(self.upload_cfg_btn, "Aktuelle Bridge-Parameter auslesen")

        self.save_cfg_btn = ctk.CTkButton(
            settings_frame,
            text="⭳",
            width=62,
            height=40,
            corner_radius=10,
            font=ctk.CTkFont(size=22, weight="bold"),
            command=lambda: self._send_bridge_command(self.commands["bridge_set"]["savecfg"]),
        )
        self.save_cfg_btn.grid(row=0, column=5, padx=(0, 12), pady=(8, 10), sticky="e")
        self._install_tooltip(self.save_cfg_btn, "Parameter dauerhaft speichern (-set savecfg)")

        self.param_entries = {}
        params = [
            ("RS232 RX Buffer Size", self.commands["bridge_set"]["rs232rx"], "buffer", 1, 0, 1),
            ("RS232 TX Buffer Size", self.commands["bridge_set"]["rs232tx"], "buffer", 2, 0, 1),
            ("RS232 Baud Rate", self.commands["bridge_set"]["rs232br"], "baud", 3, 0, 1),
            ("KLine RX Buffer Size", self.commands["bridge_set"]["klinerx"], "buffer", 1, 2, 3),
            ("KLine TX Buffer Size", self.commands["bridge_set"]["klinetx"], "buffer", 2, 2, 3),
            ("KLine Baud Rate", self.commands["bridge_set"]["klinebr"], "baud", 3, 2, 3),
            ("DTR Forwarding", self.commands["bridge_set"]["dtr_fwd"], "fwd", 4, 1, 2),
        ]

        for title, cmd, control_type, row, label_col, control_col in params:
            rs232_prefix = self.commands["bridge_set"]["rs232rx"].rsplit("rx", maxsplit=1)[0]
            kline_prefix = self.commands["bridge_set"]["klinerx"].rsplit("rx", maxsplit=1)[0]
            dtr_cmd = self.commands["bridge_set"]["dtr_fwd"]
            kline_baud_cmd = self.commands["bridge_set"]["klinebr"]

            pady = (12, 6) if cmd == dtr_cmd else 6
            if cmd.startswith(rs232_prefix):
                label_padx = (12, 6)
                control_padx = (0, 10)
            elif cmd.startswith(kline_prefix):
                label_padx = (12, 6)
                control_padx = (0, 10)
            elif cmd == dtr_cmd:
                label_padx = (8, 2)
                control_padx = (0, 8)
            else:
                label_padx = (8, 6)
                control_padx = (8, 8)

            ctk.CTkLabel(settings_frame, text=title).grid(row=row, column=label_col, padx=label_padx, pady=pady, sticky="w")

            if control_type == "buffer":
                control = ctk.CTkComboBox(settings_frame, values=self.buffer_labels)
                control.set("64 Bytes")
            elif control_type == "baud":
                control = ctk.CTkComboBox(settings_frame, values=self.param_baud_values)
                control.set("10400" if cmd == kline_baud_cmd else self.selected_rs232_baud)
            elif control_type == "fwd":
                control = ctk.CTkComboBox(settings_frame, values=self.fwd_labels)
                control.set(self.fwd_labels[1])
            else:
                control = ctk.CTkEntry(settings_frame)

            control.grid(row=row, column=control_col, padx=control_padx, pady=pady, sticky="ew")
            self.param_entries[cmd] = {"widget": control, "type": control_type}
            self.param_confirmed_values[cmd] = control.get().strip()
            control.configure(command=lambda _v=None, c=cmd: self._on_param_control_changed(c))
            control.bind("<Return>", lambda _e, c=cmd: self._on_param_enter_pressed(c))

        ctk.CTkLabel(settings_frame, text="Buffer Usage", font=ctk.CTkFont(weight="bold")).grid(
            row=5, column=0, padx=(12, 6), pady=(12, 10), sticky="w"
        )
        self.buffer_usage_bar = ctk.CTkFrame(
            settings_frame,
            corner_radius=7,
            border_width=1,
            border_color=self.CARD_BORDER,
            fg_color=("#e5e7eb", "#374151"),
            height=16,
        )
        self.buffer_usage_bar.grid(row=5, column=1, columnspan=3, padx=(0, 10), pady=(12, 10), sticky="ew")
        self.buffer_usage_bar.grid_propagate(False)

        for index, (title, _key, color) in enumerate(self.buffer_segment_specs):
            segment = ctk.CTkFrame(
                self.buffer_usage_bar,
                corner_radius=0,
                fg_color=color,
            )
            segment.place(relx=0, rely=0, relheight=1, relwidth=0)
            self.buffer_usage_segments[title] = segment

            legend = ctk.CTkLabel(
                settings_frame,
                text=title,
                font=ctk.CTkFont(size=11),
                text_color=color,
            )
            legend.grid(row=6, column=index, columnspan=1, padx=(12, 6), pady=(0, 6), sticky="w")

        self.buffer_fill_status_label = ctk.CTkLabel(
            settings_frame,
            text="Used: 0 B / Max: -",
            font=ctk.CTkFont(family="Consolas", size=12),
            text_color=("#4b5563", "#9ca3af"),
        )
        self.buffer_fill_status_label.grid(row=5, column=4, columnspan=2, padx=(0, 12), pady=(12, 10), sticky="e")

        self.log_box = ctk.CTkTextbox(bridge_tab, wrap="word", corner_radius=12, border_width=1, border_color=self.CARD_BORDER)
        self.log_box.grid(row=2, column=0, sticky="nsew", pady=(0, 8))
        self.log_box.configure(font=ctk.CTkFont(family="Consolas", size=12))
        self.log_boxes["Configuration"] = self.log_box

        boot_frame = ctk.CTkFrame(boot_tab, corner_radius=12, border_width=1, border_color=self.CARD_BORDER)
        boot_frame.grid(row=0, column=0, sticky="ew", pady=(8, 8))
        boot_frame.grid_columnconfigure(1, weight=1)
        boot_frame.grid_columnconfigure(3, weight=0)

        ctk.CTkLabel(boot_frame, text="Bootloader", font=ctk.CTkFont(size=17, weight="bold")).grid(
            row=0, column=0, padx=(10, 8), pady=(10, 6), sticky="w"
        )

        self.boot_connect_btn = ctk.CTkButton(
            boot_frame,
            text="Connect to Bootloader",
            width=180,
            command=self._connect_to_bootloader,
        )
        self.boot_connect_btn.grid(row=1, column=0, padx=(10, 8), pady=6, sticky="ns")
        self._install_tooltip(self.boot_connect_btn, "Bridge resetten und in den Bootloader wechseln")

        self.fw_path_entry = ctk.CTkEntry(boot_frame)
        self.fw_path_entry.grid(row=2, column=1, padx=8, pady=6, sticky="ew")
        self.pick_firmware_btn = ctk.CTkButton(boot_frame, text="Firmware...", width=120, command=self._pick_firmware)
        self.pick_firmware_btn.grid(
            row=2, column=2, padx=(8, 10), pady=6
        )
        self._install_tooltip(self.pick_firmware_btn, "Firmware-Datei auswaehlen")
        self.flash_firmware_btn = ctk.CTkButton(boot_frame, text="Flash Firmware", width=140, command=self._flash_firmware)
        self.flash_firmware_btn.grid(row=2, column=0, padx=(10, 8), pady=6)
        self._install_tooltip(self.flash_firmware_btn, "Firmware in den Controller flashen")

        self.eeprom_path_entry = ctk.CTkEntry(boot_frame)
        self.eeprom_path_entry.grid(row=3, column=1, padx=8, pady=6, sticky="ew")
        self.pick_eeprom_btn = ctk.CTkButton(boot_frame, text="EEPROM...", width=120, command=self._pick_eeprom)
        self.pick_eeprom_btn.grid(
            row=3, column=2, padx=(8, 10), pady=6
        )
        self._install_tooltip(self.pick_eeprom_btn, "EEPROM-Datei auswaehlen")
        self.flash_eeprom_btn = ctk.CTkButton(boot_frame, text="Flash EEPROM", width=140, command=self._flash_eeprom)
        self.flash_eeprom_btn.grid(row=3, column=0, padx=(10, 8), pady=6)
        self._install_tooltip(self.flash_eeprom_btn, "EEPROM-Inhalt flashen")

        self.boot_start_app_btn = ctk.CTkButton(boot_frame, text="Start Application", width=140, command=self._bootloader_start_application)
        self.boot_start_app_btn.grid(row=4, column=0, padx=(10, 8), pady=(6, 6))
        self._install_tooltip(self.boot_start_app_btn, "Bootloader verlassen und Applikation starten")

        self.boot_info_label = ctk.CTkLabel(boot_frame, text="Version: -")
        self.boot_info_label.grid(row=4, column=1, padx=8, pady=(6, 6), sticky="w")

        self.boot_progress = ctk.CTkProgressBar(boot_frame)
        self.boot_progress.grid(row=5, column=1, padx=8, pady=(6, 10), sticky="ew")
        self.boot_progress.set(0)

        self.boot_progress_label = ctk.CTkLabel(boot_frame, text="Fortschritt: 0%")
        self.boot_progress_label.grid(row=5, column=2, padx=(8, 10), pady=(6, 10), sticky="w")

        self.log_boxes["Bootloader"] = ctk.CTkTextbox(
            boot_tab, wrap="word", corner_radius=12, border_width=1,
            border_color=self.CARD_BORDER, font=ctk.CTkFont(family="Consolas", size=12),
        )
        self.log_boxes["Bootloader"].grid(row=1, column=0, sticky="nsew", pady=(0, 8))

        self._configure_log_tags()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self._refresh_statistics_display()
        self._update_buffer_fill_indicator()

    def _watch_active_tab(self):
        try:
            current_tab = self.main_tabs.get()
            if current_tab != self.active_tab_name:
                self.active_tab_name = current_tab
                self._on_tab_changed(current_tab)
        except Exception:
            pass
        self.after(150, self._watch_active_tab)

    def _on_tab_changed(self, tab_name: str):
        if tab_name == "Terminal":
            self._disable_dtr_for_terminal()

    def _init_log_file(self):
        """Initialize the debug log file with timestamp."""
        try:
            with open(self.log_file_path, "a", encoding="utf-8") as f:
                f.write(f"\n{'='*80}\n")
                f.write(f"Log started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
                f.write(f"App Version: {self.APP_VERSION}\n")
                f.write(f"{'='*80}\n")
        except Exception as e:
            print(f"Failed to initialize log file: {e}")

    def _write_to_log_file(self, entry: str):
        """Write a single log entry to the debug log file."""
        try:
            with open(self.log_file_path, "a", encoding="utf-8") as f:
                f.write(entry)
        except Exception as e:
            print(f"Failed to write to log file: {e}")

    def _open_log_file(self):
        """Open the debug log file in the default text editor."""
        try:
            if not os.path.exists(self.log_file_path):
                messagebox.showinfo("Log File", f"Log file not found at:\n{self.log_file_path}")
                return
            if os.name == "nt":  # Windows
                os.startfile(self.log_file_path)
            else:  # Linux/Mac
                subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", self.log_file_path])
        except Exception as e:
            messagebox.showerror("Error", f"Failed to open log file: {e}")

    def _disable_dtr_for_terminal(self):
        if not self.serial_port or not self.serial_port.is_open:
            return
        if not bool(self.dtr_switch.get()):
            return
        self.dtr_switch.deselect()
        self._toggle_dtr()

    def _log(self, text: str):
        if text.startswith("DEBUG") and not self.debug_logging_enabled:
            return

        ts = datetime.now().strftime("%H:%M:%S")
        lowered = text.lower()
        if "tx:" in lowered:
            self.tx_count += 1
            self.last_tx = text.replace("TX:", "", 1).strip()[:80] or "-"
        elif "rx:" in lowered:
            self.rx_count += 1
            self.last_rx = text.replace("RX:", "", 1).strip()[:80] or "-"
        if "error" in lowered or "failed" in lowered or "timeout" in lowered:
            self.error_count += 1
        elif "warn" in lowered or "ungueltig" in lowered:
            self.warn_count += 1

        log_entry = f"[{ts}] {text}\n"
        self.log_queue.put((self.active_tab_name, log_entry))
        self._write_to_log_file(log_entry)
        self.after(0, self._refresh_statistics_display)

    def _configure_log_tags(self):
        # Colors are chosen to stay readable on both light and dark system themes.
        for box in self.log_boxes.values():
            box.tag_config("tx", foreground="#1f6feb")
            box.tag_config("rx", foreground="#238636")
            box.tag_config("warn", foreground="#9a6700")
            box.tag_config("error", foreground="#cf222e")
            box.tag_config("info", foreground="#57606a")

    def _log_tag_for_message(self, message: str) -> str:
        lowered = message.lower()
        if "rx:" in lowered:
            return "rx"
        if "tx:" in lowered:
            return "tx"
        if "error" in lowered or "failed" in lowered or "timeout" in lowered:
            return "error"
        if "warn" in lowered or "ungueltig" in lowered:
            return "warn"
        return "info"

    def _drain_log_queue(self):
        while not self.log_queue.empty():
            item = self.log_queue.get_nowait()
            _tab_name, msg = item if isinstance(item, tuple) else ("Configuration", item)
            # Always write to the tab that is currently visible.
            log_box = self.log_boxes.get(self.active_tab_name, self.log_box)
            tag = self._log_tag_for_message(msg)
            log_box.insert("end", msg, tag)
            if self.log_autoscroll_var.get():
                log_box.see("end")
        self.after(100, self._drain_log_queue)

    def _clear_log(self):
        active_box = self.log_boxes.get(self.active_tab_name, self.log_box)
        active_box.delete("1.0", "end")
        self._log("Log wurde geleert.")

    def _reset_runtime_statistics(self):
        self.tx_count = 0
        self.rx_count = 0
        self.warn_count = 0
        self.error_count = 0
        self.last_tx = "-"
        self.last_rx = "-"
        self.app_start_time = time.time()
        self._refresh_statistics_display()
        self._log("Runtime statistics reset.")

    def _on_debug_logging_toggle(self):
        enabled = bool(self.debug_logging_var.get())
        self.debug_logging_enabled = enabled
        self._save_app_config()
        state_text = "aktiv" if enabled else "inaktiv"
        self._log(f"Debug-Ausgaben sind jetzt {state_text}.")

    def _refresh_statistics_display(self):
        uptime_s = int(max(0, time.time() - self.app_start_time))
        hh = uptime_s // 3600
        mm = (uptime_s % 3600) // 60
        ss = uptime_s % 60
        port_text = self.port_option.get().strip() if hasattr(self, "port_option") else "-"
        if not port_text:
            port_text = "-"
        if self.serial_port and self.serial_port.is_open:
            port_state = "bridge connected"
        elif self.bootloader_serial and self.bootloader_serial.is_open:
            port_state = "bootloader mode"
        else:
            port_state = "disconnected"

        boot_state = "bootloader connected" if self.bootloader_ready else "bootloader idle"
        values = {
            "uptime": f"{hh:02d}:{mm:02d}:{ss:02d}",
            "port": port_state,
            "tx": str(self.tx_count),
            "rx": str(self.rx_count),
            "warn": str(self.warn_count),
            "error": str(self.error_count),
            "last_tx": self.last_tx,
            "last_rx": self.last_rx,
            "boot": boot_state,
            "fw": self.bridge_fw_version,
        }
        for key, value in values.items():
            lbl = self.stats_value_labels.get(key)
            if lbl is not None:
                lbl.configure(text=value)

        for key, value in self.bridge_stats_values.items():
            lbl = self.bridge_stats_labels.get(key)
            if lbl is not None:
                lbl.configure(text=value)

    def _refresh_bridge_statistics(self):
        if not self._can_send_bridge_commands():
            return
        self.stats_refresh_btn.configure(state="disabled")
        threading.Thread(target=self._refresh_bridge_statistics_worker, daemon=True).start()

    def _upload_bridge_config(self):
        if not self._can_send_bridge_commands():
            return
        self.upload_cfg_btn.configure(state="disabled")
        threading.Thread(target=self._upload_bridge_config_worker, daemon=True).start()

    def _schedule_param_auto_refresh(self):
        if self.param_refresh_running:
            return
        if self.param_refresh_after_id is not None:
            try:
                self.after_cancel(self.param_refresh_after_id)
            except Exception:
                pass
        # Debounce repeated replies so rapid response bursts trigger only one refresh cycle.
        self.param_refresh_after_id = self.after(220, self._start_param_auto_refresh)

    def _start_param_auto_refresh(self):
        self.param_refresh_after_id = None
        if self.param_refresh_running:
            return
        if not self.serial_port or not self.serial_port.is_open:
            return
        if not bool(self.dtr_switch.get()):
            return
        self.param_refresh_running = True
        self.upload_cfg_btn.configure(state="disabled")
        threading.Thread(target=self._upload_bridge_config_worker, daemon=True).start()

    def _sanitize_timeout(self, value, default: float) -> float:
        try:
            timeout = float(value)
        except Exception:
            return default
        if timeout < self.GET_TIMEOUT_MIN:
            return self.GET_TIMEOUT_MIN
        if timeout > self.GET_TIMEOUT_MAX:
            return self.GET_TIMEOUT_MAX
        return timeout

    def _get_timeout_for_command(self, command: str) -> float:
        normalized = " ".join((command or "").strip().lower().split())
        configured = self.get_command_timeouts.get(normalized, self.GET_TIMEOUT_DEFAULT)
        return self._sanitize_timeout(configured, self.GET_TIMEOUT_DEFAULT)

    def _query_bridge_value(self, command: str, key: str, timeout: float = 1.5):
        timeout = self._sanitize_timeout(timeout, self._get_timeout_for_command(command))
        # Only one in-flight query is supported by the shared awaiting_response state.
        with self.bridge_query_lock:
            event = threading.Event()
            event_id = id(event)
            with self.awaiting_response_lock:
                self.awaiting_response_key = key
                self.awaiting_response_event = event
                self.awaiting_response_value = ""

            self._log(f"DEBUG await start: key='{key}', cmd='{command}', timeout={timeout:.2f}s, event_id={event_id}")

            if not self._write_serial_line(command):
                with self.awaiting_response_lock:
                    self.awaiting_response_key = None
                    self.awaiting_response_event = None
                return False, "ERR(write)"

            if not event.wait(timeout=timeout):
                self._log(f"DEBUG await timeout: key='{key}', cmd='{command}', event_id={event_id}")
                with self.awaiting_response_lock:
                    self.awaiting_response_key = None
                    self.awaiting_response_event = None
                return False, "TIMEOUT"

            with self.awaiting_response_lock:
                value = self.awaiting_response_value
                self.awaiting_response_key = None
                self.awaiting_response_event = None

            self._log(f"DEBUG await done: key='{key}', cmd='{command}', value='{value}', event_id={event_id}")

            return True, value

    def _extract_numeric_value(self, text: str):
        if not text:
            return None
        # Remove hidden control chars from serial payloads (e.g. NUL, STX) before parsing.
        cleaned = "".join(ch for ch in str(text) if ch >= " " or ch == "\t")
        cleaned = cleaned.strip().replace(",", " ").replace(";", " ")

        hex_match = re.search(r"(?i)(?:^|\s)([+-]?0x[0-9a-f]+)(?:$|\s)", cleaned)
        if hex_match:
            try:
                return int(hex_match.group(1), 16)
            except ValueError:
                pass

        dec_match = re.search(r"(?:^|\s)([+-]?\d+)(?:$|\s)", cleaned)
        if dec_match:
            try:
                return int(dec_match.group(1), 10)
            except ValueError:
                pass

        return None

    def _normalize_bridge_stat_value(self, key: str, raw_value: str) -> str:
        numeric = self._extract_numeric_value(raw_value)
        if numeric is None:
            return raw_value

        bit_width = self.bridge_stat_bit_width.get(key)
        if bit_width:
            numeric &= (1 << bit_width) - 1
            if key in {"rre", "kre"}:
                return self._decode_uart_error_mask(numeric)
            return str(numeric)

        return raw_value

    def _decode_uart_error_mask(self, mask_value: int) -> str:
        if mask_value == 0:
            return "0 (OK)"

        active = [name for bit, name in self.uart_error_flags if mask_value & bit]
        if not active:
            return str(mask_value)
        return f"{mask_value} ({' | '.join(active)})"

    def _apply_uploaded_config_value(self, key: str, raw_value: str):
        numeric = self._extract_numeric_value(raw_value)

        self.suspend_param_autosend = True
        try:
            if key == "rs232rx" and numeric is not None:
                _cmd = self.commands["bridge_set"]["rs232rx"]
                _val = f"{numeric} Bytes"
                self.param_entries[_cmd]["widget"].set(_val)
                self.param_confirmed_values[_cmd] = _val
            elif key == "rs232tx" and numeric is not None:
                _cmd = self.commands["bridge_set"]["rs232tx"]
                _val = f"{numeric} Bytes"
                self.param_entries[_cmd]["widget"].set(_val)
                self.param_confirmed_values[_cmd] = _val
            elif key == "rs232br" and numeric is not None:
                _cmd = self.commands["bridge_set"]["rs232br"]
                baud = str(numeric)
                self.param_entries[_cmd]["widget"].set(baud)
                self.param_confirmed_values[_cmd] = baud
                self.selected_rs232_baud = self._normalize_baud_value(baud, self.DEFAULT_RS232_BAUD)
                self._save_app_config()
            elif key == "klinerx" and numeric is not None:
                _cmd = self.commands["bridge_set"]["klinerx"]
                _val = f"{numeric} Bytes"
                self.param_entries[_cmd]["widget"].set(_val)
                self.param_confirmed_values[_cmd] = _val
            elif key == "klinetx" and numeric is not None:
                _cmd = self.commands["bridge_set"]["klinetx"]
                _val = f"{numeric} Bytes"
                self.param_entries[_cmd]["widget"].set(_val)
                self.param_confirmed_values[_cmd] = _val
            elif key == "klinebr" and numeric is not None:
                _cmd = self.commands["bridge_set"]["klinebr"]
                _val = str(numeric)
                self.param_entries[_cmd]["widget"].set(_val)
                self.param_confirmed_values[_cmd] = _val
            elif key == "dtr_fwd" and numeric is not None:
                _cmd = self.commands["bridge_set"]["dtr_fwd"]
                _val = "1 (ein)" if numeric else "0 (aus)"
                self.param_entries[_cmd]["widget"].set(_val)
                self.param_confirmed_values[_cmd] = _val
            elif key == "bbm" and numeric is not None:
                self.bridge_max_buffer_size = numeric
        finally:
            self.suspend_param_autosend = False
            self._update_buffer_fill_indicator()

    def _current_buffer_values(self):
        values = []
        for _title, command_key, _color in self.buffer_segment_specs:
            command = self.commands["bridge_set"][command_key]
            entry = self.param_entries.get(command)
            value_num = 0
            if entry:
                raw = entry["widget"].get().strip()
                value = self._resolve_param_value(command, raw)
                try:
                    value_num = int(value, 10)
                except Exception:
                    value_num = 0
            values.append(max(0, value_num))
        return values

    def _current_buffer_sum(self) -> int:
        return sum(self._current_buffer_values())

    def _update_buffer_fill_indicator(self):
        if self.buffer_usage_bar is None or self.buffer_fill_status_label is None:
            return

        accent_color = ("#1f6feb", "#2f81f7")
        values = self._current_buffer_values()
        used = sum(values)
        max_value = self.bridge_max_buffer_size if isinstance(self.bridge_max_buffer_size, int) else None

        if max_value is None or max_value <= 0:
            max_value_for_bar = max(used, 1)
        else:
            max_value_for_bar = max_value

        relx = 0.0
        for index, (title, _key, _color) in enumerate(self.buffer_segment_specs):
            segment = self.buffer_usage_segments.get(title)
            if segment is None:
                continue
            width = max(0.0, values[index] / max_value_for_bar)
            # Keep segment ordering stable while preventing overflow beyond 100% width.
            width = min(width, max(0.0, 1.0 - relx))
            segment.place_configure(relx=relx, relwidth=width, rely=0, relheight=1)
            relx += width

        if max_value is None or max_value <= 0:
            self.buffer_fill_status_label.configure(
                text=f"Used: {used} B / Max: - (read with upload)",
                text_color=accent_color,
            )
            return

        ratio = used / max_value
        percent = int(round(ratio * 100))
        self.buffer_fill_status_label.configure(
            text=f"Used: {used} B / Max: {max_value} B ({percent}%)",
            text_color=accent_color,
        )

    def _upload_bridge_config_worker(self):
        self._set_processing(True)
        try:
            self._log("Configuration upload started.")
            for command, key in self.config_upload_commands:
                ok, response = self._query_bridge_value(command, key, timeout=self._get_timeout_for_command(command))
                if not ok:
                    self._log(f"Upload {key} failed: {response}")
                    continue

                self.after(0, lambda k=key, v=response: self._apply_uploaded_config_value(k, v))
                time.sleep(0.03)

            self._log("Configuration upload completed.")
        finally:
            self._set_processing(False)
            self.param_refresh_running = False
            self.after(0, lambda: self.upload_cfg_btn.configure(state="normal"))

    def _refresh_bridge_statistics_worker(self):
        self._set_processing(True)
        try:
            self._log("Bridge snapshot refresh started.")
            for command, key in self.bridge_stat_request_commands:
                ok, response = self._query_bridge_value(command, key, timeout=self._get_timeout_for_command(command))
                self.bridge_stats_values[key] = self._normalize_bridge_stat_value(key, response) if ok else response

                self.after(0, self._refresh_statistics_display)
                time.sleep(0.03)

            self._log("Bridge snapshot refresh completed.")
        finally:
            self._set_processing(False)
            self.after(0, lambda: self.stats_refresh_btn.configure(state="normal"))

    def _show_about(self):
        messagebox.showinfo(
            "About",
            "RS232-KLine Bridge Suite\n"
            "Mit nativer chip45boot2-Integration\n"
            "(ohne externe EXE).",
        )

    def _parse_hex_bytes(self, payload: str):
        cleaned = payload.replace(",", " ").replace(";", " ").strip()
        if not cleaned:
            return b""

        tokens = cleaned.split()
        if len(tokens) == 1 and " " not in payload and payload.replace("0x", "").replace("0X", "").isalnum():
            raw = cleaned
            if raw.lower().startswith("0x"):
                raw = raw[2:]
            if len(raw) % 2 != 0:
                raise ValueError("Hex stream must have even length.")
            return bytes.fromhex(raw)

        out = bytearray()
        for token in tokens:
            t = token.strip().lower()
            if t.startswith("0x"):
                t = t[2:]
            if not t or len(t) > 2:
                raise ValueError(f"Invalid hex token: {token}")
            out.append(int(t, 16))
        return bytes(out)

    def _send_terminal_payload(self):
        if not self.serial_port or not self.serial_port.is_open:
            messagebox.showwarning("Nicht verbunden", "Bitte zuerst verbinden.")
            return

        payload = self.terminal_input_entry.get().strip()
        if not payload:
            messagebox.showwarning("Terminal", "Bitte einen Wert eingeben.")
            return

        mode = self.terminal_mode_option.get()
        append_newline = bool(self.terminal_newline_switch.get())

        try:
            if mode == "String":
                data = payload
                if append_newline:
                    data += "\n"
                raw = data.encode("utf-8", errors="replace")
            elif mode == "Character":
                if len(payload) != 1:
                    messagebox.showwarning("Character Mode", "Bitte genau ein Zeichen eingeben.")
                    return
                data = payload + ("\n" if append_newline else "")
                raw = data.encode("utf-8", errors="replace")
            else:
                raw = self._parse_hex_bytes(payload)
                if append_newline:
                    raw += b"\n"

            self.serial_port.write(raw)
            self.serial_port.flush()
            self._log(f"TX: {payload}")
            self.terminal_input_entry.delete(0, "end")
        except ValueError as exc:
            messagebox.showwarning("Hex Mode", str(exc))
        except serial.SerialException as exc:
            self._log(f"Serial write error: {exc}")

    def _send_kline_high(self):
        self._send_bridge_command(self.commands["bridge_set"]["kline_high"])

    def _send_kline_low(self):
        self._send_bridge_command(self.commands["bridge_set"]["kline_low"])

    def _send_kline_pulse(self):
        if not self._can_send_bridge_commands():
            return

        raw_value = self.kline_pulse_entry.get().strip()
        if not raw_value:
            messagebox.showwarning("KLine Pulse", "Bitte einen Pulse-Wert in ms eingeben (0..65535).")
            return

        try:
            pulse_ms = int(raw_value, 10)
        except ValueError:
            messagebox.showwarning("KLine Pulse", "Ungueltiger Zahlenwert. Erlaubt ist 0..65535 ms.")
            return

        if pulse_ms < 0 or pulse_ms > 0xFFFF:
            messagebox.showwarning("KLine Pulse", "Der Pulse-Wert muss im Bereich 0..65535 ms liegen.")
            return

        self._send_set_command_with_response(
            f"{self.commands['bridge_set']['kline_pulse_prefix']} {pulse_ms}",
            show_warnings=True,
        )

    def _on_mode_change(self, selected_mode: str):
        normalized = self._normalize_ui_mode(selected_mode)
        self.selected_ui_mode = normalized
        appearance = self.ui_mode_map.get(normalized, "System")
        ctk.set_appearance_mode(appearance)
        self._save_app_config()

    def _normalize_ui_mode(self, mode_value: str) -> str:
        value = (mode_value or "").strip().lower()
        if value in {"hell", "light"}:
            return "Hell"
        if value in {"dunkel", "dark"}:
            return "Dunkel"
        return "Automatisch"

    def _normalize_baud_value(self, value: str, fallback: str) -> str:
        candidate = (value or "").strip()
        if candidate in self.serial_baud_values:
            return candidate
        if fallback in self.serial_baud_values:
            return fallback
        return self.serial_baud_values[0]

    def _load_app_config(self):
        if not os.path.isfile(self.config_path):
            ctk.set_appearance_mode(self.ui_mode_map[self.selected_ui_mode])
            return

        try:
            with open(self.config_path, "r", encoding="utf-8") as fp:
                data = json.load(fp)
            stored_mode = self._normalize_ui_mode(str(data.get("ui_mode", "Automatisch")))
            self.selected_ui_mode = stored_mode
            self.selected_port_baud = self._normalize_baud_value(
                str(data.get("port_baud", self.DEFAULT_PORT_BAUD)),
                self.DEFAULT_PORT_BAUD,
            )
            self.selected_rs232_baud = self._normalize_baud_value(
                str(data.get("rs232_baud", self.DEFAULT_RS232_BAUD)),
                self.DEFAULT_RS232_BAUD,
            )
            self.debug_logging_enabled = bool(data.get("debug_logging", True))
            self.debug_logging_var.set(self.debug_logging_enabled)
        except Exception:
            self.selected_ui_mode = "Automatisch"
            self.selected_port_baud = self.DEFAULT_PORT_BAUD
            self.selected_rs232_baud = self.DEFAULT_RS232_BAUD
            self.debug_logging_enabled = True
            self.debug_logging_var.set(True)

        ctk.set_appearance_mode(self.ui_mode_map[self.selected_ui_mode])

    def _save_app_config(self):
        data = {
            "ui_mode": self.selected_ui_mode,
            "port_baud": self.selected_port_baud,
            "rs232_baud": self.selected_rs232_baud,
            "debug_logging": self.debug_logging_enabled,
        }
        try:
            with open(self.config_path, "w", encoding="utf-8") as fp:
                json.dump(data, fp, indent=2)
        except Exception as exc:
            self._log(f"WARN: Could not save app config: {exc}")

    def _refresh_ports(self):
        ports = [p.device for p in serial.tools.list_ports.comports()]
        if not ports:
            ports = ["-"]
        self.port_option.configure(values=ports)
        self.port_option.set(ports[0])

    def _toggle_connection(self):
        if self.serial_port and self.serial_port.is_open:
            self._disconnect_serial()
        else:
            self._connect_serial()

    def _connect_serial(self, request_version: bool = True) -> tuple[bool, str]:
        """Connect to serial port. Returns (success: bool, message: str)."""
        port = self.port_option.get().strip()
        if not port or port == "-":
            msg = "No port selected."
            messagebox.showwarning("Port fehlt", "Bitte seriellen Port waehlen.")
            return False, f"ERROR: {msg}"

        self._close_bootloader_serial()
        self.bootloader_ready = False
        self.boot_info_label.configure(text="Version: -")

        try:
            baud = int(self.baud_combo.get().strip())
        except ValueError:
            msg = "Invalid port baudrate."
            messagebox.showwarning("Baudrate", "Ungueltige Baudrate.")
            return False, f"ERROR: {msg}"

        self.selected_port_baud = self._normalize_baud_value(str(baud), self.DEFAULT_PORT_BAUD)
        self._save_app_config()

        try:
            self.serial_port = serial.Serial(port=port, baudrate=baud, timeout=0.2, write_timeout=1.0)
            self.serial_port.dtr = False
        except serial.SerialException as exc:
            msg = str(exc)
            messagebox.showerror("Connect Fehler", msg)
            return False, f"ERROR: {msg}"

        self.reader_stop_event.clear()
        self.reader_thread = threading.Thread(target=self._reader_loop, daemon=True)
        self.reader_thread.start()

        self.connect_btn.configure(text="Disconnect")
        self.dtr_switch.configure(state="normal")
        self.dtr_switch.select()
        self.serial_port.dtr = True
        self._update_dtr_indicator(True)
        msg = f"Connected to {port} @ {baud}."
        self._log(msg)
        self._log("DTR set to ON (auto).")
        if request_version:
            self._request_bridge_version()
        self._refresh_statistics_display()
        return True, "SUCCESS"

    def _disconnect_serial(self):
        self.reader_stop_event.set()

        if self.serial_port:
            try:
                self.serial_port.close()
            except Exception:
                pass

        self.serial_port = None
        self.connect_btn.configure(text="Connect")
        self.dtr_switch.deselect()
        self.dtr_switch.configure(state="disabled")
        self._update_dtr_indicator(False)
        if self.version_timeout_after_id is not None:
            try:
                self.after_cancel(self.version_timeout_after_id)
            except Exception:
                pass
            self.version_timeout_after_id = None
        self.awaiting_version_response = False
        self._set_bridge_fw_version("-")
        self._log("Serial disconnected.")
        self._refresh_statistics_display()

    def _reader_loop(self):
        while not self.reader_stop_event.is_set():
            try:
                if not self.serial_port or not self.serial_port.is_open:
                    break
                data = self.serial_port.readline()
                if data:
                    try:
                        msg = data.decode("utf-8", errors="replace").rstrip()
                    except Exception:
                        msg = repr(data)
                    accepted_key = None
                    accepted_event = None
                    ignored_reason = ""

                    # Handle awaited response first, before any logging that can block on GUI/file I/O.
                    with self.awaiting_response_lock:
                        response_key = self.awaiting_response_key
                        response_event = self.awaiting_response_event
                        can_process = bool(
                            msg and response_key is not None and response_event is not None and not response_event.is_set()
                        )

                        if can_process:
                            if response_key in {"set_rsp", "set_resp", "reset_rsp"}:
                                if self._is_set_response_message(msg):
                                    self.awaiting_response_value = msg
                                    accepted_key = response_key
                                    accepted_event = response_event
                                else:
                                    ignored_reason = self._describe_set_response_match(msg)
                            else:
                                self.awaiting_response_value = msg
                                if response_key in self.bridge_stats_values:
                                    self.bridge_stats_values[response_key] = self._normalize_bridge_stat_value(response_key, msg)
                                accepted_key = response_key
                                accepted_event = response_event

                    if accepted_event is not None:
                        accepted_event.set()

                    self._log(f"RX: {msg}")
                    if accepted_key is not None:
                        self._log(f"DEBUG await accept({accepted_key}): msg='{msg}', event_id={id(accepted_event)}")
                    elif ignored_reason and response_key == "set_rsp":
                        self._log(f"DEBUG set_rsp ignore: raw='{msg}', reason={ignored_reason}")
                    elif ignored_reason and response_key == "set_resp":
                        self._log(f"DEBUG set_resp ignore: raw='{msg}', reason={ignored_reason}")
                    elif ignored_reason and response_key == "reset_rsp":
                        self._log(f"DEBUG reset_rsp ignore: raw='{msg}', reason={ignored_reason}")
                    if self.awaiting_version_response and msg:
                        if self.version_timeout_after_id is not None:
                            try:
                                self.after_cancel(self.version_timeout_after_id)
                            except Exception:
                                pass
                            self.version_timeout_after_id = None
                        self.awaiting_version_response = False
                        self.after(0, lambda m=msg: self._set_bridge_fw_version(m))
            except serial.SerialException as exc:
                self._log(f"Serial read error: {exc}")
                break
            except Exception as exc:
                self._log(f"Unexpected read error: {exc}")
                break
            time.sleep(0.01)

    def _toggle_dtr(self):
        if not self.serial_port or not self.serial_port.is_open:
            self.dtr_switch.deselect()
            self._update_dtr_indicator(False)
            return

        enabled = bool(self.dtr_switch.get())
        try:
            self.serial_port.dtr = enabled
            self._update_dtr_indicator(enabled)
            state = "ON" if enabled else "OFF"
            self._log(f"DTR set to {state}.")
            if enabled and self.serial_port and self.serial_port.is_open:
                self._request_bridge_version()
        except serial.SerialException as exc:
            self._log(f"DTR set failed: {exc}")
            self.dtr_switch.deselect()
            self._update_dtr_indicator(False)

    def _update_dtr_indicator(self, enabled: bool):
        color = "#22c55e" if enabled else "#9ca3af"
        self.dtr_status_bubble.configure(fg_color=color)

    def _request_bridge_version(self):
        if not self.serial_port or not self.serial_port.is_open:
            return
        if not bool(self.dtr_switch.get()):
            return
        if self.version_timeout_after_id is not None:
            try:
                self.after_cancel(self.version_timeout_after_id)
            except Exception:
                pass
            self.version_timeout_after_id = None
        self.awaiting_version_response = True
        if not self._write_serial_line(self.commands["bridge_get"]["version"]):
            self.awaiting_version_response = False
            return

        timeout_ms = int(self._get_timeout_for_command(self.commands["bridge_get"]["version"]) * 1000)
        self.version_timeout_after_id = self.after(timeout_ms, self._on_version_request_timeout)

    def _on_version_request_timeout(self):
        self.version_timeout_after_id = None
        if not self.awaiting_version_response:
            return
        self.awaiting_version_response = False
        self._log("Version request timeout.")

    def _cancel_version_request(self):
        if self.version_timeout_after_id is not None:
            try:
                self.after_cancel(self.version_timeout_after_id)
            except Exception:
                pass
            self.version_timeout_after_id = None
        self.awaiting_version_response = False

    def _set_bridge_fw_version(self, text: str):
        self.bridge_fw_version = text.strip() if text else "-"
        self.bridge_fw_label.configure(text=self.bridge_fw_version)
        self._refresh_statistics_display()

    def _normalize_ack_text(self, message: str) -> str:
        raw = (message or "").strip().upper()
        # Drop control chars that may prefix serial payloads.
        filtered = "".join(ch for ch in raw if ch >= " " or ch == "\t")
        return " ".join(filtered.split())

    def _is_set_error_response(self, message: str) -> bool:
        normalized = self._normalize_ack_text(message)
        if not normalized:
            return False
        tokens = normalized.replace(":", " ").replace(";", " ").replace(",", " ").split()
        if any(token in {"ERROR", "ERR"} for token in tokens):
            return True
        if normalized.startswith("ERROR"):
            return True
        if normalized.startswith("ERR"):
            return True
        return False

    def _is_set_value_echo_response(self, message: str) -> bool:
        """Accept numeric/value echoes (e.g. '64', 'kbr=10400') returned by -set commands."""
        if not message:
            return False
        return self._extract_numeric_value(message) is not None

    def _is_set_response_message(self, message: str) -> bool:
        """Valid response for -set flows: ERR tokens or numeric echoes."""
        return self._is_set_error_response(message) or self._is_set_value_echo_response(message)

    def _describe_set_response_match(self, message: str) -> str:
        """Return why a message was (not) classified as a set response for debugging."""
        if self._is_set_error_response(message):
            return "ack-error"
        numeric = self._extract_numeric_value(message)
        if numeric is not None:
            return f"value-echo({numeric})"
        normalized = self._normalize_ack_text(message)
        return f"no-match normalized='{normalized}'"

    def _get_command_key(self, command: str, group: str = "bridge_set") -> str | None:
        """Return the registry key for a command string, e.g. '-set krx' -> 'klinerx'."""
        for key, cmd_str in self.commands.get(group, {}).items():
            if cmd_str == command:
                return key
        return None

    def _send_reset_and_wait_success(self, timeout: float = 0.05) -> tuple[bool, str]:
        self._set_processing(True)
        try:
            ok, response = self._query_bridge_value(self.commands["bridge_set"]["reset"], "reset_rsp", timeout=timeout)
            if not ok:
                return False, response

            if self._is_set_error_response(response):
                return False, response
            return True, response
        finally:
            self._set_processing(False)

    def _can_send_bridge_commands(self, show_warnings: bool = True) -> bool:
        if not self.serial_port or not self.serial_port.is_open:
            if show_warnings:
                messagebox.showwarning("Nicht verbunden", "Bitte zuerst verbinden.")
            return False

        if not bool(self.dtr_switch.get()):
            if show_warnings:
                messagebox.showwarning("DTR inaktiv", "Kommandos duerfen nur bei aktivem DTR gesendet werden.")
            return False

        return True

    def _send_bridge_command(self, command: str):
        if not self._can_send_bridge_commands():
            return
        if command.strip().lower().startswith("-set "):
            self._send_set_command_with_response(command, show_warnings=True)
            return
        self._write_serial_line(command)

    def _send_set_command_with_response(self, command: str, show_warnings: bool = True, timeout: float = 2.0) -> bool:
        self._set_processing(True)
        try:
            self._log(f"DEBUG set_cmd start: cmd='{command}', timeout={timeout:.2f}s")
            ok, response = self._query_bridge_value(command, "set_resp", timeout=timeout)
            if not ok:
                self._log(f"DEBUG set_cmd transport-fail: cmd='{command}', result='{response}'")
                if show_warnings:
                    messagebox.showwarning("Bridge", f"Keine gueltige Antwort fuer {command}: {response}")
                return False

            self._log(f"DEBUG set_cmd rx: raw='{response}'")

            # Check if response is an error
            if self._is_set_error_response(response):
                self._log(f"DEBUG set_cmd classified: ERROR for cmd='{command}'")
                self._log(f"Set rejected: {command} -> {response}")
                if show_warnings:
                    messagebox.showwarning("Bridge ERR", f"Bridge hat den Wert abgelehnt: {response}")
                return False

            # Any other response is treated as success (device acknowledged by responding)
            self._log(f"DEBUG set_cmd classified: SUCCESS for cmd='{command}'")
            self._log(f"Set acknowledged: {command} -> {response}")
            return True
        finally:
            self._set_processing(False)

    def _send_param(self, command: str, show_warnings: bool = True):
        if not self._can_send_bridge_commands(show_warnings=show_warnings):
            return

        control = self.param_entries[command]["widget"]
        raw_value = control.get().strip()
        previous_display = self.param_confirmed_values.get(command, raw_value)
        value = self._resolve_param_value(command, raw_value)
        if not value:
            if show_warnings:
                messagebox.showwarning("Wert fehlt", f"Bitte Wert fuer {command} eintragen.")
            return

        if command in {
            self.commands["bridge_set"]["rs232rx"],
            self.commands["bridge_set"]["rs232tx"],
            self.commands["bridge_set"]["klinerx"],
            self.commands["bridge_set"]["klinetx"],
        }:
            is_valid, error_message = self._validate_buffer_twos_complement(value)
            if not is_valid:
                if show_warnings:
                    messagebox.showwarning("Buffer-Wert", error_message)
                return

        cmd = f"{command} {value}"
        self._log(f"DEBUG param set echo start: cmd='{cmd}'")
        set_ok, set_response = self._query_bridge_value(cmd, "set_rsp", timeout=2.0)
        if not set_ok:
            self.suspend_param_autosend = True
            try:
                control.set(previous_display)
            finally:
                self.suspend_param_autosend = False
            if show_warnings:
                messagebox.showwarning("Bridge", f"Keine Antwort fuer {cmd}: {set_response}")
            self._update_buffer_fill_indicator()
            return

        self._log(f"DEBUG param set response: cmd='{cmd}', response='{set_response}'")
        if self._is_set_error_response(set_response):
            self.suspend_param_autosend = True
            try:
                control.set(previous_display)
            finally:
                self.suspend_param_autosend = False
            if show_warnings:
                messagebox.showwarning("Bridge ERR", f"Bridge hat den Wert abgelehnt: {set_response}")
            self._update_buffer_fill_indicator()
            return

        # Device echoes back the value. Determine parameter key from command for UI update.
        param_key = self._get_command_key(command)
        
        if param_key is not None:
            self._log(f"DEBUG param echoed value: cmd='{cmd}', param_key='{param_key}', echo='{set_response}'")
            self._apply_uploaded_config_value(param_key, set_response)
            if command == self.commands["bridge_set"]["rs232br"]:
                resolved = self._resolve_param_value(command, self.param_entries[command]["widget"].get().strip())
                self.selected_rs232_baud = self._normalize_baud_value(resolved, self.DEFAULT_RS232_BAUD)
                self._save_app_config()
            return

        confirmed_display = raw_value
        if command in {
            self.commands["bridge_set"]["rs232rx"],
            self.commands["bridge_set"]["rs232tx"],
            self.commands["bridge_set"]["klinerx"],
            self.commands["bridge_set"]["klinetx"],
        }:
            confirmed_display = f"{value} Bytes"
            self.suspend_param_autosend = True
            try:
                control.set(confirmed_display)
            finally:
                self.suspend_param_autosend = False
        elif command == self.commands["bridge_set"]["dtr_fwd"]:
            confirmed_display = "1 (ein)" if value == "1" else "0 (aus)"
            self.suspend_param_autosend = True
            try:
                control.set(confirmed_display)
            finally:
                self.suspend_param_autosend = False

        self.param_confirmed_values[command] = confirmed_display

        if command == self.commands["bridge_set"]["rs232br"]:
            self.selected_rs232_baud = self._normalize_baud_value(value, self.DEFAULT_RS232_BAUD)
            self._save_app_config()

        self._update_buffer_fill_indicator()

    def _on_param_control_changed(self, command: str):
        if self.suspend_param_autosend:
            return
        pending_job = self.param_autosend_jobs.pop(command, None)
        if pending_job:
            try:
                self.after_cancel(pending_job)
            except Exception:
                pass

        job_id = self.after(
            self.PARAM_AUTOSEND_DEBOUNCE_MS,
            lambda c=command: self._send_param_debounced(c),
        )
        self.param_autosend_jobs[command] = job_id

    def _send_param_debounced(self, command: str):
        self.param_autosend_jobs.pop(command, None)
        if self.suspend_param_autosend:
            return
        self._send_param(command, show_warnings=False)

    def _on_param_enter_pressed(self, command: str):
        pending_job = self.param_autosend_jobs.pop(command, None)
        if pending_job:
            try:
                self.after_cancel(pending_job)
            except Exception:
                pass
        self._send_param(command, show_warnings=True)
        return "break"

    def _resolve_param_value(self, command: str, raw_value: str) -> str:
        if not raw_value:
            return ""

        if command in {
            self.commands["bridge_set"]["rs232rx"],
            self.commands["bridge_set"]["rs232tx"],
            self.commands["bridge_set"]["klinerx"],
            self.commands["bridge_set"]["klinetx"],
        }:
            if raw_value in self.buffer_value_map:
                return self.buffer_value_map[raw_value]
            return raw_value

        if command in {
            self.commands["bridge_set"]["rs232br"],
            self.commands["bridge_set"]["klinebr"],
            self.commands["bridge_set"]["dtr_fwd"],
        }:
            return raw_value.split(" ", maxsplit=1)[0].strip()

        return raw_value

    def _validate_buffer_twos_complement(self, value: str):
        # Buffer values must be one of the allowed two-step sizes and map to valid 16-bit two's-complement.
        try:
            numeric_value = int(value, 10)
        except ValueError:
            return False, "Buffergroessen muessen numerisch sein (16..1024)."

        if numeric_value < 0:
            return False, "Negative Buffergroessen sind nicht zulaessig."

        if numeric_value < 16 or numeric_value > 1024:
            return False, "Buffergroessen muessen im Bereich 16..1024 liegen."

        if numeric_value not in self.buffer_allowed_values:
            return False, "Zulaessig sind nur 16, 32, 64, 128, 256, 512 und 1024."

        signed_value = numeric_value if numeric_value < 0x8000 else numeric_value - 0x10000
        roundtrip = signed_value & 0xFFFF
        if roundtrip != numeric_value:
            return False, "Ungueltiger Zweierkomplement-Wert fuer 16-Bit Buffergroesse."

        return True, ""

    def _write_serial_line(self, payload: str):
        try:
            line = (payload + "\n").encode("ascii", errors="replace")
            self.serial_port.write(line)
            self.serial_port.flush()
            self._log(f"TX: {payload}")
            return True
        except serial.SerialTimeoutException:
            self._log("Serial write timeout.")
            return False
        except serial.SerialException as exc:
            self._log(f"Serial write error: {exc}")
            return False

    def _pick_firmware(self):
        file_path = filedialog.askopenfilename(
            title="Firmware waehlen",
            filetypes=[("Hex/Bin", "*.hex *.bin"), ("All files", "*.*")],
        )
        if file_path:
            self.fw_path_entry.delete(0, "end")
            self.fw_path_entry.insert(0, file_path)

    def _pick_eeprom(self):
        file_path = filedialog.askopenfilename(
            title="EEPROM Datei waehlen",
            filetypes=[("Hex/Bin", "*.hex *.bin"), ("All files", "*.*")],
        )
        if file_path:
            self.eeprom_path_entry.delete(0, "end")
            self.eeprom_path_entry.insert(0, file_path)

    def _connect_to_bootloader(self):
        """Connect to bootloader: auto-connect bridge if needed, send reset, wait 1s, open bootloader."""
        port = self.port_option.get().strip()
        baud_text = self.baud_combo.get().strip()
        if not port or port == "-":
            messagebox.showwarning("Port fehlt", "Bitte seriellen Port waehlen.")
            return
        try:
            baud = int(baud_text)
        except ValueError:
            messagebox.showwarning("Baudrate", "Ungueltige Port-Baudrate.")
            return

        self.boot_connect_btn.configure(state="disabled")

        # Ensure bridge connection exists
        if not self.serial_port or not self.serial_port.is_open:
            self._log("Bootloader connect: no bridge connection, establishing...")
            ok, result = self._connect_serial(request_version=False)
            if not ok:
                self._log(f"Bootloader connect: {result}")
                self.boot_connect_btn.configure(state="normal")
                return
            self._log("Bootloader connect: bridge connection established.")
            time.sleep(0.2)  # Give reader thread time to start

        # Avoid a parallel version request while waiting for reset response.
        self._cancel_version_request()

        # Check DTR is active
        if not bool(self.dtr_switch.get()):
            self._log("ERROR: DTR not active. Cannot send reset command.")
            self.boot_connect_btn.configure(state="normal")
            return

        # Send reset command and wait for a regular set-style response (echo or ERR).
        ok, reset_response = self._send_reset_and_wait_success(timeout=0.05)
        if not ok:
            if reset_response == "TIMEOUT":
                # Some bridge firmware resets immediately and never returns a textual response.
                self._log("WARN: Reset response timeout; continuing with bootloader connect.")
            else:
                self._log(f"ERROR: Reset command failed: {reset_response}")
                self.boot_connect_btn.configure(state="normal")
                return
        else:
            self._log(f"Bootloader connect: reset command confirmed: {reset_response}")
        self._log("Waiting 1s for bridge reset...")
        self._disconnect_serial()
        self._log("Bridge connection closed.")
        threading.Thread(target=self._bootloader_connect_worker, args=(port, baud), daemon=True).start()

    def _bootloader_connect_worker(self, port: str, baud: int):
        time.sleep(0.02)
        max_retries = max(1, int(self.BOOT_CONNECT_MAX_RETRIES))
        retry_delay = max(0.0, float(self.BOOT_CONNECT_RETRY_DELAY_S))
        handshake_timeout = max(0.5, float(self.BOOT_HANDSHAKE_TIMEOUT_S))
        last_error = "unknown error"

        for attempt in range(1, max_retries + 1):
            self._log(f"Opening bootloader connection (attempt {attempt}/{max_retries})...")

            try:
                boot_ser = serial.Serial(port=port, baudrate=baud, timeout=0.2, write_timeout=1.0)
            except serial.SerialException as exc:
                last_error = f"Open failed: {exc}"
                self._log(f"Bootloader open failed (attempt {attempt}/{max_retries}): {exc}")
                if attempt < max_retries:
                    self._log(f"Retrying bootloader connect in {retry_delay:.1f}s...")
                    time.sleep(retry_delay)
                continue

            ok, version_or_error = self._bootloader_handshake(boot_ser, timeout_s=handshake_timeout)
            if ok:
                self.bootloader_serial = boot_ser
                self._log(f"Bootloader connected: {version_or_error}")
                self.after(0, lambda: self._finish_bootloader_connect(True, version_or_error, ""))
                return

            last_error = version_or_error
            self._log(f"Bootloader handshake failed (attempt {attempt}/{max_retries}): {version_or_error}")
            try:
                boot_ser.close()
            except Exception:
                pass

            if attempt < max_retries:
                self._log(f"Retrying bootloader connect in {retry_delay:.1f}s...")
                time.sleep(retry_delay)

        self.after(0, lambda: self._finish_bootloader_connect(False, "", last_error))

    def _finish_bootloader_connect(self, connected: bool, version: str, error_text: str):
        self.boot_connect_btn.configure(state="normal")
        if connected:
            self.bootloader_ready = True
            self.bootloader_version = version
            self.boot_info_label.configure(text=f"Version: {version}")
            self._log(f"Bootloader connected ({version}).")
        else:
            self.bootloader_ready = False
            self.bootloader_version = ""
            self.boot_info_label.configure(text="Version: -")
            self._log(f"Bootloader connect failed: {error_text}")
        self._refresh_statistics_display()

    def _bootloader_handshake(self, boot_ser: serial.Serial, timeout_s: float = 8.0):
        deadline = time.time() + max(0.5, float(timeout_s))
        try:
            boot_ser.reset_input_buffer()
            boot_ser.reset_output_buffer()
        except Exception:
            pass

        while time.time() < deadline:
            try:
                boot_ser.write(self.commands["bootloader"]["handshake"])
                boot_ser.flush()
            except serial.SerialException as exc:
                return False, f"Write failed: {exc}"

            response = self._boot_read_until(boot_ser, terminator=b">", timeout=0.35, max_len=128)
            if not response:
                continue

            text = response.decode("ascii", errors="replace").strip()
            if "c45b2" not in text:
                continue

            token = text.split("c45b2", maxsplit=1)[1].strip()
            version = token if token else "unknown"
            return True, version

        return False, f"Timeout waiting for bootloader identifier ({timeout_s:.1f}s)"

    def _flash_firmware(self):
        self._start_flash("firmware")

    def _flash_eeprom(self):
        self._start_flash("eeprom")

    def _start_flash(self, mode: str):
        if not self.bootloader_ready:
            messagebox.showwarning("Bootloader", "Bitte zuerst 'Connect to Bootloader' ausfuehren.")
            return

        port = self.port_option.get().strip()
        if not port or port == "-":
            messagebox.showwarning("Port fehlt", "Bitte seriellen Port waehlen.")
            return

        if mode == "firmware":
            image_path = self.fw_path_entry.get().strip()
            if not image_path or not os.path.isfile(image_path):
                messagebox.showwarning("Firmware", "Bitte gueltige Firmware-Datei auswaehlen.")
                return
        else:
            image_path = self.eeprom_path_entry.get().strip()
            if not image_path or not os.path.isfile(image_path):
                messagebox.showwarning("EEPROM", "Bitte gueltige EEPROM-Datei auswaehlen.")
                return

        self._set_flash_busy(True)
        self._set_boot_progress(0.0)
        threading.Thread(target=self._bootloader_program_worker, args=(mode, image_path), daemon=True).start()

    def _set_flash_busy(self, busy: bool):
        state = "disabled" if busy else "normal"
        self.flash_firmware_btn.configure(state=state)
        self.flash_eeprom_btn.configure(state=state)
        self.boot_connect_btn.configure(state=state)
        self.boot_start_app_btn.configure(state=state)
        if busy:
            self._set_processing(True)
        else:
            self._set_processing(False)

    def _set_boot_progress(self, value: float):
        clamped = max(0.0, min(1.0, value))
        self.boot_progress.set(clamped)
        self.boot_progress_label.configure(text=f"Fortschritt: {int(clamped * 100)}%")

    def _bootloader_program_worker(self, mode: str, hex_path: str):
        try:
            records = self._build_hex_upload_records(hex_path)
        except Exception as exc:
            self._log(f"HEX parse failed: {exc}")
            self.after(0, lambda: self._set_flash_busy(False))
            return

        if not records:
            self._log("HEX file enthaelt keine Datenrecords.")
            self.after(0, lambda: self._set_flash_busy(False))
            return

        ser = self.bootloader_serial
        if ser is None or not ser.is_open:
            self._log("Bootloader serial not connected.")
            self.after(0, lambda: self._set_flash_busy(False))
            return

        cmd = (
            self.commands["bootloader"]["program_firmware"]
            if mode == "firmware"
            else self.commands["bootloader"]["program_eeprom"]
        )
        mode_name = "Flash" if mode == "firmware" else "EEPROM"

        if not self._bootloader_enter_programming_mode(ser, cmd, mode_name):
            self.after(0, lambda: self._set_flash_busy(False))
            return

        total = len(records)
        for idx, record in enumerate(records, start=1):
            if not self._bootloader_upload_record(ser, record):
                self._log(f"{mode_name} upload failed at record {idx}/{total}.")
                self.after(0, lambda: self._set_flash_busy(False))
                return
            progress = idx / total
            self.after(0, lambda p=progress: self._set_boot_progress(p))

        self._log(f"{mode_name} upload erfolgreich.")
        self.after(0, lambda: self._set_flash_busy(False))

    def _bootloader_enter_programming_mode(self, ser: serial.Serial, command: bytes, mode_name: str) -> bool:
        try:
            ser.reset_input_buffer()
            ser.write(b"\n")
            ser.flush()
            time.sleep(1.0)
            _ = self._boot_read_until(ser, terminator=bytes([self.XON]), timeout=0.6, max_len=128)
            ser.write(command)
            ser.flush()
        except serial.SerialException as exc:
            self._log(f"{mode_name}: serial error before programming mode: {exc}")
            return False

        if not self._boot_wait_for_byte(ser, self.XOFF, timeout=3.0):
            self._log(f"{mode_name}: timeout waiting for XOFF.")
            return False

        response = self._boot_read_until(ser, terminator=b"\r", timeout=2.0, max_len=32)
        text = response.decode("ascii", errors="replace").strip()
        if not (text.startswith("pf+") or text.startswith("pe+")):
            self._log(f"{mode_name}: could not enter programming mode ({text}).")
            return False

        if not self._boot_wait_for_byte(ser, self.XON, timeout=2.0):
            self._log(f"{mode_name}: timeout waiting for XON.")
            return False

        self._log(f"{mode_name}: programming mode active.")
        return True

    def _bootloader_upload_record(self, ser: serial.Serial, record: str) -> bool:
        try:
            time.sleep(0.008)
            ser.write(record.encode("ascii"))
            ser.flush()
        except serial.SerialException as exc:
            self._log(f"Upload serial write failed: {exc}")
            return False

        response = self._boot_read_until(ser, terminator=bytes([self.XON]), timeout=2.0, max_len=32)
        return bool(response and response[-1] == self.XON)

    def _boot_wait_for_byte(self, ser: serial.Serial, wanted: int, timeout: float) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            b = ser.read(1)
            if not b:
                continue
            if b[0] == wanted:
                return True
        return False

    def _boot_read_until(self, ser: serial.Serial, terminator: bytes, timeout: float, max_len: int) -> bytes:
        deadline = time.time() + timeout
        out = bytearray()
        while time.time() < deadline and len(out) < max_len:
            b = ser.read(1)
            if not b:
                continue
            out.extend(b)
            if out.endswith(terminator):
                break
        return bytes(out)

    def _build_hex_upload_records(self, hex_path: str):
        flash_buffer = bytearray([0xFF] * self.MAX_FLASH_BYTES)
        base_address = 0
        highest_address = 0
        saw_data = False

        with open(hex_path, "r", encoding="ascii", errors="strict") as fp:
            for raw_line in fp:
                line = raw_line.strip()
                if not line:
                    continue
                if not line.startswith(":"):
                    raise ValueError(f"Invalid Intel HEX line: {line}")

                payload = line[1:]
                if len(payload) < 10 or len(payload) % 2 != 0:
                    raise ValueError(f"Malformed Intel HEX line: {line}")

                record = bytes.fromhex(payload)
                byte_count = record[0]
                address = (record[1] << 8) | record[2]
                record_type = record[3]
                data = record[4:4 + byte_count]
                checksum = record[4 + byte_count]

                if ((sum(record[:-1]) + checksum) & 0xFF) != 0:
                    raise ValueError("Checksum mismatch in HEX file")

                if record_type == 0x00:
                    absolute = base_address + address
                    end = absolute + byte_count
                    if end > self.MAX_FLASH_BYTES:
                        raise ValueError("HEX exceeds supported size")
                    flash_buffer[absolute:end] = data
                    highest_address = max(highest_address, end)
                    saw_data = True
                elif record_type == 0x01:
                    break
                elif record_type == 0x02:
                    if byte_count != 2:
                        raise ValueError("Invalid type-02 record")
                    segment = (data[0] << 8) | data[1]
                    base_address = segment << 4
                elif record_type == 0x04:
                    if byte_count != 2:
                        raise ValueError("Invalid type-04 record")
                    linear = (data[0] << 8) | data[1]
                    base_address = linear << 16
                else:
                    continue

        if not saw_data:
            return []

        records = []
        current_segment = 0
        end_address = ((highest_address + 15) // 16) * 16
        for absolute in range(0, end_address, 16):
            needed_segment = absolute >> 4
            if needed_segment != current_segment and (absolute & 0xFFFF) == 0:
                current_segment = needed_segment
                seg_value = current_segment & 0xFFFF
                records.append(self._intel_hex_record(0x0000, 0x02, bytes([(seg_value >> 8) & 0xFF, seg_value & 0xFF])))

            chunk = bytes(flash_buffer[absolute:absolute + 16])
            records.append(self._intel_hex_record(absolute & 0xFFFF, 0x00, chunk))

        records.append(self._intel_hex_record(0x0000, 0x01, b""))
        return records

    def _intel_hex_record(self, address: int, record_type: int, data: bytes) -> str:
        byte_count = len(data)
        raw = bytearray([byte_count, (address >> 8) & 0xFF, address & 0xFF, record_type])
        raw.extend(data)
        checksum = ((-sum(raw)) & 0xFF)
        return ":" + raw.hex().upper() + f"{checksum:02X}" + "\n"

    def _bootloader_start_application(self):
        ser = self.bootloader_serial
        if not self.bootloader_ready or ser is None or not ser.is_open:
            messagebox.showwarning("Bootloader", "Bootloader ist nicht verbunden.")
            return

        try:
            ser.write(b"\n")
            ser.flush()
            time.sleep(0.2)
            ser.reset_input_buffer()
            ser.write(self.commands["bootloader"]["start_application"])
            ser.flush()
            self._log("Bootloader command sent: g (Start Application)")
        except serial.SerialException as exc:
            self._log(f"Start application failed: {exc}")
            return

        self._close_bootloader_serial()
        self.bootloader_ready = False
        self.boot_info_label.configure(text="Version: -")
        self._refresh_statistics_display()

    def _close_bootloader_serial(self):
        if self.bootloader_serial is not None:
            try:
                self.bootloader_serial.close()
            except Exception:
                pass
        self.bootloader_serial = None
        self._refresh_statistics_display()

    def _on_close(self):
        self._close_bootloader_serial()
        self._disconnect_serial()
        self.destroy()


if __name__ == "__main__":
    app = BridgeGui()
    app.mainloop()


