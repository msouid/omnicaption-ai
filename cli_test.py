"""
OmniCaption AI - Console / CMD Live Transcriber Test Runner
Stream live captions directly to Command Prompt / PowerShell!
"""
import sys
import os
import time
import argparse

if sys.stdout and hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

from engine import AudioTranscriptionEngine, LANGUAGE_MAP, load_external_keyterms, save_api_key

# ANSI Colors for CMD/Terminal
CYAN = "\033[96m"
YELLOW = "\033[93m"
GREEN = "\033[92m"
RED = "\033[91m"
MAGENTA = "\033[95m"
WHITE = "\033[97m"
GRAY = "\033[90m"
RESET = "\033[0m"
BOLD = "\033[1m"

def main():
    parser = argparse.ArgumentParser(description="OmniCaption Live Audio Transcriber - CMD Test")
    parser.add_argument("--lang", default="fr", choices=["ar-tn", "ar", "fr", "en"], help="Language code (ar-tn, fr, en)")
    parser.add_argument("--source", default="system", choices=["system", "mic", "both"], help="Audio source (system, mic, both)")
    parser.add_argument("--diarize", action="store_true", help="Enable speaker diarization")
    args = parser.parse_args()

    # Enable ANSI escape sequences on Windows CMD
    os.system("")

    print(f"{BOLD}{CYAN}======================================================{RESET}")
    print(f"{BOLD}{GREEN}  OmniCaption AI - CMD Live Audio Transcription Test  {RESET}")
    print(f"{BOLD}{CYAN}======================================================{RESET}")
    print(f"{WHITE}Language:{RESET} {YELLOW}{args.lang}{RESET}")
    print(f"{WHITE}Audio Source:{RESET} {YELLOW}{args.source}{RESET} (system=YouTube/PC audio, mic=microphone)")
    print(f"{WHITE}Diarization:{RESET} {YELLOW}{'ON' if args.diarize else 'OFF'}{RESET}")
    print(f"{GRAY}Press Ctrl+C anytime to stop.{RESET}\n")

    engine = AudioTranscriptionEngine()
    if not engine.api_key:
        print(f"{YELLOW}🔑 Deepgram API Key not configured.{RESET}")
        print(f"{WHITE}Get a free key at: {CYAN}https://console.deepgram.com/signup{RESET}\n")
        try:
            user_key = input("Enter your Deepgram API Key: ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\nExiting.")
            return
        if user_key:
            save_api_key(user_key)
            engine.api_key = user_key
            print(f"{GREEN}✓ Key saved permanently! Starting transcription...{RESET}\n")
        else:
            print(f"{RED}Cannot start transcription without an API key.{RESET}")
            return

    def on_status(status, is_active):
        color = GREEN if is_active else GRAY
        print(f"\r{GRAY}[STATUS]{RESET} {color}{status}{RESET}                         ")

    def on_volume(vol):
        pass

    def on_interim(draft, spk=None):
        spk_str = f"{YELLOW}[Person {spk}]{RESET} " if spk else ""
        trimmed = draft if len(draft) < 110 else "..." + draft[-107:]
        sys.stdout.write(f"\r{CYAN}▶ {spk_str}{trimmed}{RESET} \033[K")
        sys.stdout.flush()

    def on_final(sentence, ts, spk=None):
        spk_str = f"{YELLOW}[Person {spk}]{RESET} " if spk else ""
        sys.stdout.write(f"\r{GRAY}[{ts}]{RESET} {spk_str}{BOLD}{WHITE}{sentence}{RESET}\033[K\n")
        sys.stdout.flush()

    def on_error(err):
        print(f"\n{MAGENTA}[ERROR]{RESET} {err}")

    engine.on_status_change = on_status
    engine.on_volume = on_volume
    engine.on_interim = on_interim
    engine.on_final = on_final
    engine.on_error = on_error

    engine.start(source_mode=args.source, language_code=args.lang, enable_diarization=args.diarize)

    try:
        while engine.is_running:
            time.sleep(0.5)
    except KeyboardInterrupt:
        print(f"\n{YELLOW}Stopping transcription engine...{RESET}")
        engine.stop()
        print(f"{GREEN}Stopped cleanly.{RESET}")

if __name__ == "__main__":
    main()
