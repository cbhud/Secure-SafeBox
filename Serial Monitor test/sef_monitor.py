"""
╔══════════════════════════════════════════════════════════╗
║           PAMETNI SEF — Kontrolna Tabla                  ║
║         Python GUI Monitor za Arduino projekat           ║
╚══════════════════════════════════════════════════════════╝
Zahtijeva: pip install pyserial
Pokreni:   python sef_monitor.py
"""

import tkinter as tk
from tkinter import ttk, font as tkfont
import serial
import serial.tools.list_ports
import threading
import time
from datetime import datetime

# ──────────────────── BOJE I DIZAJN ────────────────────
COLORS = {
    "bg_dark":       "#0d1117",
    "bg_panel":      "#161b22",
    "bg_card":       "#1c2333",
    "bg_input":      "#0d1117",
    "border":        "#30363d",
    "border_accent": "#58a6ff",
    "text":          "#e6edf3",
    "text_dim":      "#8b949e",
    "text_muted":    "#484f58",
    "accent_blue":   "#58a6ff",
    "accent_green":  "#3fb950",
    "accent_red":    "#f85149",
    "accent_orange": "#d29922",
    "accent_purple": "#bc8cff",
    "accent_cyan":   "#39d2c0",
    "log_alarm":     "#ff7b72",
    "log_success":   "#7ee787",
    "log_info":      "#79c0ff",
    "log_system":    "#d2a8ff",
    "log_warning":   "#e3b341",
    "glow_green":    "#238636",
    "glow_red":      "#da3633",
}


class StatusIndicator(tk.Canvas):
    """Animirani kružni indikator statusa sa glow efektom."""

    def __init__(self, parent, size=14, color_on="#3fb950", color_off="#484f58", **kw):
        super().__init__(parent, width=size + 6, height=size + 6,
                         bg=COLORS["bg_card"], highlightthickness=0, **kw)
        self.size = size
        self.color_on = color_on
        self.color_off = color_off
        self._state = False
        self._pulse_step = 0
        self._pulse_job = None
        self._draw(color_off)

    def _draw(self, color, glow=False):
        self.delete("all")
        cx, cy = (self.size + 6) // 2, (self.size + 6) // 2
        r = self.size // 2
        if glow:
            # Glow efekat — poluprovidan krug iza glavnog
            self.create_oval(cx - r - 3, cy - r - 3, cx + r + 3, cy + r + 3,
                             fill="", outline=color, width=1)
        self.create_oval(cx - r, cy - r, cx + r, cy + r,
                         fill=color, outline="", width=0)

    def set_on(self, pulse=False):
        self._state = True
        self._draw(self.color_on, glow=True)
        if pulse:
            self._start_pulse()

    def set_off(self):
        self._state = False
        self._stop_pulse()
        self._draw(self.color_off)

    def _start_pulse(self):
        self._stop_pulse()
        self._pulse_step = 0
        self._pulse()

    def _stop_pulse(self):
        if self._pulse_job:
            self.after_cancel(self._pulse_job)
            self._pulse_job = None

    def _pulse(self):
        self._pulse_step += 1
        visible = self._pulse_step % 2 == 0
        if visible:
            self._draw(self.color_on, glow=True)
        else:
            self._draw(self.color_off)
        self._pulse_job = self.after(500, self._pulse)


