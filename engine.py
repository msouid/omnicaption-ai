"""
Windows Audio Capture & Deepgram Streaming Engine (Ultra-Low Latency <100ms)
Non-blocking audio capture queue with direct WebSocket streaming to Deepgram Nova-3.
"""

import sys
import os
import time
import json
import re
import asyncio
import threading
import urllib.parse
import socket
import numpy as np

try:
    import truststore
    truststore.inject_into_ssl()
except ImportError:
    pass

import pyaudiowpatch as pyaudio
import websockets

TUNISIAN_KEYTERMS = [
    "شوفلي حل", "سبوعي", "سليمان", "سليمان الأبيض", "عزة", "فوفو", "دليلة",
    "زينة", "فوشيكة", "سي المنصف", "البيرو", "العيادة", "الكلينيك", "التقفيف",
    "حبيبة", "كحلوشة", "بجبوج", "السبوعي", "دكتورة", "سيكريتيرة",
    "علاش", "برشا", "وقتاش", "شكون", "شنوة", "شنية", "كيفاش", "وين", "قداش",
    "علاش هكا", "ميسالش", "شبيك", "شبيكم", "أيا", "تي", "تي شبيك",
    "باهي", "يعيشك", "عيشك", "ربي يخليك", "يرحم والديك", "سامحني", "بربي",
    "بالله", "والله", "يزي", "فيسع", "يا خويا", "يا راجل", "يا ولدي", "أسمع",
    "شوف", "أقعد", "برة", "إمشي", "أرجع",
    "موش", "ماهوش", "ماهيش", "مانيش", "ما فماش", "فما", "ما ثماش", "ثما",
    "ما عنديش", "عندي", "ما نحبش", "نحب", "ما نجمش", "نجم", "قاعد", "ديما",
    "توة", "توا", "غادي", "هوني", "هنا",
    "موش نورمال", "حكاية فارغة", "حتى شيء", "حاجة", "شوية", "ياسر",
    "فلوس", "دينار", "مليون", "خدمة", "دار", "كرهبة", "تليفون", "مشكلة",
    "فرحان", "تاعب", "فادد", "زعمة", "أكيد", "بالحق", "صحيح"
]

FRENCH_TECH_KEYTERMS = [
    "back-off", "checkpointing", "Delta Lake", "Parquet", "ACID",
    "API", "REST", "GraphQL", "Docker", "Kubernetes", "PostgreSQL",
    "MongoDB", "Python", "JavaScript", "TypeScript", "React", "Next.js",
    "GitHub", "GitLab", "CI/CD", "DevOps", "Cloud", "AWS", "Azure",
    "GCP", "LLM", "IA", "Machine Learning", "Deep Learning", "pipeline",
    "frontend", "backend", "fullstack", "microservices", "cluster",
    "cache", "Redis", "Kafka", "streaming", "batch", "database",
    "data lake", "data warehouse", "ETL", "open source", "sprint",
    "agile", "scrum", "pull request", "commit", "merge", "debug",
    "stack trace", "log", "latency", "benchmark", "déployer", "déploiement",
    "requête", "serveur", "architecture", "conteneur", "production"
]

ENGLISH_TECH_KEYTERMS = [
    "back-off", "checkpointing", "Delta Lake", "Parquet", "ACID",
    "API", "REST", "GraphQL", "Docker", "Kubernetes", "PostgreSQL",
    "MongoDB", "Python", "JavaScript", "TypeScript", "React", "Next.js",
    "GitHub", "GitLab", "CI/CD", "DevOps", "Cloud", "AWS", "Azure",
    "GCP", "LLM", "AI", "Machine Learning", "Deep Learning", "pipeline",
    "frontend", "backend", "fullstack", "microservices", "cluster",
    "cache", "Redis", "Kafka", "streaming", "batch", "database",
    "data lake", "data warehouse", "ETL", "open source", "sprint",
    "pull request", "commit", "merge", "debug", "latency", "benchmark",
    "Deepgram", "Nova-3", "WebSocket", "vectorized", "endpointing"
]

