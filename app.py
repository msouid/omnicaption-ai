"""
OmniCaption AI - Interactive Windows Live Audio Transcriber
Features:
- Full Studio Window (Search, Copy, Export SRT/TXT, Font size)
- Floating Desktop Transcript Box (Multi-line rolling conversation widget over all apps)
- Floating Subtitle Bar (Compact movie / video subtitle mode)
- Smart VAD Silence Gate (Minimizes API usage and costs)
- <150ms real-time latency with 48kHz studio audio
"""

import sys
import os
import time
import re
import tkinter as tk
from tkinter import filedialog, messagebox
import customtkinter as ctk

if sys.stdout and hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

from engine import AudioTranscriptionEngine, LANGUAGE_MAP

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

SPEAKER_COLORS = {
    1: "#00E5FF",  # Vibrant Cyan for Person 1
    2: "#FFD54F",  # Warm Amber / Gold for Person 2
    3: "#69F0AE",  # Mint Green for Person 3
    4: "#FF5252",  # Coral Red for Person 4
    5: "#E040FB",  # Electric Purple for Person 5
}


def format_subtitle_display(text, max_chars=130):
    """
    Ensures live subtitle text fits cleanly on 1-2 lines without vertical cutoffs.
    If text contains multiple finished sentences, extracts the latest active thought.
    """
    if not text:
        return text
    text = text.strip()
    if len(text) <= max_chars:
        return text

    # Try to split by sentence boundaries (. ! ? ؛ ،)
    sentences = re.split(r'(?<=[.!?؛؟])\s+', text)
    if len(sentences) > 1:
        accum = []
        curr_len = 0
        for s in reversed(sentences):
            if not s:
                continue
            if curr_len + len(s) <= max_chars or not accum:
                accum.insert(0, s)
                curr_len += len(s)
            else:
                break
        res = " ".join(accum).strip()
        if res:
            return res

    # Word boundary fallback
    words = text.split()
    accum_words = []
    curr_len = 0
    for w in reversed(words):
        if curr_len + len(w) + 1 <= max_chars or not accum_words:
            accum_words.insert(0, w)
            curr_len += len(w) + 1
        else:
            break
    return " ".join(accum_words).strip()