class SefMonitor:
    def __init__(self, root):
        self.root = root
        self.root.title("Pametni Sef — Kontrolna Tabla")
        self.root.configure(bg=COLORS["bg_dark"])
        self.root.minsize(880, 640)
        self.root.geometry("960x720")

        # Ikona prozora (unicode fallback)
        try:
            self.root.iconname("🔒")
        except Exception:
            pass

        self.serial_conn = None
        self.connected = False
        self.read_thread = None
        self.stop_event = threading.Event()

        # Stanje sistema
        self.sef_otkljucan = False
        self.alarm_aktivan = False
        self.sms_cooldown = False
        self.log_count = 0

        self._build_fonts()
        self._build_ui()
        self._refresh_ports()

    # ───────── FONTOVI ─────────
    def _build_fonts(self):
        self.font_title = tkfont.Font(family="Segoe UI", size=16, weight="bold")
        self.font_heading = tkfont.Font(family="Segoe UI", size=11, weight="bold")
        self.font_body = tkfont.Font(family="Segoe UI", size=10)
        self.font_mono = tkfont.Font(family="Consolas", size=10)
        self.font_mono_small = tkfont.Font(family="Consolas", size=9)
        self.font_status = tkfont.Font(family="Segoe UI", size=10, weight="bold")
        self.font_big_icon = tkfont.Font(family="Segoe UI", size=28)

    # ───────── UI KONSTRUKCIJA ─────────
    def _build_ui(self):
        # Glavni kontejner
        main = tk.Frame(self.root, bg=COLORS["bg_dark"])
        main.pack(fill="both", expand=True, padx=12, pady=12)

        # ─── HEADER ───
        header = tk.Frame(main, bg=COLORS["bg_dark"])
        header.pack(fill="x", pady=(0, 10))

        tk.Label(header, text="🔐", font=self.font_big_icon,
                 bg=COLORS["bg_dark"], fg=COLORS["accent_blue"]).pack(side="left", padx=(0, 8))

        htext = tk.Frame(header, bg=COLORS["bg_dark"])
        htext.pack(side="left")
        tk.Label(htext, text="PAMETNI SEF", font=self.font_title,
                 bg=COLORS["bg_dark"], fg=COLORS["text"]).pack(anchor="w")
        tk.Label(htext, text="Kontrolna tabla  —  Arduino serijski monitor",
                 font=self.font_body, bg=COLORS["bg_dark"],
                 fg=COLORS["text_dim"]).pack(anchor="w")

        # Konekcija (desna strana headera)
        conn_frame = tk.Frame(header, bg=COLORS["bg_dark"])
        conn_frame.pack(side="right")
        self._build_connection_panel(conn_frame)

        # ─── SREDNJI DIO: Status kartice + Log ───
        middle = tk.Frame(main, bg=COLORS["bg_dark"])
        middle.pack(fill="both", expand=True)

        # Lijeva kolona — Status kartice
        left_col = tk.Frame(middle, bg=COLORS["bg_dark"], width=260)
        left_col.pack(side="left", fill="y", padx=(0, 10))
        left_col.pack_propagate(False)
        self._build_status_cards(left_col)

        # Desna kolona — Log
        right_col = tk.Frame(middle, bg=COLORS["bg_dark"])
        right_col.pack(side="left", fill="both", expand=True)
        self._build_log_panel(right_col)

        # ─── FOOTER: Komandni unos ───
        self._build_command_bar(main)

    def _build_connection_panel(self, parent):
        frame = tk.Frame(parent, bg=COLORS["bg_panel"], highlightbackground=COLORS["border"],
                         highlightthickness=1, padx=10, pady=6)
        frame.pack()

        # COM port izbor
        tk.Label(frame, text="PORT", font=self.font_mono_small,
                 bg=COLORS["bg_panel"], fg=COLORS["text_dim"]).pack(side="left", padx=(0, 4))

        self.port_var = tk.StringVar()
        self.port_combo = ttk.Combobox(frame, textvariable=self.port_var,
                                        width=10, state="readonly",
                                        font=self.font_mono_small)
        self.port_combo.pack(side="left", padx=(0, 6))

        # Refresh dugme
        ref_btn = tk.Label(frame, text="⟳", font=self.font_body,
                           bg=COLORS["bg_panel"], fg=COLORS["accent_blue"], cursor="hand2")
        ref_btn.pack(side="left", padx=(0, 8))
        ref_btn.bind("<Button-1>", lambda e: self._refresh_ports())

        # Baud rate
        tk.Label(frame, text="BAUD", font=self.font_mono_small,
                 bg=COLORS["bg_panel"], fg=COLORS["text_dim"]).pack(side="left", padx=(0, 4))
        self.baud_var = tk.StringVar(value="115200")
        baud_entry = tk.Entry(frame, textvariable=self.baud_var, width=7,
                              font=self.font_mono_small, bg=COLORS["bg_input"],
                              fg=COLORS["text"], insertbackground=COLORS["text"],
                              relief="flat", highlightbackground=COLORS["border"],
                              highlightthickness=1)
        baud_entry.pack(side="left", padx=(0, 10))

        # Konekcija dugme
        self.connect_btn = tk.Label(frame, text="  POVEŽI  ", font=self.font_status,
                                    bg=COLORS["accent_blue"], fg="#ffffff",
                                    cursor="hand2", padx=10, pady=2)
        self.connect_btn.pack(side="left")
        self.connect_btn.bind("<Button-1>", lambda e: self._toggle_connection())

    def _build_status_cards(self, parent):
        tk.Label(parent, text="STATUS SISTEMA", font=self.font_heading,
                 bg=COLORS["bg_dark"], fg=COLORS["text_dim"]).pack(anchor="w", pady=(0, 8))

        # ── Kartica: Konekcija ──
        self.card_conn = self._make_card(parent, "SERIJSKA VEZA", "Nije spojeno",
                                          "⚡", COLORS["text_muted"])
        # ── Kartica: Brava ──
        self.card_lock = self._make_card(parent, "BRAVA", "Zaključano",
                                          "🔒", COLORS["accent_green"])
        # ── Kartica: Alarm ──
        self.card_alarm = self._make_card(parent, "ALARM", "Neaktivan",
                                           "🔔", COLORS["text_muted"])
        # ── Kartica: SMS ──
        self.card_sms = self._make_card(parent, "SMS STATUS", "Spreman",
                                         "✉", COLORS["accent_green"])
        # ── Kartica: Senzor ──
        self.card_sensor = self._make_card(parent, "ULTRAZVUČNI", "Monitoring...",
                                            "📡", COLORS["accent_cyan"])

        # Statistika
        stats_frame = self._card_frame(parent)
        tk.Label(stats_frame, text="STATISTIKA SESIJE", font=self.font_mono_small,
                 bg=COLORS["bg_card"], fg=COLORS["text_dim"]).pack(anchor="w", pady=(0, 4))
        self.lbl_log_count = tk.Label(stats_frame, text="Poruke: 0", font=self.font_body,
                                       bg=COLORS["bg_card"], fg=COLORS["text"])
        self.lbl_log_count.pack(anchor="w")
        self.lbl_uptime = tk.Label(stats_frame, text="Vrijeme: --:--:--", font=self.font_body,
                                    bg=COLORS["bg_card"], fg=COLORS["text"])
        self.lbl_uptime.pack(anchor="w")
        self.start_time = None
        self._update_uptime()

    def _card_frame(self, parent):
        """Napravi stilizovani okvir kartice."""
        card = tk.Frame(parent, bg=COLORS["bg_card"],
                        highlightbackground=COLORS["border"],
                        highlightthickness=1, padx=12, pady=10)
        card.pack(fill="x", pady=(0, 6))
        return card

    def _make_card(self, parent, title, status_text, icon, icon_color):
        card = self._card_frame(parent)

        top = tk.Frame(card, bg=COLORS["bg_card"])
        top.pack(fill="x")

        tk.Label(top, text=icon, font=self.font_body,
                 bg=COLORS["bg_card"], fg=icon_color).pack(side="left", padx=(0, 6))
        tk.Label(top, text=title, font=self.font_mono_small,
                 bg=COLORS["bg_card"], fg=COLORS["text_dim"]).pack(side="left")

        indicator = StatusIndicator(top, size=10,
                                     color_on=COLORS["accent_green"],
                                     color_off=COLORS["text_muted"])
        indicator.pack(side="right")

        status_lbl = tk.Label(card, text=status_text, font=self.font_status,
                               bg=COLORS["bg_card"], fg=COLORS["text"], anchor="w")
        status_lbl.pack(fill="x", pady=(4, 0))

        return {"frame": card, "indicator": indicator, "status": status_lbl, "icon_label": top.winfo_children()[0]}

    def _build_log_panel(self, parent):
        # Naslov loga
        log_header = tk.Frame(parent, bg=COLORS["bg_dark"])
        log_header.pack(fill="x", pady=(0, 6))

        tk.Label(log_header, text="SERIJSKI LOG", font=self.font_heading,
                 bg=COLORS["bg_dark"], fg=COLORS["text_dim"]).pack(side="left")

        clear_btn = tk.Label(log_header, text="✕ Obriši", font=self.font_mono_small,
                             bg=COLORS["bg_dark"], fg=COLORS["accent_red"], cursor="hand2")
        clear_btn.pack(side="right")
        clear_btn.bind("<Button-1>", lambda e: self._clear_log())

        # Log text widget
        log_frame = tk.Frame(parent, bg=COLORS["border"], highlightthickness=0)
        log_frame.pack(fill="both", expand=True)

        self.log_text = tk.Text(log_frame, bg=COLORS["bg_panel"], fg=COLORS["text"],
                                font=self.font_mono, relief="flat", wrap="word",
                                insertbackground=COLORS["text"], padx=12, pady=8,
                                spacing1=2, state="disabled", cursor="arrow",
                                selectbackground=COLORS["accent_blue"],
                                selectforeground="#ffffff")
        self.log_text.pack(side="left", fill="both", expand=True, padx=1, pady=1)

        scrollbar = tk.Scrollbar(log_frame, command=self.log_text.yview,
                                  bg=COLORS["bg_panel"], troughcolor=COLORS["bg_dark"],
                                  highlightthickness=0, relief="flat")
        scrollbar.pack(side="right", fill="y")
        self.log_text.config(yscrollcommand=scrollbar.set)

        # Tagovi za boje
        self.log_text.tag_configure("alarm", foreground=COLORS["log_alarm"])
        self.log_text.tag_configure("success", foreground=COLORS["log_success"])
        self.log_text.tag_configure("info", foreground=COLORS["log_info"])
        self.log_text.tag_configure("system", foreground=COLORS["log_system"])
        self.log_text.tag_configure("warning", foreground=COLORS["log_warning"])
        self.log_text.tag_configure("dim", foreground=COLORS["text_dim"])
        self.log_text.tag_configure("timestamp", foreground=COLORS["text_muted"])

    def _build_command_bar(self, parent):
        bar = tk.Frame(parent, bg=COLORS["bg_panel"], highlightbackground=COLORS["border"],
                       highlightthickness=1, padx=10, pady=8)
        bar.pack(fill="x", pady=(10, 0))

        tk.Label(bar, text="❯", font=self.font_body,
                 bg=COLORS["bg_panel"], fg=COLORS["accent_blue"]).pack(side="left", padx=(0, 6))

        self.cmd_entry = tk.Entry(bar, font=self.font_mono, bg=COLORS["bg_input"],
                                   fg=COLORS["text"], insertbackground=COLORS["text"],
                                   relief="flat", highlightbackground=COLORS["border"],
                                   highlightthickness=1)
        self.cmd_entry.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self.cmd_entry.bind("<Return>", self._send_command)
        self.cmd_entry.insert(0, "Pošalji komandu na Arduino...")
        self.cmd_entry.config(fg=COLORS["text_muted"])
        self.cmd_entry.bind("<FocusIn>", self._cmd_focus_in)
        self.cmd_entry.bind("<FocusOut>", self._cmd_focus_out)

        send_btn = tk.Label(bar, text="  POŠALJI  ", font=self.font_status,
                            bg=COLORS["accent_blue"], fg="#ffffff", cursor="hand2",
                            padx=8, pady=2)
        send_btn.pack(side="right")
        send_btn.bind("<Button-1>", self._send_command)

    # ───────── PLACEHOLDER LOGIKA ZA KOMANDNI UNOS ─────────
    def _cmd_focus_in(self, event):
        if self.cmd_entry.get() == "Pošalji komandu na Arduino...":
            self.cmd_entry.delete(0, "end")
            self.cmd_entry.config(fg=COLORS["text"])

    def _cmd_focus_out(self, event):
        if not self.cmd_entry.get():
            self.cmd_entry.insert(0, "Pošalji komandu na Arduino...")
            self.cmd_entry.config(fg=COLORS["text_muted"])

    # ───────── SERIJSKA KOMUNIKACIJA ─────────
    def _refresh_ports(self):
        ports = [p.device for p in serial.tools.list_ports.comports()]
        self.port_combo["values"] = ports
        if ports:
            self.port_combo.current(0)

    def _toggle_connection(self):
        if self.connected:
            self._disconnect()
        else:
            self._connect()

    def _connect(self):
        port = self.port_var.get()
        baud = int(self.baud_var.get())
        if not port:
            self._log_message("[GREŠKA] Izaberi COM port!", "alarm")
            return
        try:
            self.serial_conn = serial.Serial(port, baud, timeout=0.1)
            self.connected = True
            self.stop_event.clear()
            self.start_time = time.time()

            self.connect_btn.config(text="  PREKINI  ", bg=COLORS["accent_red"])
            self.card_conn["status"].config(text=f"Spojeno ({port})", fg=COLORS["accent_green"])
            self.card_conn["indicator"].set_on()
            self._log_message(f"Spojeno na {port} @ {baud} baud", "success")

            # Pokreni thread za čitanje
            self.read_thread = threading.Thread(target=self._read_serial, daemon=True)
            self.read_thread.start()
        except Exception as e:
            self._log_message(f"[GREŠKA] {str(e)}", "alarm")

    def _disconnect(self):
        self.connected = False
        self.stop_event.set()
        if self.serial_conn and self.serial_conn.is_open:
            self.serial_conn.close()
        self.connect_btn.config(text="  POVEŽI  ", bg=COLORS["accent_blue"])
        self.card_conn["status"].config(text="Nije spojeno", fg=COLORS["text_muted"])
        self.card_conn["indicator"].set_off()
        self._log_message("Veza prekinuta.", "warning")

    def _read_serial(self):
        """Čita podatke sa serijskog porta u pozadinskom threadu."""
        buffer = ""
        while not self.stop_event.is_set():
            try:
                if self.serial_conn and self.serial_conn.is_open and self.serial_conn.in_waiting:
                    raw = self.serial_conn.read(self.serial_conn.in_waiting)
                    text = raw.decode("utf-8", errors="replace")
                    buffer += text

                    # Procesiraj kompletne linije
                    while "\n" in buffer:
                        line, buffer = buffer.split("\n", 1)
                        line = line.strip()
                        if line:
                            self.root.after(0, self._process_line, line)
                else:
                    time.sleep(0.05)
            except Exception:
                if not self.stop_event.is_set():
                    self.root.after(0, self._disconnect)
                break

    def _process_line(self, line):
        """Parsira liniju sa Arduina i ažurira GUI."""
        tag = "dim"

        # ── ALARM detekcija ──
        if "[ALARM]" in line:
            tag = "alarm"
            self.alarm_aktivan = True
            self.card_alarm["status"].config(text="⚠ AKTIVAN!", fg=COLORS["accent_red"])
            self.card_alarm["indicator"].color_on = COLORS["accent_red"]
            self.card_alarm["indicator"].set_on(pulse=True)
            self.card_alarm["icon_label"].config(fg=COLORS["accent_red"])
            self.root.after(5000, self._reset_alarm_card)

        # ── SMS detekcija ──
        elif "[Sistem]: SMS" in line or "SMS komanda" in line:
            tag = "system"
            self.card_sms["status"].config(text="✓ SMS poslat", fg=COLORS["accent_green"])
            self.card_sms["indicator"].set_on()

        elif "[SMS] Pocinjem" in line:
            tag = "system"
            self.card_sms["status"].config(text="📤 Šalje se...", fg=COLORS["accent_blue"])
            self.card_sms["indicator"].color_on = COLORS["accent_blue"]
            self.card_sms["indicator"].set_on(pulse=True)

        elif "[SMS Cooldown]" in line:
            tag = "warning"
            self.card_sms["status"].config(text="⏳ Cooldown aktivan", fg=COLORS["accent_orange"])
            self.card_sms["indicator"].color_on = COLORS["accent_orange"]
            self.card_sms["indicator"].set_on()

        elif "[SMS] Vec se salje" in line:
            tag = "warning"

        # ── Servo / brava stanja ──
        elif "Otvaram bravu" in line:
            tag = "success"
            self.sef_otkljucan = True
            self.card_lock["status"].config(text="🔓 Otvara se...", fg=COLORS["accent_orange"])
            self.card_lock["indicator"].color_on = COLORS["accent_orange"]
            self.card_lock["indicator"].set_on(pulse=True)
            self.card_lock["icon_label"].config(text="🔓", fg=COLORS["accent_orange"])

        elif "Sef je otvoren" in line or "Zatvaranje za" in line:
            tag = "success"
            self.card_lock["status"].config(text="🔓 Otvoren (15s)", fg=COLORS["accent_green"])

        elif "Zakljucavam bravu" in line:
            tag = "info"
            self.card_lock["status"].config(text="🔒 Zatvara se...", fg=COLORS["accent_orange"])
            self.card_lock["indicator"].set_on(pulse=True)

        elif "ponovo zakljucan" in line:
            tag = "info"
            self.sef_otkljucan = False
            self.card_lock["status"].config(text="Zaključano", fg=COLORS["accent_green"])
            self.card_lock["indicator"].color_on = COLORS["accent_green"]
            self.card_lock["indicator"].set_on()
            self.card_lock["icon_label"].config(text="🔒", fg=COLORS["accent_green"])

        # ── RFID / PIN ──
        elif "ODOBRENO" in line:
            tag = "success"
        elif "ODBIJEN" in line or "Pogresan PIN" in line:
            tag = "alarm"
        elif "PIN tacan" in line:
            tag = "success"

        # ── Sistemske poruke ──
        elif "SIGURNOSNI SEF SPREMAN" in line:
            tag = "success"
            self.card_lock["indicator"].set_on()
        elif "Cekam" in line or "GSM" in line or "Pokretanje" in line:
            tag = "system"
        elif "Pritisnuto" in line:
            tag = "info"
        elif "Narusavanje distance" in line or "Distanca:" in line:
            tag = "warning"
            self.card_sensor["status"].config(text="⚠ Objekat detektovan!", fg=COLORS["accent_orange"])

        self._log_message(line, tag)

    def _reset_alarm_card(self):
        if not self.alarm_aktivan:
            return
        self.alarm_aktivan = False
        self.card_alarm["status"].config(text="Neaktivan", fg=COLORS["text_muted"])
        self.card_alarm["indicator"].set_off()
        self.card_alarm["icon_label"].config(fg=COLORS["text_muted"])
        self.card_sensor["status"].config(text="Monitoring...", fg=COLORS["accent_cyan"])

    # ───────── LOG FUNKCIJE ─────────
    def _log_message(self, text, tag="dim"):
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.log_text.config(state="normal")
        self.log_text.insert("end", f"[{timestamp}] ", "timestamp")
        self.log_text.insert("end", text + "\n", tag)
        self.log_text.see("end")
        self.log_text.config(state="disabled")
        self.log_count += 1
        self.lbl_log_count.config(text=f"Poruke: {self.log_count}")

    def _clear_log(self):
        self.log_text.config(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.config(state="disabled")
        self.log_count = 0
        self.lbl_log_count.config(text="Poruke: 0")

    # ───────── KOMANDE ─────────
    def _send_command(self, event=None):
        cmd = self.cmd_entry.get().strip()
        if cmd and cmd != "Pošalji komandu na Arduino..." and self.connected:
            try:
                self.serial_conn.write((cmd + "\n").encode("utf-8"))
                self._log_message(f">>> {cmd}", "info")
            except Exception as e:
                self._log_message(f"[GREŠKA] Slanje neuspješno: {e}", "alarm")
            self.cmd_entry.delete(0, "end")

    # ───────── UPTIME BROJAČ ─────────
    def _update_uptime(self):
        if self.start_time and self.connected:
            elapsed = int(time.time() - self.start_time)
            h, m, s = elapsed // 3600, (elapsed % 3600) // 60, elapsed % 60
            self.lbl_uptime.config(text=f"Vrijeme: {h:02d}:{m:02d}:{s:02d}")
        self.root.after(1000, self._update_uptime)

    def on_closing(self):
        self.stop_event.set()
        if self.serial_conn and self.serial_conn.is_open:
            self.serial_conn.close()
        self.root.destroy()


# ───────── POKRETANJE ─────────
if __name__ == "__main__":
    root = tk.Tk()
    app = SefMonitor(root)
    root.protocol("WM_DELETE_WINDOW", app.on_closing)
    root.mainloop()