LANGUAGE_MAP = {
    "Tunisian Darija (ar-TN)": "ar-tn",
    "Arabic - Standard (ar)": "ar",
    "Arabic - Egyptian (ar-EG)": "ar-eg",
    "Arabic - Algerian (ar-DZ)": "ar-dz",
    "Arabic - Moroccan (ar-MA)": "ar-ma",
    "French (fr)": "fr",
    "English (en)": "en",
}


class LowCutRumbleFilter:
    """
    Zero-latency 80Hz high-pass filter.
    Eliminates desk vibrations, microphone rumble, and electrical hum (<80Hz)
    to feed Deepgram Nova-3 the cleanest possible speech signal.
    """
    def __init__(self, cutoff=80.0, fs=16000.0):
        dt = 1.0 / fs
        rc = 1.0 / (2.0 * np.pi * cutoff)
        self.alpha = float(rc / (rc + dt))
        self.last_x = 0.0
        self.last_y = 0.0

    def process(self, chunk):
        if len(chunk) == 0:
            return chunk
        data = chunk.astype(np.float32)
        # Vectorized DC centering
        data -= np.mean(data)
        out = np.empty_like(data)
        lx = self.last_x
        ly = self.last_y
        a = self.alpha
        for i in range(len(data)):
            ly = a * (ly + data[i] - lx)
            lx = data[i]
            out[i] = ly
        self.last_x = lx
        self.last_y = ly
        return np.clip(out, -32768, 32767).astype(np.int16)


def clean_live_transcript(text, lang_code="ar-tn"):
    """
    Intelligent contextual cleaner for Arabic, French, and English:
    - Arabic/Tunisian: merges detached question clitics (شكون, علاش, شبيك, كيفاش), fixes spacing, removes stutter.
    - French: fixes elisions & contractions (c'est, j'ai, d'accord, qu'il, aujourd'hui), fixes spacing, capitalizes.
    - English: fixes contractions (don't, it's, I'm, can't), capitalizes 'I' and sentence starts, fixes spacing.
    """
    if not text:
        return text

    # Remove extra spaces
    text = re.sub(r'\s+', ' ', text).strip()

    if "ar" in lang_code or any('\u0600' <= c <= '\u06FF' for c in text):
        # Arabic & Tunisian clitics & Darija idioms
        text = re.sub(r'\bش\s+(كون|بيك|بيكم|بيه|بيها|نوة|نية)\b', r'ش\1', text)
        text = re.sub(r'\bكيف\s+اش\b', 'كيفاش', text)
        text = re.sub(r'\bوقت\s+اش\b', 'وقتاش', text)
        text = re.sub(r'\bعل\s+اش\b', 'علاش', text)
        text = re.sub(r'\bي\s+عيشك\b', 'يعيشك', text)
        text = re.sub(r'\bما\s+ذابيا\b', 'ماذابيا', text)
        text = re.sub(r'\bإن\s+شاء\s+الله\b', 'إن شاء الله', text)
        text = re.sub(r'\b(أنا|إنت|هو|هي|نحنا|هما|في|من|على|إلى|ثم)\s+\1\b', r'\1', text)
        text = re.sub(r'\s+([،,\.!\?؛:])', r'\1', text)

    elif "fr" in lang_code:
        # French contractions, elisions & hyphens
        text = re.sub(r"\baujourd\s*['’]\s*hui\b", "aujourd'hui", text, flags=re.IGNORECASE)
        text = re.sub(r"\bjusqu\s*['’]\s*", "jusqu'", text, flags=re.IGNORECASE)
        text = re.sub(r"\bquelqu\s*['’]\s*un\b", "quelqu'un", text, flags=re.IGNORECASE)
        text = re.sub(r"\b([cjdlnmstCJDLA-Za-z])\s*['’]\s*", r"\1'", text)
        text = re.sub(r"\bau\s+dessus\b", "au-dessus", text, flags=re.IGNORECASE)
        text = re.sub(r"\bau\s+dessous\b", "au-dessous", text, flags=re.IGNORECASE)
        text = re.sub(r"\bc['’]est\s*à\s*dire\b", "c'est-à-dire", text, flags=re.IGNORECASE)
        text = re.sub(r"\bpeut\s+être\b", "peut-être", text, flags=re.IGNORECASE)
        text = re.sub(r'\b(je|tu|il|elle|on|nous|vous|ils|elles|le|la|les|de|du|des|un|une|et|mais|ou)\s+\1\b', r'\1', text, flags=re.IGNORECASE)
        text = re.sub(r'\s+([,.\?!;:])', r'\1', text)
        if len(text) > 1 and text[0].islower():
            text = text[0].upper() + text[1:]

    elif "en" in lang_code:
        # English contractions & capitalization
        text = re.sub(r"\b([A-Za-z]+)\s*n\s*['’]\s*t\b", r"\1n't", text)
        text = re.sub(r"\b([A-Za-z]+)\s*['’]\s*(m|re|ve|ll|d|s)\b", r"\1'\2", text)
        text = re.sub(r'\bi\b', 'I', text)
        text = re.sub(r"\bi'([a-z]+)\b", r"I'\1", text)
        text = re.sub(r'\b(i|you|he|she|it|we|they|the|a|an|and|but|or|so|to|of|in|that)\s+\1\b', r'\1', text, flags=re.IGNORECASE)
        text = re.sub(r'\s+([,\.!\?;:])', r'\1', text)
        if len(text) > 1 and text[0].islower():
            text = text[0].upper() + text[1:]

    return text.strip()


