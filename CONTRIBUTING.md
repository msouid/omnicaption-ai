# Contributing to OmniCaption AI 🧏‍♂️

Thank you for your interest in making OmniCaption AI better for the deaf and hard-of-hearing community!

## How You Can Help
1. **Accessibility Improvements**: Enhancing font readability, high contrast modes, customizable subtitle background opacities, and visual cues.
2. **Dialect & Vocabulary Expansion**: Adding regional dialect terms, slang, medical keywords, and academic technical terms into `predefined_languages_keyterms.json`.
3. **Platform Support**: Optimizing audio capture drivers and audio buffer settings.

## Development Setup
1. Fork and clone the repository:
   ```bash
   git clone https://github.com/msouid/omnicaption-ai.git
   cd omnicaption-ai
   ```
2. Create a virtual environment and install requirements:
   ```bash
   python -m venv venv
   venv\Scripts\activate
   pip install -r requirements.txt
   ```
3. Copy `.env.example` to `.env` and set your `DEEPGRAM_API_KEY`.
4. Run tests or start the app:
   ```bash
   python app.py
   ```

## Pull Request Guidelines
* Keep PRs focused on a single feature or bug fix.
* Ensure UI modifications do not introduce vertical text clipping in the floating subtitle bar.
* Verify that latency optimizations (such as `TCP_NODELAY` and bounded FIFO audio queue) remain intact.
