"""Listening preferences shared by the API and streaming provider adapters."""

from typing import Annotated

from pydantic import AfterValidator

# Common explicit ISO language choices for the existing provider adapters.
# Auto mode delegates detection/code-switching to the configured provider and
# is not restricted to this convenience menu. Provider coverage can differ.
LANGUAGES = {
    "auto": "Auto-detect / mixed languages",
    "ar": "Arabic",
    "bn": "Bengali",
    "bg": "Bulgarian",
    "ca": "Catalan",
    "zh": "Chinese (Mandarin)",
    "hr": "Croatian",
    "cs": "Czech",
    "da": "Danish",
    "nl": "Dutch",
    "en": "English",
    "fi": "Finnish",
    "fr": "French",
    "de": "German",
    "el": "Greek",
    "gu": "Gujarati",
    "he": "Hebrew",
    "hi": "Hindi",
    "hu": "Hungarian",
    "id": "Indonesian",
    "it": "Italian",
    "ja": "Japanese",
    "kn": "Kannada",
    "ko": "Korean",
    "ms": "Malay",
    "mr": "Marathi",
    "ne": "Nepali",
    "no": "Norwegian",
    "fa": "Persian",
    "pl": "Polish",
    "pt": "Portuguese",
    "ro": "Romanian",
    "ru": "Russian",
    "sr": "Serbian",
    "sk": "Slovak",
    "es": "Spanish",
    "sv": "Swedish",
    "tl": "Tagalog",
    "ta": "Tamil",
    "te": "Telugu",
    "th": "Thai",
    "tr": "Turkish",
    "uk": "Ukrainian",
    "ur": "Urdu",
    "vi": "Vietnamese",
}


def normalize_language(value: str) -> str:
    code = value.strip().lower()
    if code not in LANGUAGES:
        raise ValueError("Choose auto or a supported listening language")
    return code


ListeningLanguage = Annotated[str, AfterValidator(normalize_language)]