def resample_to_16k(audio_arr, orig_sr):
    """
    High-speed audio resampling to 16,000 Hz for Deepgram Nova-3.
    Uses microsecond integer decimation for 48kHz / 32kHz and vectorized
    linear interpolation for non-integer multiples (e.g. 44.1kHz).
    """
    if orig_sr == 16000 or len(audio_arr) == 0:
        return audio_arr
    if orig_sr == 48000:
        return audio_arr[::3]
    if orig_sr == 32000:
        return audio_arr[::2]
    target_len = int(len(audio_arr) * 16000 / orig_sr)
    if target_len <= 0:
        return audio_arr
    x_orig = np.linspace(0, 1, len(audio_arr), endpoint=False)
    x_target = np.linspace(0, 1, target_len, endpoint=False)
    return np.interp(x_target, x_orig, audio_arr).astype(np.int16)


def extract_speaker_segments(words, fallback_txt=""):
    """
    Extract contiguous segments grouped by speaker from word tokens.
    Returns a list of (speaker_id_1_indexed, text_segment).
    If no speaker information is present, returns [(None, fallback_txt)].
    """
    if not words:
        txt = fallback_txt.strip()
        return [(None, txt)] if txt else []

    has_speaker = any("speaker" in w and w["speaker"] is not None for w in words)
    if not has_speaker:
        txt = " ".join(w.get("punctuated_word", w.get("word", "")) for w in words).strip()
        return [(None, txt or fallback_txt.strip())]

    segments = []
    curr_speaker = None
    curr_words = []

    for w in words:
        spk = w.get("speaker")
        spk_id = (spk + 1) if spk is not None else (curr_speaker or 1)
        p_word = w.get("punctuated_word") or w.get("word") or ""
        if not p_word:
            continue
        if curr_speaker is None:
            curr_speaker = spk_id
            curr_words.append(p_word)
        elif spk_id == curr_speaker:
            curr_words.append(p_word)
        else:
            seg = " ".join(curr_words).strip()
            if seg:
                segments.append((curr_speaker, seg))
            curr_speaker = spk_id
            curr_words = [p_word]

    if curr_words:
        seg = " ".join(curr_words).strip()
        if seg:
            segments.append((curr_speaker, seg))

    return segments