class FloatingDesktopTranscriptBox(ctk.CTkToplevel):
    """
    Independent, resizable, floating desktop transcript box that stays
    ALWAYS ON TOP of any Windows application (WhatsApp, Zoom, Chrome, YouTube, VLC).
    """
    def __init__(self, master, on_restore_callback):
        super().__init__(master)
        self.on_restore_callback = on_restore_callback

        self.title("OmniCaption Floating Widget")
        self.attributes("-topmost", True)
        self.attributes("-alpha", 0.90)
        self.overrideredirect(True)

        self.is_box_mode = False  # Default: Compact Subtitle Bar Mode (Zero history clutter!)
        self.current_opacity = 0.90

        # Position on screen (default: bottom-center for Bar, bottom-right for Box)
        sw = self.winfo_screenwidth()
        sh = self.winfo_screenheight()
        self.box_geom = (540, 300, sw - 580, sh - 370)
        self.bar_geom = (960, 130, (sw - 960) // 2, sh - 180)

        bw, bh, bx, by = self.bar_geom
        self.geometry(f"{bw}x{bh}+{bx}+{by}")
        self.configure(fg_color="#0F1216")

        # Root Card Frame
        self.card = ctk.CTkFrame(self, fg_color="#0F1216", corner_radius=12, border_width=1, border_color="#263238")
        self.card.pack(fill="both", expand=True, padx=2, pady=2)

        # 1. Draggable Header Bar
        self.header = ctk.CTkFrame(self.card, fg_color="#181D24", height=32, corner_radius=10)
        self.header.pack(fill="x", padx=4, pady=(4, 2))

        self.title_lbl = ctk.CTkLabel(
            self.header,
            text="● Live Subtitles",
            font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold"),
            text_color="#00E5FF"
        )
        self.title_lbl.pack(side="left", padx=8)

        # Expand to Studio button
        self.btn_expand = ctk.CTkButton(
            self.header,
            text="⤢ Studio",
            width=65,
            height=22,
            font=ctk.CTkFont(size=11),
            fg_color="#263238",
            hover_color="#37474F",
            command=self.restore_studio
        )
        self.btn_expand.pack(side="right", padx=(2, 6), pady=4)

        # Mode toggle button (Box vs Bar)
        self.btn_mode = ctk.CTkButton(
            self.header,
            text="⊞ Box Mode",
            width=75,
            height=22,
            font=ctk.CTkFont(size=11),
            fg_color="#263238",
            hover_color="#37474F",
            command=self.toggle_mode
        )
        self.btn_mode.pack(side="right", padx=2, pady=4)

        # Opacity button
        self.btn_opacity = ctk.CTkButton(
            self.header,
            text="👁 90%",
            width=50,
            height=22,
            font=ctk.CTkFont(size=11),
            fg_color="#263238",
            hover_color="#37474F",
            command=self.cycle_opacity
        )
        self.btn_opacity.pack(side="right", padx=2, pady=4)

        # 2. Multi-line History Box (Only shown if user explicitly switches to Box Mode)
        self.txt_feed = ctk.CTkTextbox(
            self.card,
            font=ctk.CTkFont(family="Segoe UI", size=14),
            fg_color="#0A0D10",
            text_color="#ECEFF1",
            corner_radius=8,
            border_width=0
        )
        # Not packed initially (Bar mode active by default)

        # Configure syntax tags for speaker diarization
        tb = self.txt_feed._textbox
        tb.tag_config("ts", foreground="#78909C")
        tb.tag_config("speech", foreground="#ECEFF1")
        for spk_id, hex_color in SPEAKER_COLORS.items():
            tb.tag_config(f"spk_{spk_id}", foreground=hex_color, font=("Segoe UI", 13, "bold"))

        # 3. Live In-Progress Speech Banner
        self.live_frame = ctk.CTkFrame(self.card, fg_color="#141920", corner_radius=8)
        self.live_frame.pack(fill="both", expand=True, padx=6, pady=(0, 4))

        self.lbl_live = ctk.CTkLabel(
            self.live_frame,
            text="... في انتظار الصوت ...",
            font=ctk.CTkFont(family="Segoe UI", size=16, weight="bold"),
            text_color="#00E5FF",
            wraplength=bw - 40,
            justify="center"
        )
        self.lbl_live.pack(fill="both", expand=True, padx=8, pady=4)

        # Dragging bindings
        for w_elem in [self.header, self.title_lbl]:
            w_elem.bind("<ButtonPress-1>", self._start_move)
            w_elem.bind("<B1-Motion>", self._do_move)

    def _start_move(self, event):
        self.x = event.x
        self.y = event.y

    def _do_move(self, event):
        dx = event.x - self.x
        dy = event.y - self.y
        self.geometry(f"+{self.winfo_x() + dx}+{self.winfo_y() + dy}")

    def toggle_mode(self):
        sw = self.winfo_screenwidth()
        sh = self.winfo_screenheight()
        if self.is_box_mode:
            # Switch to Compact Bar Mode
            self.is_box_mode = False
            self.btn_mode.configure(text="⊞ Box Mode")
            self.txt_feed.pack_forget()
            bw, bh, bx, by = self.bar_geom
            self.geometry(f"{bw}x{bh}+{bx}+{by}")
            self.lbl_live.configure(font=ctk.CTkFont(family="Segoe UI", size=16, weight="bold"), text_color="#00E5FF", wraplength=bw - 40)
        else:
            # Switch to Multi-line Box Mode
            self.is_box_mode = True
            self.btn_mode.configure(text="⊟ Bar Mode")
            bw, bh, bx, by = self.box_geom
            self.geometry(f"{bw}x{bh}+{bx}+{by}")
            self.txt_feed.pack(fill="both", expand=True, padx=6, pady=4)
            self.lbl_live.configure(font=ctk.CTkFont(family="Segoe UI", size=14, weight="bold"), text_color="#FFD54F", wraplength=bw - 40)

    def cycle_opacity(self):
        if self.current_opacity >= 0.95:
            self.current_opacity = 0.65
        elif self.current_opacity >= 0.85:
            self.current_opacity = 0.98
        else:
            self.current_opacity = 0.85

        self.attributes("-alpha", self.current_opacity)
        pct = int(self.current_opacity * 100)
        self.btn_opacity.configure(text=f"👁 {pct}%")

    def update_draft(self, text, speaker=None, lang_code="ar-tn"):
        if not text:
            return
        spk_tag = ""
        color = "#FFD54F" if self.is_box_mode else "#00E5FF"
        if speaker is not None:
            color = SPEAKER_COLORS.get(speaker, "#00E5FF")
            if "ar" in lang_code:
                spk_tag = f"[شخص {speaker}] "
            elif "fr" in lang_code:
                spk_tag = f"[Personne {speaker}] "
            else:
                spk_tag = f"[Person {speaker}] "

        display_text = text if self.is_box_mode else format_subtitle_display(text)
        self.lbl_live.configure(text=f"{spk_tag}{display_text}", text_color=color)

    def append_final(self, timestamp, sentence, speaker=None, lang_code="ar-tn"):
        spk_tag = ""
        color = "#ECEFF1"
        if speaker is not None:
            color = SPEAKER_COLORS.get(speaker, "#FFFFFF")
            if "ar" in lang_code:
                spk_tag = f"[شخص {speaker}] "
            elif "fr" in lang_code:
                spk_tag = f"[Personne {speaker}] "
            else:
                spk_tag = f"[Person {speaker}] "

        display_text = sentence if self.is_box_mode else format_subtitle_display(sentence)
        self.lbl_live.configure(text=f"{spk_tag}{display_text}", text_color=color)
        if self.is_box_mode:
            tb = self.txt_feed._textbox
            tb.insert("end", f"[{timestamp}]  ", "ts")
            if speaker is not None:
                tb.insert("end", spk_tag, f"spk_{speaker}")
            tb.insert("end", f"{sentence}\n", "speech")
            self.txt_feed.see("end")

    def restore_studio(self):
        self.withdraw()
        if self.on_restore_callback:
            self.on_restore_callback()


class OmniCaptionApp(ctk.CTk):
    def __init__(self):
        super().__init__()

        self.title("OmniCaption AI - Windows Live Audio Transcriber")
        self.geometry("960x390")
        self.minsize(780, 340)

        self.engine = AudioTranscriptionEngine()
        self.engine.on_status_change = self._engine_status_callback
        self.engine.on_volume = self._engine_volume_callback
        self.engine.on_interim = self._engine_interim_callback
        self.engine.on_final = self._engine_final_callback
        self.engine.on_error = self._engine_error_callback

        self.floating_box = None
        self.transcript_records = []

        self._build_ui()

    def _build_ui(self):
        # 1. Header Frame
        header_frame = ctk.CTkFrame(self, fg_color="#181B20", corner_radius=10)
        header_frame.pack(fill="x", padx=16, pady=(14, 8))

        title_box = ctk.CTkFrame(header_frame, fg_color="transparent")
        title_box.pack(side="left", padx=16, pady=12)

        title_lbl = ctk.CTkLabel(
            title_box,
            text="OmniCaption AI",
            font=ctk.CTkFont(family="Segoe UI", size=22, weight="bold"),
            text_color="#00E5FF"
        )
        title_lbl.pack(anchor="w")

        sub_lbl = ctk.CTkLabel(
            title_box,
            text="Real-Time System Audio & Call Transcriber (WhatsApp, Zoom, YouTube, VLC, Browser)",
            font=ctk.CTkFont(family="Segoe UI", size=12),
            text_color="#90A4AE"
        )
        sub_lbl.pack(anchor="w")

        # Right status & VU box
        status_box = ctk.CTkFrame(header_frame, fg_color="transparent")
        status_box.pack(side="right", padx=16, pady=12)

        self.status_dot = ctk.CTkLabel(
            status_box,
            text="●",
            font=ctk.CTkFont(size=20),
            text_color="#78909C"
        )
        self.status_dot.pack(side="left", padx=(0, 6))

        self.status_lbl = ctk.CTkLabel(
            status_box,
            text="Ready (Stopped)",
            font=ctk.CTkFont(family="Segoe UI", size=13, weight="bold"),
            text_color="#CFD8DC"
        )
        self.status_lbl.pack(side="left", padx=(0, 12))

        self.vu_bar = ctk.CTkProgressBar(status_box, width=110, height=12)
        self.vu_bar.pack(side="left", padx=6)
        self.vu_bar.set(0.0)

        # 2. Control Bar Frame
        controls_frame = ctk.CTkFrame(self, fg_color="#1E232A", corner_radius=10)
        controls_frame.pack(fill="x", padx=16, pady=6)

        # Audio Source Selector
        lbl_src = ctk.CTkLabel(controls_frame, text="Audio Source:", font=ctk.CTkFont(size=12, weight="bold"))
        lbl_src.grid(row=0, column=0, padx=(14, 6), pady=12, sticky="w")

        self.source_var = ctk.StringVar(value="System Audio (Calls, Videos, YouTube)")
        self.combo_source = ctk.CTkComboBox(
            controls_frame,
            values=[
                "System Audio (Calls, Videos, YouTube)",
                "Microphone (My Voice)",
                "Calls Mode (System Audio + Microphone)"
            ],
            variable=self.source_var,
            width=280
        )
        self.combo_source.grid(row=0, column=1, padx=6, pady=12)

        # Language Selector
        lbl_lang = ctk.CTkLabel(controls_frame, text="Language / Dialect:", font=ctk.CTkFont(size=12, weight="bold"))
        lbl_lang.grid(row=0, column=2, padx=(16, 6), pady=12, sticky="w")

        self.lang_var = ctk.StringVar(value="Tunisian Darija (ar-TN)")
        self.combo_lang = ctk.CTkComboBox(
            controls_frame,
            values=list(LANGUAGE_MAP.keys()),
            variable=self.lang_var,
            width=210
        )
        self.combo_lang.grid(row=0, column=3, padx=6, pady=12)

        # Desktop Floating Box Button
        self.btn_overlay = ctk.CTkButton(
            controls_frame,
            text="🪟 Floating Desktop Box",
            fg_color="#37474F",
            hover_color="#455A64",
            width=160,
            command=self.open_floating_box
        )
        self.btn_overlay.grid(row=0, column=4, padx=12, pady=12)

        # Row 1: Speaker Diarization Switch + History Switch + Latency Info
        self.diarize_var = ctk.BooleanVar(value=True)
        self.switch_diarize = ctk.CTkSwitch(
            controls_frame,
            text="👥 Detect Speakers (Person 1 / 2)",
            variable=self.diarize_var,
            font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold"),
            progress_color="#00C853",
            button_color="#00E5FF",
            button_hover_color="#18FFFF"
        )
        self.switch_diarize.grid(row=1, column=0, columnspan=2, padx=(14, 6), pady=(0, 10), sticky="w")

        # History Switch - OFF by default (No saving/displaying history unless required!)
        self.keep_history_var = ctk.BooleanVar(value=False)
        self.switch_history = ctk.CTkSwitch(
            controls_frame,
            text="📜 Keep & Display History",
            variable=self.keep_history_var,
            font=ctk.CTkFont(family="Segoe UI", size=12, weight="bold"),
            progress_color="#00C853",
            button_color="#00E5FF",
            button_hover_color="#18FFFF",
            command=self._toggle_history_visibility
        )
        self.switch_history.grid(row=1, column=2, padx=(16, 6), pady=(0, 10), sticky="w")

        lbl_engine_info = ctk.CTkLabel(
            controls_frame,
            text="⚡ Ultra-Low Latency (<100ms) • Nova-3",
            font=ctk.CTkFont(family="Segoe UI", size=11),
            text_color="#64FFDA"
        )
        lbl_engine_info.grid(row=1, column=3, columnspan=2, padx=12, pady=(0, 10), sticky="e")

        # 3. Main Action Button Bar
        action_frame = ctk.CTkFrame(self, fg_color="transparent")
        action_frame.pack(fill="x", padx=16, pady=(4, 6))

        self.btn_toggle = ctk.CTkButton(
            action_frame,
            text="▶  START LIVE TRANSCRIPTION",
            font=ctk.CTkFont(family="Segoe UI", size=15, weight="bold"),
            fg_color="#00C853",
            hover_color="#00E676",
            height=42,
            command=self.toggle_transcription
        )
        self.btn_toggle.pack(fill="x", expand=True)

        # 4. Live Speech Banner (Active currently spoken sentence - Big, Clear & Prominent!)
        self.active_frame = ctk.CTkFrame(self, fg_color="#14181D", corner_radius=10, border_width=1, border_color="#00E5FF")
        self.active_frame.pack(fill="both", expand=True, padx=16, pady=6)

        active_title = ctk.CTkLabel(
            self.active_frame,
            text="ACTIVE LIVE SPEECH (الحديث المباشر الآن):",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color="#00E5FF"
        )
        active_title.pack(anchor="w", padx=14, pady=(6, 2))

        self.lbl_active = ctk.CTkLabel(
            self.active_frame,
            text="... في انتظار الصوت ...",
            font=ctk.CTkFont(family="Segoe UI", size=22, weight="bold"),
            text_color="#FFFFFF",
            wraplength=920,
            justify="center"
        )
        self.lbl_active.pack(fill="both", expand=True, padx=14, pady=(2, 10))

        # 5. History Panel (HIDDEN BY DEFAULT - Only displayed if user toggles 'Keep & Display History')
        self.history_panel = ctk.CTkFrame(self, fg_color="transparent")
        # Note: history_panel is NOT packed initially!

        hist_header = ctk.CTkFrame(self.history_panel, fg_color="transparent")
        hist_header.pack(fill="x", pady=(4, 2))

        hist_lbl = ctk.CTkLabel(
            hist_header,
            text="FULL TRANSCRIPT HISTORY (سجل الحوار الكامل):",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color="#B0BEC5"
        )
        hist_lbl.pack(side="left")

        font_slider_lbl = ctk.CTkLabel(hist_header, text="Font Size:", font=ctk.CTkFont(size=11), text_color="#78909C")
        font_slider_lbl.pack(side="right", padx=(6, 0))

        self.font_slider = ctk.CTkSlider(
            hist_header,
            from_=13,
            to=24,
            number_of_steps=11,
            width=110,
            command=self._change_font_size
        )
        self.font_slider.set(16)
        self.font_slider.pack(side="right", padx=4)

        self.txt_history = ctk.CTkTextbox(
            self.history_panel,
            font=ctk.CTkFont(family="Segoe UI", size=16),
            fg_color="#101317",
            text_color="#ECEFF1",
            corner_radius=10,
            border_width=1,
            border_color="#263238"
        )
        self.txt_history.pack(fill="both", expand=True, pady=4)

        # Configure syntax tags for speaker diarization
        tb_hist = self.txt_history._textbox
        tb_hist.tag_config("ts", foreground="#78909C")
        tb_hist.tag_config("speech", foreground="#ECEFF1")
        for spk_id, hex_color in SPEAKER_COLORS.items():
            tb_hist.tag_config(f"spk_{spk_id}", foreground=hex_color, font=("Segoe UI", 16, "bold"))

        # 6. Bottom Action Toolbar (Inside history_panel)
        toolbar = ctk.CTkFrame(self.history_panel, fg_color="#181B20", corner_radius=10)
        toolbar.pack(fill="x", pady=(4, 8))

        self.btn_copy = ctk.CTkButton(
            toolbar,
            text="📋 Copy All",
            fg_color="#263238",
            hover_color="#37474F",
            width=120,
            command=self.copy_transcript
        )
        self.btn_copy.pack(side="left", padx=8, pady=8)

        self.btn_save = ctk.CTkButton(
            toolbar,
            text="💾 Save Text (.txt)",
            fg_color="#263238",
            hover_color="#37474F",
            width=140,
            command=self.save_text
        )
        self.btn_save.pack(side="left", padx=8, pady=8)

        self.btn_srt = ctk.CTkButton(
            toolbar,
            text="🎬 Export Subtitles (.srt)",
            fg_color="#263238",
            hover_color="#37474F",
            width=160,
            command=self.export_srt
        )
        self.btn_srt.pack(side="left", padx=8, pady=8)

        self.btn_clear = ctk.CTkButton(
            toolbar,
            text="🧹 Clear",
            fg_color="#B71C1C",
            hover_color="#D32F2F",
            width=100,
            command=self.clear_transcript
        )
        self.btn_clear.pack(side="right", padx=8, pady=8)

    def _toggle_history_visibility(self):
        if self.keep_history_var.get():
            self.history_panel.pack(fill="both", expand=True, padx=16, pady=(0, 10))
            self.geometry("1040x730")
            self.lbl_active.configure(font=ctk.CTkFont(family="Segoe UI", size=18, weight="bold"))
        else:
            self.history_panel.pack_forget()
            self.geometry("960x390")
            self.lbl_active.configure(font=ctk.CTkFont(family="Segoe UI", size=22, weight="bold"))

    def _change_font_size(self, val):
        sz = int(val)
        self.txt_history.configure(font=ctk.CTkFont(family="Segoe UI", size=sz))
        tb_hist = self.txt_history._textbox
        for spk_id, hex_color in SPEAKER_COLORS.items():
            tb_hist.tag_config(f"spk_{spk_id}", foreground=hex_color, font=("Segoe UI", sz, "bold"))

    def toggle_transcription(self):
        if not self.engine.is_running:
            src_str = self.source_var.get()
            if "Microphone" in src_str:
                mode = "mic"
            elif "Calls Mode" in src_str:
                mode = "both"
            else:
                mode = "system"

            lang_str = self.lang_var.get()
            lang_code = LANGUAGE_MAP.get(lang_str, "ar-tn")
            enable_diarize = self.diarize_var.get()

            self.btn_toggle.configure(
                text="⏹  STOP TRANSCRIPTION",
                fg_color="#D50000",
                hover_color="#FF1744"
            )
            self.combo_source.configure(state="disabled")
            self.combo_lang.configure(state="disabled")
            self.switch_diarize.configure(state="disabled")
            self.switch_history.configure(state="disabled")

            self.engine.start(source_mode=mode, language_code=lang_code, enable_diarization=enable_diarize)
        else:
            self.engine.stop()
            self.btn_toggle.configure(
                text="▶  START LIVE TRANSCRIPTION",
                fg_color="#00C853",
                hover_color="#00E676"
            )
            self.combo_source.configure(state="normal")
            self.combo_lang.configure(state="normal")
            self.switch_diarize.configure(state="normal")
            self.switch_history.configure(state="normal")
            self.status_dot.configure(text_color="#78909C")
            self.status_lbl.configure(text="Stopped")
            self.vu_bar.set(0.0)

    def open_floating_box(self):
        if not self.floating_box or not self.floating_box.winfo_exists():
            self.floating_box = FloatingDesktopTranscriptBox(self, on_restore_callback=self._on_overlay_restore)
            # Replay recent history to floating box ONLY if history is enabled
            if self.keep_history_var.get():
                lang_str = self.lang_var.get()
                lang_code = LANGUAGE_MAP.get(lang_str, "ar-tn")
                for item in self.transcript_records[-20:]:
                    ts = item[0]
                    sentence = item[1]
                    spk = item[2] if len(item) > 2 else None
                    self.floating_box.append_final(ts, sentence, spk, lang_code)
        self.withdraw()
        self.floating_box.deiconify()

    def _on_overlay_restore(self):
        self.deiconify()

    # --- Engine Callbacks ---
    def _engine_status_callback(self, status, is_active):
        self.after(0, lambda: self._update_status_ui(status, is_active))

    def _update_status_ui(self, status, is_active):
        self.status_lbl.configure(text=status)
        if is_active:
            self.status_dot.configure(text_color="#00E5FF")
        else:
            self.status_dot.configure(text_color="#78909C")
            self.btn_toggle.configure(
                text="▶  START LIVE TRANSCRIPTION",
                fg_color="#00C853",
                hover_color="#00E676"
            )
            self.combo_source.configure(state="normal")
            self.combo_lang.configure(state="normal")
            self.switch_diarize.configure(state="normal")
            self.switch_history.configure(state="normal")

    def _engine_volume_callback(self, vol):
        now = time.time()
        if now - getattr(self, "_last_vu_time", 0) > 0.12:
            self._last_vu_time = now
            self.after(0, lambda: self.vu_bar.set(vol))

    def _engine_interim_callback(self, draft_txt, speaker_id=None):
        self.after(0, lambda: self._update_active_speech(draft_txt, speaker_id))

    def _update_active_speech(self, txt, speaker_id=None):
        if getattr(self, "_last_rendered_draft", None) == (txt, speaker_id):
            return
        self._last_rendered_draft = (txt, speaker_id)

        lang_str = self.lang_var.get()
        lang_code = LANGUAGE_MAP.get(lang_str, "ar-tn")
        spk_color = SPEAKER_COLORS.get(speaker_id, "#00E5FF")

        spk_prefix = ""
        if speaker_id is not None:
            if "ar" in lang_code:
                spk_prefix = f"[شخص {speaker_id}] "
            elif "fr" in lang_code:
                spk_prefix = f"[Personne {speaker_id}] "
            else:
                spk_prefix = f"[Person {speaker_id}] "

        self.lbl_active.configure(text=f"{spk_prefix}{txt}", text_color=spk_color)
        if self.floating_box and self.floating_box.winfo_exists():
            self.floating_box.update_draft(txt, speaker_id, lang_code)

    def _engine_final_callback(self, full_sentence, timestamp, speaker_id=None):
        self.after(0, lambda: self._append_final_speech(full_sentence, timestamp, speaker_id))

    def _append_final_speech(self, sentence, timestamp, speaker_id=None):
        self._last_rendered_draft = None
        lang_str = self.lang_var.get()
        lang_code = LANGUAGE_MAP.get(lang_str, "ar-tn")
        spk_color = SPEAKER_COLORS.get(speaker_id, "#FFFFFF")

        spk_prefix = ""
        if speaker_id is not None:
            if "ar" in lang_code:
                spk_prefix = f"[شخص {speaker_id}] "
            elif "fr" in lang_code:
                spk_prefix = f"[Personne {speaker_id}] "
            else:
                spk_prefix = f"[Person {speaker_id}] "

        self.lbl_active.configure(text=f"{spk_prefix}{sentence}", text_color=spk_color)
        if self.floating_box and self.floating_box.winfo_exists():
            self.floating_box.append_final(timestamp, sentence, speaker_id, lang_code)

        # ONLY save and display history if user required it
        if self.keep_history_var.get():
            self.transcript_records.append((timestamp, sentence, speaker_id))
            tb = self.txt_history._textbox
            tb.insert("end", f"[{timestamp}]  ", "ts")
            if speaker_id is not None:
                tb.insert("end", spk_prefix, f"spk_{speaker_id}")
            tb.insert("end", f"{sentence}\n", "speech")
            self.txt_history.see("end")

    def _engine_error_callback(self, err):
        self.after(0, lambda: messagebox.showerror("Transcription Error", f"{err}"))

    # --- Toolbar Actions ---
    def copy_transcript(self):
        content = self.txt_history.get("1.0", "end").strip()
        if content:
            self.clipboard_clear()
            self.clipboard_append(content)
            orig_txt = self.btn_copy.cget("text")
            self.btn_copy.configure(text="✓ Copied!")
            self.after(1500, lambda: self.btn_copy.configure(text=orig_txt))

    def save_text(self):
        content = self.txt_history.get("1.0", "end").strip()
        if not content:
            messagebox.showinfo("Empty", "No transcript history to save.")
            return
        fpath = filedialog.asksaveasfilename(
            defaultextension=".txt",
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")],
            title="Save Transcript"
        )
        if fpath:
            with open(fpath, "w", encoding="utf-8") as f:
                f.write(content)
            messagebox.showinfo("Saved", f"Transcript successfully saved to:\n{fpath}")

    def export_srt(self):
        if not self.transcript_records:
            messagebox.showinfo("Empty", "No transcript segments to export.")
            return
        fpath = filedialog.asksaveasfilename(
            defaultextension=".srt",
            filetypes=[("SubRip Subtitle files", "*.srt"), ("All files", "*.*")],
            title="Export Subtitles (.srt)"
        )
        if fpath:
            lang_str = self.lang_var.get()
            lang_code = LANGUAGE_MAP.get(lang_str, "ar-tn")
            with open(fpath, "w", encoding="utf-8") as f:
                for idx, record in enumerate(self.transcript_records, start=1):
                    ts = record[0]
                    text = record[1]
                    spk = record[2] if len(record) > 2 else None
                    start_srt = f"{ts},000"
                    end_srt = f"{ts},999"

                    spk_label = ""
                    if spk is not None:
                        if "ar" in lang_code:
                            spk_label = f"[شخص {spk}] "
                        elif "fr" in lang_code:
                            spk_label = f"[Personne {spk}] "
                        else:
                            spk_label = f"[Person {spk}] "

                    f.write(f"{idx}\n{start_srt} --> {end_srt}\n{spk_label}{text}\n\n")
            messagebox.showinfo("Exported", f"SRT Subtitles exported to:\n{fpath}")


    def clear_transcript(self):
        self.txt_history.delete("1.0", "end")
        self.transcript_records.clear()
        self.lbl_active.configure(text="... في انتظار الصوت ...")


if __name__ == "__main__":
    app = OmniCaptionApp()
    app.mainloop()
