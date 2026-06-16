import os
import queue
import json
import threading
import time
from datetime import datetime
from tkinter import BooleanVar, Menu, filedialog, messagebox

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

    def __init__(self):
        super().__init__()
        self.title("RS232-KLine Bridge GUI")
        self.geometry("1220x760")
        self.minsize(1080, 680)

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
        self.ui_mode_map = {"Hell": "Light", "Dunkel": "Dark", "Automatisch": "System"}
        self.terminal_mode_values = ["String", "Character", "Bytes (Hex)"]
        self.config_path = os.path.join(os.path.dirname(__file__), "app_config.json")
        self.selected_ui_mode = "Automatisch"
        self.selected_port_baud = self.DEFAULT_PORT_BAUD
        self.selected_rs232_baud = self.DEFAULT_RS232_BAUD
        self.active_tab_name = ""

        self._load_app_config()

        self._build_menu()
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

        self.configure(menu=menubar)

    def _build_buffer_labels(self):
        labels = []
        value_map = {}
        for value in self.buffer_allowed_values:
            label = f"{value} Bytes"
            labels.append(label)
            value_map[label] = str(value)
        return labels, value_map

    def _build_ui(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        title_frame = ctk.CTkFrame(self, fg_color="transparent")
        title_frame.grid(row=0, column=0, sticky="ew", padx=12, pady=(12, 4))
        title_frame.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(title_frame, text="RS232-KLine Bridge Engineering Console", font=ctk.CTkFont(size=24, weight="bold")).grid(
            row=0, column=0, sticky="w"
        )
        ctk.CTkLabel(title_frame, text="Configuration, diagnostics, and native chip45boot2 flashing in one workspace").grid(
            row=1, column=0, sticky="w", pady=(2, 0)
        )

        header = ctk.CTkFrame(self, corner_radius=12, border_width=1, border_color=self.CARD_BORDER)
        header.grid(row=1, column=0, sticky="ew", padx=12, pady=(4, 8))
        header.grid_columnconfigure(8, weight=1)

        ctk.CTkLabel(header, text="Serial Port").grid(row=0, column=0, padx=(10, 6), pady=10)
        self.port_option = ctk.CTkOptionMenu(header, values=["-"])
        self.port_option.grid(row=0, column=1, padx=6, pady=10)

        refresh_btn = ctk.CTkButton(header, text="Refresh", width=90, command=self._refresh_ports)
        refresh_btn.grid(row=0, column=2, padx=6, pady=10)

        ctk.CTkLabel(header, text="Port Baud").grid(row=0, column=3, padx=(14, 6), pady=10)
        self.baud_combo = ctk.CTkComboBox(header, values=self.serial_baud_values, width=120)
        self.baud_combo.set(self.selected_port_baud)
        self.baud_combo.grid(row=0, column=4, padx=6, pady=10)

        self.connect_btn = ctk.CTkButton(header, text="Connect", width=110, command=self._toggle_connection)
        self.connect_btn.grid(row=0, column=5, padx=(14, 6), pady=10)

        self.dtr_switch = ctk.CTkSwitch(header, text="DTR aktiv", command=self._toggle_dtr, state="disabled")
        self.dtr_switch.grid(row=0, column=6, padx=(14, 6), pady=10)

        self.status_label = ctk.CTkLabel(header, text="Disconnected", text_color="#cc4b37")
        self.status_label.grid(row=0, column=7, padx=(10, 12), pady=10)

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
        bridge_tab.grid_rowconfigure(1, weight=1)

        stats_tab = self.main_tabs.tab("Statistics")
        stats_tab.grid_columnconfigure(0, weight=1)

        terminal_tab = self.main_tabs.tab("Terminal")
        terminal_tab.grid_columnconfigure(0, weight=1)
        terminal_tab.grid_rowconfigure(1, weight=1)

        boot_tab = self.main_tabs.tab("Bootloader")
        boot_tab.grid_columnconfigure(0, weight=1)

        stats_frame = ctk.CTkFrame(stats_tab, corner_radius=12, border_width=1, border_color=self.CARD_BORDER)
        stats_frame.grid(row=0, column=0, sticky="ew", pady=(8, 8))
        stats_frame.grid_columnconfigure((1, 3), weight=1)
        ctk.CTkLabel(stats_frame, text="Statistics", font=ctk.CTkFont(size=18, weight="bold")).grid(
            row=0, column=0, padx=10, pady=(10, 6), sticky="w"
        )
        ctk.CTkButton(stats_frame, text="Reset Counters", width=120, command=self._reset_runtime_statistics).grid(
            row=0, column=3, padx=10, pady=(10, 6), sticky="e"
        )

        stat_rows = [
            ("Session Uptime", "uptime", 1, 0),
            ("Port", "port", 1, 2),
            ("TX Frames", "tx", 2, 0),
            ("RX Frames", "rx", 2, 2),
            ("Warnings", "warn", 3, 0),
            ("Errors", "error", 3, 2),
            ("Last TX", "last_tx", 4, 0),
            ("Last RX", "last_rx", 4, 2),
            ("Bootloader", "boot", 5, 0),
            ("Bridge FW", "fw", 5, 2),
        ]
        for title, key, row, col in stat_rows:
            ctk.CTkLabel(stats_frame, text=title, font=ctk.CTkFont(weight="bold")).grid(
                row=row, column=col, padx=(10, 6), pady=4, sticky="w"
            )
            value_lbl = ctk.CTkLabel(stats_frame, text="-", font=ctk.CTkFont(family="Consolas", size=13))
            value_lbl.grid(row=row, column=col + 1, padx=(0, 10), pady=4, sticky="w")
            self.stats_value_labels[key] = value_lbl

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

        ctk.CTkLabel(
            terminal_ctrl,
            text="Hex examples: '01 A0 FF' or '0x01 0xA0 0xFF'",
            text_color=("#5f6b7a", "#95a1b1"),
        ).grid(row=1, column=0, columnspan=5, padx=10, pady=(0, 10), sticky="w")

        self.terminal_rx_box = ctk.CTkTextbox(
            terminal_tab,
            wrap="none",
            corner_radius=12,
            border_width=1,
            border_color=self.CARD_BORDER,
            font=ctk.CTkFont(family="Consolas", size=12),
        )
        self.terminal_rx_box.grid(row=1, column=0, sticky="nsew", pady=(0, 8))

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

        body = ctk.CTkFrame(bridge_tab, fg_color="transparent")
        body.grid(row=0, column=0, sticky="ew", pady=(8, 8))
        body.grid_columnconfigure((0, 1), weight=1)

        command_frame = ctk.CTkFrame(body, corner_radius=12, border_width=1, border_color=self.CARD_BORDER)
        command_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 6), pady=10)
        command_frame.grid_columnconfigure((0, 1, 2), weight=1)
        ctk.CTkLabel(command_frame, text="Bridge Kommandos", font=ctk.CTkFont(size=18, weight="bold")).grid(
            row=0, column=0, columnspan=3, padx=10, pady=(10, 14), sticky="w"
        )

        quick_commands = [
            ("Config (-c)", "-c"),
            ("Reset (-r)", "-r"),
            ("Stats (-n)", "-n"),
            ("Save EEPROM (-s)", "-s"),
        ]

        for idx, (label, cmd) in enumerate(quick_commands, start=1):
            btn = ctk.CTkButton(command_frame, text=label, command=lambda c=cmd: self._send_bridge_command(c))
            btn.grid(row=idx, column=0, padx=10, pady=6, sticky="ew", columnspan=3)

        settings_frame = ctk.CTkFrame(body, corner_radius=12, border_width=1, border_color=self.CARD_BORDER)
        settings_frame.grid(row=0, column=1, sticky="nsew", padx=(6, 0), pady=10)
        settings_frame.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(settings_frame, text="Parameter Kommandos", font=ctk.CTkFont(size=18, weight="bold")).grid(
            row=0, column=0, columnspan=3, padx=10, pady=(10, 14), sticky="w"
        )

        self.param_entries = {}
        params = [
            ("RS232 RX Buffer (-rrx, 16..1024 Bytes)", "-rrx", "buffer"),
            ("RS232 TX Buffer (-rtx, 16..1024 Bytes)", "-rtx", "buffer"),
            ("RS232 Baud (-rbr)", "-rbr", "baud"),
            ("KLine Baud (-kbr)", "-kbr", "baud"),
            ("KLine RX Buffer (-krx, 16..1024 Bytes)", "-krx", "buffer"),
            ("KLine TX Buffer (-ktx, 16..1024 Bytes)", "-ktx", "buffer"),
            ("DTR Forwarding (-fwd)", "-fwd", "fwd"),
        ]

        for row, (title, cmd, control_type) in enumerate(params, start=1):
            ctk.CTkLabel(settings_frame, text=title).grid(row=row, column=0, padx=(10, 8), pady=6, sticky="w")

            if control_type == "buffer":
                control = ctk.CTkComboBox(settings_frame, values=self.buffer_labels)
                control.set("64 Bytes")
            elif control_type == "baud":
                control = ctk.CTkComboBox(settings_frame, values=self.param_baud_values)
                control.set("10400" if cmd == "-kbr" else self.selected_rs232_baud)
            elif control_type == "fwd":
                control = ctk.CTkComboBox(settings_frame, values=self.fwd_labels)
                control.set(self.fwd_labels[1])
            else:
                control = ctk.CTkEntry(settings_frame)

            control.grid(row=row, column=1, padx=8, pady=6, sticky="ew")
            self.param_entries[cmd] = {"widget": control, "type": control_type}
            send_btn = ctk.CTkButton(settings_frame, text="Senden", width=90, command=lambda c=cmd: self._send_param(c))
            send_btn.grid(row=row, column=2, padx=(8, 10), pady=6)

        self.log_box = ctk.CTkTextbox(bridge_tab, wrap="word", corner_radius=12, border_width=1, border_color=self.CARD_BORDER)
        self.log_box.grid(row=1, column=0, sticky="nsew", pady=(0, 8))
        self.log_box.configure(font=ctk.CTkFont(family="Consolas", size=12))
        self._configure_log_tags()

        boot_frame = ctk.CTkFrame(boot_tab, corner_radius=12, border_width=1, border_color=self.CARD_BORDER)
        boot_frame.grid(row=0, column=0, sticky="ew", pady=(8, 8))
        boot_frame.grid_columnconfigure(1, weight=1)
        boot_frame.grid_columnconfigure(3, weight=0)

        ctk.CTkLabel(boot_frame, text="Bootloader", font=ctk.CTkFont(size=17, weight="bold")).grid(
            row=0, column=0, padx=(10, 8), pady=(10, 6), sticky="w"
        )

        ctk.CTkLabel(boot_frame, text="Native chip45boot2-Protokoll (ohne externe EXE)").grid(
            row=0, column=1, columnspan=2, padx=(8, 10), pady=(10, 6), sticky="w"
        )

        self.boot_connect_btn = ctk.CTkButton(
            boot_frame,
            text="Connect to Bootloader",
            width=180,
            command=self._connect_to_bootloader,
        )
        self.boot_connect_btn.grid(row=1, column=0, padx=(10, 8), pady=6, sticky="ns")

        self.boot_status_label = ctk.CTkLabel(boot_frame, text="Bootloader: not connected", text_color="#9a6700")
        self.boot_status_label.grid(row=1, column=2, padx=(8, 10), pady=6, sticky="w")

        self.fw_path_entry = ctk.CTkEntry(boot_frame)
        self.fw_path_entry.grid(row=2, column=1, padx=8, pady=6, sticky="ew")
        ctk.CTkButton(boot_frame, text="Firmware...", width=120, command=self._pick_firmware).grid(
            row=2, column=2, padx=(8, 10), pady=6
        )
        self.flash_firmware_btn = ctk.CTkButton(boot_frame, text="Flash Firmware", width=140, command=self._flash_firmware)
        self.flash_firmware_btn.grid(row=2, column=0, padx=(10, 8), pady=6)

        self.eeprom_path_entry = ctk.CTkEntry(boot_frame)
        self.eeprom_path_entry.grid(row=3, column=1, padx=8, pady=6, sticky="ew")
        ctk.CTkButton(boot_frame, text="EEPROM...", width=120, command=self._pick_eeprom).grid(
            row=3, column=2, padx=(8, 10), pady=6
        )
        self.flash_eeprom_btn = ctk.CTkButton(boot_frame, text="Flash EEPROM", width=140, command=self._flash_eeprom)
        self.flash_eeprom_btn.grid(row=3, column=0, padx=(10, 8), pady=6)

        self.boot_start_app_btn = ctk.CTkButton(boot_frame, text="Start Application", width=140, command=self._bootloader_start_application)
        self.boot_start_app_btn.grid(row=4, column=0, padx=(10, 8), pady=(6, 6))

        self.boot_info_label = ctk.CTkLabel(boot_frame, text="Version: -")
        self.boot_info_label.grid(row=4, column=1, padx=8, pady=(6, 6), sticky="w")

        self.boot_progress = ctk.CTkProgressBar(boot_frame)
        self.boot_progress.grid(row=5, column=1, padx=8, pady=(6, 10), sticky="ew")
        self.boot_progress.set(0)

        self.boot_progress_label = ctk.CTkLabel(boot_frame, text="Fortschritt: 0%")
        self.boot_progress_label.grid(row=5, column=2, padx=(8, 10), pady=(6, 10), sticky="w")

        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self._refresh_statistics_display()

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

    def _disable_dtr_for_terminal(self):
        if not self.serial_port or not self.serial_port.is_open:
            return
        if not bool(self.dtr_switch.get()):
            return
        self.dtr_switch.deselect()
        self._toggle_dtr()

    def _log(self, text: str):
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

        self.log_queue.put(f"[{ts}] {text}\n")
        self.after(0, self._refresh_statistics_display)

    def _configure_log_tags(self):
        # Colors are chosen to stay readable on both light and dark system themes.
        self.log_box.tag_config("tx", foreground="#1f6feb")
        self.log_box.tag_config("rx", foreground="#238636")
        self.log_box.tag_config("warn", foreground="#9a6700")
        self.log_box.tag_config("error", foreground="#cf222e")
        self.log_box.tag_config("info", foreground="#57606a")

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
            msg = self.log_queue.get_nowait()
            tag = self._log_tag_for_message(msg)
            self.log_box.insert("end", msg, tag)
            if self.log_autoscroll_var.get():
                self.log_box.see("end")
        self.after(100, self._drain_log_queue)

    def _clear_log(self):
        self.log_box.delete("1.0", "end")
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

    def _refresh_statistics_display(self):
        if not self.stats_value_labels:
            return
        uptime_s = int(max(0, time.time() - self.app_start_time))
        hh = uptime_s // 3600
        mm = (uptime_s % 3600) // 60
        ss = uptime_s % 60
        port_text = self.port_option.get().strip() if hasattr(self, "port_option") else "-"
        if not port_text:
            port_text = "-"
        if self.serial_port and self.serial_port.is_open:
            port_state = f"{port_text} connected"
        elif self.bootloader_serial and self.bootloader_serial.is_open:
            port_state = f"{port_text} bootloader"
        else:
            port_state = "disconnected"

        boot_state = "connected" if self.bootloader_ready else "idle"
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

    def _show_about(self):
        messagebox.showinfo(
            "Ueber",
            "RS232-KLine Bridge GUI\n"
            "Mit nativer chip45boot2-Integration\n"
            "(ohne externe EXE).",
        )

    def _append_terminal_rx(self, text: str):
        self.terminal_rx_box.insert("end", text + "\n")
        if self.log_autoscroll_var.get():
            self.terminal_rx_box.see("end")

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
        except Exception:
            self.selected_ui_mode = "Automatisch"
            self.selected_port_baud = self.DEFAULT_PORT_BAUD
            self.selected_rs232_baud = self.DEFAULT_RS232_BAUD

        ctk.set_appearance_mode(self.ui_mode_map[self.selected_ui_mode])

    def _save_app_config(self):
        data = {
            "ui_mode": self.selected_ui_mode,
            "port_baud": self.selected_port_baud,
            "rs232_baud": self.selected_rs232_baud,
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

    def _connect_serial(self):
        port = self.port_option.get().strip()
        if not port or port == "-":
            messagebox.showwarning("Port fehlt", "Bitte seriellen Port waehlen.")
            return

        self._close_bootloader_serial()
        self.bootloader_ready = False
        self.boot_status_label.configure(text="Bootloader: not connected", text_color="#9a6700")
        self.boot_info_label.configure(text="Version: -")

        try:
            baud = int(self.baud_combo.get().strip())
        except ValueError:
            messagebox.showwarning("Baudrate", "Ungueltige Baudrate.")
            return

        self.selected_port_baud = self._normalize_baud_value(str(baud), self.DEFAULT_PORT_BAUD)
        self._save_app_config()

        try:
            self.serial_port = serial.Serial(port=port, baudrate=baud, timeout=0.2, write_timeout=1.0)
            self.serial_port.dtr = False
        except serial.SerialException as exc:
            messagebox.showerror("Connect Fehler", str(exc))
            return

        self.reader_stop_event.clear()
        self.reader_thread = threading.Thread(target=self._reader_loop, daemon=True)
        self.reader_thread.start()

        self.connect_btn.configure(text="Disconnect")
        self.dtr_switch.configure(state="normal")
        self.dtr_switch.select()
        self.serial_port.dtr = True
        self.status_label.configure(text=f"Connected: {port}", text_color="#2e8b57")
        self._log(f"Connected to {port} @ {baud}.")
        self._log("DTR set to ON (auto).")
        self._request_bridge_version()
        self._refresh_statistics_display()

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
        self.status_label.configure(text="Disconnected", text_color="#cc4b37")
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
                    self._log(f"RX: {msg}")
                    self.after(0, lambda m=msg: self._append_terminal_rx(m))
                    if self.awaiting_version_response and msg:
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
            return

        enabled = bool(self.dtr_switch.get())
        try:
            self.serial_port.dtr = enabled
            state = "ON" if enabled else "OFF"
            self._log(f"DTR set to {state}.")
            if enabled and self.serial_port and self.serial_port.is_open:
                self._request_bridge_version()
        except serial.SerialException as exc:
            self._log(f"DTR set failed: {exc}")
            self.dtr_switch.deselect()

    def _request_bridge_version(self):
        if not self.serial_port or not self.serial_port.is_open:
            return
        if not bool(self.dtr_switch.get()):
            return
        self.awaiting_version_response = True
        if not self._write_serial_line("-v"):
            self.awaiting_version_response = False

    def _set_bridge_fw_version(self, text: str):
        self.bridge_fw_version = text.strip() if text else "-"
        self.bridge_fw_label.configure(text=self.bridge_fw_version)
        self._refresh_statistics_display()

    def _can_send_bridge_commands(self) -> bool:
        if not self.serial_port or not self.serial_port.is_open:
            messagebox.showwarning("Nicht verbunden", "Bitte zuerst verbinden.")
            return False

        if not bool(self.dtr_switch.get()):
            messagebox.showwarning("DTR inaktiv", "Kommandos duerfen nur bei aktivem DTR gesendet werden.")
            return False

        return True

    def _send_bridge_command(self, command: str):
        if not self._can_send_bridge_commands():
            return
        self._write_serial_line(command)

    def _send_param(self, command: str):
        if not self._can_send_bridge_commands():
            return

        control = self.param_entries[command]["widget"]
        raw_value = control.get().strip()
        value = self._resolve_param_value(command, raw_value)
        if not value:
            messagebox.showwarning("Wert fehlt", f"Bitte Wert fuer {command} eintragen.")
            return

        if command in {"-rrx", "-rtx", "-krx", "-ktx"}:
            is_valid, error_message = self._validate_buffer_twos_complement(value)
            if not is_valid:
                messagebox.showwarning("Buffer-Wert", error_message)
                return

        cmd = f"{command} {value}"
        self._write_serial_line(cmd)
        if command == "-rbr":
            self.selected_rs232_baud = self._normalize_baud_value(value, self.DEFAULT_RS232_BAUD)
            self._save_app_config()

    def _resolve_param_value(self, command: str, raw_value: str) -> str:
        if not raw_value:
            return ""

        if command in {"-rrx", "-rtx", "-krx", "-ktx"}:
            if raw_value in self.buffer_value_map:
                return self.buffer_value_map[raw_value]
            return raw_value

        if command in {"-rbr", "-kbr", "-fwd"}:
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
        if not self._can_send_bridge_commands():
            return

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
        self.boot_status_label.configure(text="Bootloader: connecting...", text_color="#9a6700")

        if not self._write_serial_line("-r"):
            self.boot_connect_btn.configure(state="normal")
            self.boot_status_label.configure(text="Bootloader: connect failed", text_color="#cf222e")
            return

        self._log("Bootloader connect: reset command sent (-r).")
        self._disconnect_serial()
        threading.Thread(target=self._bootloader_connect_worker, args=(port, baud), daemon=True).start()

    def _bootloader_connect_worker(self, port: str, baud: int):
        time.sleep(0.8)

        try:
            boot_ser = serial.Serial(port=port, baudrate=baud, timeout=0.2, write_timeout=1.0)
        except serial.SerialException as exc:
            self.after(0, lambda: self._finish_bootloader_connect(False, "", f"Open failed: {exc}"))
            return

        ok, version_or_error = self._bootloader_handshake(boot_ser)
        if not ok:
            try:
                boot_ser.close()
            except Exception:
                pass
            self.after(0, lambda: self._finish_bootloader_connect(False, "", version_or_error))
            return

        self.bootloader_serial = boot_ser
        self.after(0, lambda: self._finish_bootloader_connect(True, version_or_error, ""))

    def _finish_bootloader_connect(self, connected: bool, version: str, error_text: str):
        self.boot_connect_btn.configure(state="normal")
        if connected:
            self.bootloader_ready = True
            self.bootloader_version = version
            self.boot_status_label.configure(text="Bootloader: connected", text_color="#2e8b57")
            self.boot_info_label.configure(text=f"Version: {version}")
            self._log(f"Bootloader connected ({version}).")
        else:
            self.bootloader_ready = False
            self.bootloader_version = ""
            self.boot_status_label.configure(text="Bootloader: connect failed", text_color="#cf222e")
            self.boot_info_label.configure(text="Version: -")
            self._log(f"Bootloader connect failed: {error_text}")
        self._refresh_statistics_display()

    def _bootloader_handshake(self, boot_ser: serial.Serial):
        deadline = time.time() + 8.0
        try:
            boot_ser.reset_input_buffer()
            boot_ser.reset_output_buffer()
        except Exception:
            pass

        while time.time() < deadline:
            try:
                boot_ser.write(b"U")
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

        return False, "Timeout waiting for bootloader identifier"

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

        cmd = b"pf\n" if mode == "firmware" else b"pe\n"
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
            ser.write(b"g\n")
            ser.flush()
            self._log("Bootloader command sent: g (Start Application)")
        except serial.SerialException as exc:
            self._log(f"Start application failed: {exc}")
            return

        self._close_bootloader_serial()
        self.bootloader_ready = False
        self.boot_status_label.configure(text="Bootloader: not connected", text_color="#9a6700")
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