def load_external_keyterms():
    """
    Load user-customizable keyterm dictionary from predefined_languages_keyterms.json.
    Looks next to the executable, in the application bundle, or in the script directory.
    Falls back gracefully to built-in defaults if the file is missing or invalid.
    """
    candidates = []
    if getattr(sys, 'frozen', False):
        exe_dir = os.path.dirname(sys.executable)
        candidates.append(os.path.join(exe_dir, "predefined_languages_keyterms.json"))
        if hasattr(sys, '_MEIPASS'):
            candidates.append(os.path.join(sys._MEIPASS, "predefined_languages_keyterms.json"))
    script_dir = os.path.dirname(os.path.abspath(__file__))
    candidates.append(os.path.join(script_dir, "predefined_languages_keyterms.json"))
    candidates.append("predefined_languages_keyterms.json")

    for path in candidates:
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8-sig") as f:
                    return json.load(f)
            except Exception:
                continue
    return None


def load_env_file():
    """
    Load environment variables from a .env file if present.
    """
    candidates = [
        os.path.join(os.getcwd(), ".env"),
        os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    ]
    if getattr(sys, 'frozen', False):
        exe_dir = os.path.dirname(sys.executable)
        candidates.append(os.path.join(exe_dir, ".env"))
        if hasattr(sys, '_MEIPASS'):
            candidates.append(os.path.join(sys._MEIPASS, ".env"))

    for p in candidates:
        if os.path.exists(p):
            try:
                with open(p, "r", encoding="utf-8-sig") as f:
                    for line in f:
                        line = line.strip()
                        if line and not line.startswith("#") and "=" in line:
                            k, v = line.split("=", 1)
                            k = k.strip()
                            v = v.strip().strip('"').strip("'")
                            if k not in os.environ:
                                os.environ[k] = v
            except Exception:
                pass
            break

load_env_file()


class AudioTranscriptionEngine:
    def __init__(self, api_key=None):
        self.api_key = api_key or os.getenv("DEEPGRAM_API_KEY", "")
        self.is_running = False
        self.thread = None
        self.loop = None

        # Callbacks
        self.on_status_change = None  # cb(status_str, is_listening)
        self.on_volume = None         # cb(volume_0_to_1)
        self.on_interim = None        # cb(draft_text, speaker_id=None)
        self.on_final = None          # cb(full_sentence, timestamp, speaker_id=None)
        self.on_error = None          # cb(error_msg)

    def start(self, source_mode="system", language_code="ar-tn", enable_diarization=False):
        if self.is_running:
            return

        self.is_running = True
        self.thread = threading.Thread(
            target=self._run_thread,
            args=(source_mode, language_code, enable_diarization),
            daemon=True
        )
        self.thread.start()

    def stop(self):
        if not self.is_running:
            return
        self.is_running = False
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=1.5)

    def _run_thread(self, source_mode, language_code, enable_diarization):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        try:
            self.loop.run_until_complete(self._async_main(source_mode, language_code, enable_diarization))
        except asyncio.CancelledError:
            pass
        except Exception as e:
            # Ignore expected shutdown errors
            if self.is_running and self.on_error:
                self.on_error(str(e))
        finally:
            self.is_running = False
            try:
                # Cancel all remaining tasks cleanly
                pending = asyncio.all_tasks(self.loop)
                for task in pending:
                    task.cancel()
                if pending:
                    self.loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
                self.loop.close()
            except Exception:
                pass
            if self.on_status_change:
                self.on_status_change("Stopped", False)
            if self.on_volume:
                self.on_volume(0.0)

    async def _async_main(self, source_mode, language_code, enable_diarization):
        if self.on_status_change:
            self.on_status_change("Connecting to Audio...", True)

        p = pyaudio.PyAudio()
        try:
            wasapi_info = p.get_host_api_info_by_type(pyaudio.paWASAPI)
        except OSError:
            raise RuntimeError("WASAPI host API not supported on this Windows machine.")

        stream_loopback = None
        stream_mic = None

        # Build Deepgram WebSocket URL with domain-specific keyterm boosting
        ext_config = load_external_keyterms()
        kt_list = []
        if ext_config and language_code in ext_config:
            kt_list = ext_config[language_code].get("keyterms", [])
        else:
            if language_code == "ar-tn":
                kt_list = TUNISIAN_KEYTERMS
            elif "fr" in language_code:
                kt_list = FRENCH_TECH_KEYTERMS
            elif "en" in language_code:
                kt_list = ENGLISH_TECH_KEYTERMS

        kt_query = ""
        if kt_list:
            kt_query = "&" + "&".join([f"keyterm={urllib.parse.quote(k)}" for k in kt_list[:90]])

        diarize_param = "&diarize=true" if enable_diarization else ""

        # endpointing=300 + utterance_end_ms=1000 + numerals=true + smart_format + filler_words=false
        url = (
            f"wss://api.deepgram.com/v1/listen?"
            f"model=nova-3&language={language_code}&encoding=linear16&sample_rate=16000&channels=1"
            f"&interim_results=true&smart_format=true&numerals=true&punctuate=true&endpointing=300&vad_events=true"
            f"&utterance_end_ms=1000&filler_words=false"
            f"{diarize_param}{kt_query}"
        )
        headers = {"Authorization": f"Token {self.api_key}"}

        # Setup audio capture
        audio_queue = asyncio.Queue(maxsize=50)

        def safe_put_audio(data_bytes):
            # Healthy FIFO queue: never drop normal speech frames!
            # Only shed oldest frames if network experiences severe lag (> 20 frames / >1s)
            if audio_queue.qsize() > 20:
                try:
                    for _ in range(10):
                        audio_queue.get_nowait()
                except Exception:
                    pass
            try:
                audio_queue.put_nowait(data_bytes)
            except Exception:
                pass

        # Dedicated non-blocking audio capture thread
        def audio_capture_worker():
            nonlocal stream_loopback, stream_mic
            try:
                # Open loopback
                lb_rate = 48000
                CHUNK_lb = 2400
                if source_mode in ("system", "both"):
                    default_speakers = p.get_device_info_by_index(wasapi_info["defaultOutputDevice"])
                    loopback_dev = None
                    if default_speakers.get("isLoopbackDevice"):
                        loopback_dev = default_speakers
                    else:
                        for dev in p.get_loopback_device_info_generator():
                            if default_speakers["name"] in dev["name"]:
                                loopback_dev = dev
                                break
                        if not loopback_dev:
                            loopback_dev = next(p.get_loopback_device_info_generator())

                    lb_rate = int(loopback_dev["defaultSampleRate"])
                    lb_channels = loopback_dev["maxInputChannels"]
                    CHUNK_lb = int(lb_rate * 0.05)  # 50ms buffer at native rate
                    stream_loopback = p.open(
                        format=pyaudio.paInt16,
                        channels=lb_channels,
                        rate=lb_rate,
                        input=True,
                        input_device_index=loopback_dev["index"],
                        frames_per_buffer=CHUNK_lb
                    )

                # Open mic
                mic_rate = 48000
                CHUNK_mic = 2400
                if source_mode in ("mic", "both"):
                    mic_idx = wasapi_info.get("defaultInputDevice", -1)
                    if mic_idx >= 0:
                        mic_dev = p.get_device_info_by_index(mic_idx)
                        mic_rate = int(mic_dev["defaultSampleRate"])
                        mic_channels = mic_dev["maxInputChannels"]
                        CHUNK_mic = int(mic_rate * 0.05)  # 50ms buffer at native rate
                        stream_mic = p.open(
                            format=pyaudio.paInt16,
                            channels=mic_channels,
                            rate=mic_rate,
                            input=True,
                            input_device_index=mic_dev["index"],
                            frames_per_buffer=CHUNK_mic
                        )

                rumble_filter = LowCutRumbleFilter(cutoff=80.0, fs=16000.0)

                while self.is_running:
                    mono_16k = None
                    if source_mode == "system" and stream_loopback:
                        raw = stream_loopback.read(CHUNK_lb, exception_on_overflow=False)
                        if raw:
                            samples = np.frombuffer(raw, dtype=np.int16)
                            mono = samples.reshape(-1, lb_channels).mean(axis=1).astype(np.int16)
                            mono_16k = resample_to_16k(mono, lb_rate)

                    elif source_mode == "mic" and stream_mic:
                        raw = stream_mic.read(CHUNK_mic, exception_on_overflow=False)
                        if raw:
                            samples = np.frombuffer(raw, dtype=np.int16)
                            mono = samples.reshape(-1, mic_channels).mean(axis=1).astype(np.int16)
                            mono_16k = resample_to_16k(mono, mic_rate)

                    elif source_mode == "both" and stream_loopback and stream_mic:
                        raw_lb = stream_loopback.read(CHUNK_lb, exception_on_overflow=False)
                        raw_mc = stream_mic.read(CHUNK_mic, exception_on_overflow=False)
                        if raw_lb and raw_mc:
                            s_lb = np.frombuffer(raw_lb, dtype=np.int16).reshape(-1, lb_channels).mean(axis=1).astype(np.int16)
                            s_mc = np.frombuffer(raw_mc, dtype=np.int16).reshape(-1, mic_channels).mean(axis=1).astype(np.int16)
                            s_lb_16k = resample_to_16k(s_lb, lb_rate)
                            s_mc_16k = resample_to_16k(s_mc, mic_rate)
                            min_len = min(len(s_lb_16k), len(s_mc_16k))
                            mixed = np.clip(s_lb_16k[:min_len] * 0.8 + s_mc_16k[:min_len] * 0.8, -32768, 32767)
                            mono_16k = mixed.astype(np.int16)

                    if mono_16k is not None and len(mono_16k) > 0:
                        # 80Hz Low-cut rumble filter: strips DC offset, desk rumble, and electrical hum
                        mono_16k = rumble_filter.process(mono_16k)

                        # Compute volume for VU meter
                        rms = float(np.sqrt(np.mean(mono_16k.astype(float)**2)))
                        norm_vol = min(1.0, rms / 8000.0)
                        if self.on_volume:
                            self.on_volume(norm_vol)

                        # Queue continuous studio audio to Deepgram
                        if self.loop and self.loop.is_running():
                            self.loop.call_soon_threadsafe(safe_put_audio, mono_16k.tobytes())
            except Exception as ex:
                if self.on_error:
                    self.on_error(f"Audio capture error: {ex}")

        capture_thread = threading.Thread(target=audio_capture_worker, daemon=True)
        capture_thread.start()

        if self.on_status_change:
            self.on_status_change("Connecting to Deepgram Nova-3...", True)

        try:
            async with websockets.connect(
                url,
                extra_headers=headers,
                ping_interval=5,
                ping_timeout=10,
                max_size=10 * 1024 * 1024,
                compression=None
            ) as ws:
                # Ultra-low latency: bypass TCP Nagle buffering algorithm
                try:
                    sock = ws.transport.get_extra_info('socket')
                    if sock is not None:
                        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                except Exception:
                    pass

                if self.on_status_change:
                    self.on_status_change(f"Live Transcribing ({language_code.upper()})", True)

                sentence_parts = []
                accumulated_words = []
                last_emitted = ("", None)

                def emit_final(words_list, fallback_str):
                    nonlocal last_emitted
                    if not fallback_str and not words_list:
                        return
                    segments = extract_speaker_segments(words_list, fallback_str)
                    ts = time.strftime("%H:%M:%S")
                    for spk_id, seg_txt in segments:
                        seg_txt = seg_txt.strip()
                        seg_txt = clean_live_transcript(seg_txt, language_code)
                        if seg_txt and (seg_txt, spk_id) != last_emitted:
                            last_emitted = (seg_txt, spk_id)
                            if self.on_final:
                                self.on_final(seg_txt, ts, spk_id)

                async def receiver():
                    nonlocal sentence_parts, accumulated_words
                    try:
                        async for msg in ws:
                            if not self.is_running:
                                break
                            data = json.loads(msg)
                            msg_type = data.get("type")

                            if msg_type == "Results":
                                alts = data.get("channel", {}).get("alternatives", [])
                                if not alts:
                                    continue
                                txt = alts[0].get("transcript", "").strip()
                                txt = clean_live_transcript(txt, language_code)
                                is_final = data.get("is_final", False)
                                speech_final = data.get("speech_final", False)

                                if not txt:
                                    continue

                                if is_final:
                                    new_words = alts[0].get("words", [])
                                    # If a speaker switch occurred between chunks, flush previous speaker
                                    if new_words and accumulated_words:
                                        prev_spk = next((w.get("speaker") for w in reversed(accumulated_words) if w.get("speaker") is not None), None)
                                        new_spk = next((w.get("speaker") for w in new_words if w.get("speaker") is not None), None)
                                        if prev_spk is not None and new_spk is not None and prev_spk != new_spk:
                                            emit_final(accumulated_words, " ".join(sentence_parts).strip())
                                            sentence_parts = []
                                            accumulated_words = []

                                    if new_words:
                                        accumulated_words.extend(new_words)
                                    sentence_parts.append(txt)
                                    full_sentence = " ".join(sentence_parts).strip()

                                    # Current speaker for draft
                                    cur_spk = None
                                    if accumulated_words:
                                        for w in reversed(accumulated_words):
                                            if w.get("speaker") is not None:
                                                cur_spk = w.get("speaker") + 1
                                                break

                                    if self.on_interim:
                                        self.on_interim(full_sentence, cur_spk)

                                    has_terminal_punct = any(txt.rstrip().endswith(p) for p in ('.', '!', '?', '؛', '؟'))
                                    is_long_clause = len(full_sentence.split()) >= 16

                                    if speech_final or has_terminal_punct or is_long_clause:
                                        emit_final(accumulated_words, full_sentence)
                                        sentence_parts = []
                                        accumulated_words = []
                                else:
                                    # Instant zero-latency draft update
                                    current_full = " ".join(sentence_parts + [txt]).strip()
                                    cur_spk = None
                                    draft_words = alts[0].get("words", [])
                                    if draft_words:
                                        for w in reversed(draft_words):
                                            if w.get("speaker") is not None:
                                                cur_spk = w.get("speaker") + 1
                                                break
                                    if cur_spk is None and accumulated_words:
                                        for w in reversed(accumulated_words):
                                            if w.get("speaker") is not None:
                                                cur_spk = w.get("speaker") + 1
                                                break

                                    if self.on_interim:
                                        self.on_interim(current_full, cur_spk)

                            elif msg_type == "UtteranceEnd":
                                if sentence_parts or accumulated_words:
                                    full_sentence = " ".join(sentence_parts).strip()
                                    emit_final(accumulated_words, full_sentence)
                                    sentence_parts = []
                                    accumulated_words = []
                    except asyncio.CancelledError:
                        pass
                    except Exception as ex:
                        if self.on_error:
                            self.on_error(f"WebSocket receiver: {ex}")

                recv_task = asyncio.create_task(receiver())

                try:
                    while self.is_running:
                        try:
                            pcm_bytes = await asyncio.wait_for(audio_queue.get(), timeout=0.2)
                            if pcm_bytes is not None:
                                await ws.send(pcm_bytes)
                            # Instant queue drain: flush any accumulated frames immediately
                            while not audio_queue.empty():
                                extra = audio_queue.get_nowait()
                                if extra is not None:
                                    await ws.send(extra)
                        except asyncio.TimeoutError:
                            continue
                finally:
                    try:
                        # Flush server-side buffer to finalize pending speech
                        await ws.send(json.dumps({"type": "Finalize"}))
                        await asyncio.sleep(0.05)
                    except Exception:
                        pass
                    recv_task.cancel()
        finally:
            if stream_loopback:
                try:
                    stream_loopback.stop_stream()
                    stream_loopback.close()
                except Exception:
                    pass
            if stream_mic:
                try:
                    stream_mic.stop_stream()
                    stream_mic.close()
                except Exception:
                    pass
            p.terminate()
