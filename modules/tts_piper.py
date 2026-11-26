"""
Piper TTS Module for Discord Bot

This module provides offline TTS functionality using Piper TTS.
It automatically discovers voice models from the piper/voices directory
and generates audio files using subprocess calls to piper.exe.
"""

import os
import subprocess
import uuid
from typing import Dict, List, Optional
from pathlib import Path
import sys

# Constants
if getattr(sys, 'frozen', False):
    BASE_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PIPER_DIR = os.path.join(BASE_DIR, "piper")
PIPER_EXE = os.path.join(PIPER_DIR, "piper.exe")
VOICES_DIR = os.path.join(PIPER_DIR, "voices")
OUTPUT_DIR = os.path.join(PIPER_DIR, "output")


def discover_voice_models() -> Dict[str, List[str]]:
    """
    Automatically discover all voice models from the piper/voices directory.
    
    Scans the voices directory structure and returns a dictionary mapping
    language codes to lists of available voice model names.
    
    Returns:
        Dict[str, List[str]]: Dictionary where keys are language codes (e.g., "vi", "en")
                              and values are lists of voice model names (e.g., ["banmai", "vietthao"])
    
    Example:
        {
            "vi": ["banmai", "vietthao", "ngocngan"],
            "en": ["lessac", "amy"],
            "ja": ["kumi"]
        }
    """
    models: Dict[str, List[str]] = {}
    
    if not os.path.exists(VOICES_DIR):
        print(f"[Piper] Voices directory not found: {VOICES_DIR}")
        return models
    
    try:
        # Iterate through each language directory
        for lang_dir in os.listdir(VOICES_DIR):
            lang_path = os.path.join(VOICES_DIR, lang_dir)
            
            # Skip if not a directory
            if not os.path.isdir(lang_path):
                continue
            
            # Find all .onnx files in the language directory
            voices: List[str] = []
            for file in os.listdir(lang_path):
                if file.endswith(".onnx"):
                    # Remove .onnx extension to get voice name
                    voice_name = file[:-5]
                    voices.append(voice_name)
            
            # Only add language if it has voices
            if voices:
                models[lang_dir] = sorted(voices)
                print(f"[Piper] Found {len(voices)} voices for language '{lang_dir}': {voices}")
    
    except Exception as e:
        print(f"[Piper] Error discovering voice models: {e}")
    
    return models


def get_available_languages() -> List[str]:
    """
    Get list of available language codes.
    
    Returns:
        List[str]: Sorted list of language codes (e.g., ["en", "ja", "vi"])
    """
    models = discover_voice_models()
    return sorted(models.keys())


def get_available_voices(language: str) -> List[str]:
    """
    Get list of available voice models for a specific language.
    
    Args:
        language (str): Language code (e.g., "vi", "en")
    
    Returns:
        List[str]: Sorted list of voice model names for the language
    """
    models = discover_voice_models()
    return models.get(language, [])


def validate_model(language: str, voice: str) -> bool:
    """
    Validate if a specific voice model exists.
    
    Args:
        language (str): Language code (e.g., "vi", "en")
        voice (str): Voice model name (e.g., "banmai", "lessac")
    
    Returns:
        bool: True if the model exists, False otherwise
    """
    model_path = os.path.join(VOICES_DIR, language, f"{voice}.onnx")
    config_path = os.path.join(VOICES_DIR, language, f"{voice}.onnx.json")
    
    return os.path.exists(model_path) and os.path.exists(config_path)


def generate_voice(language: str, voice: str, text: str) -> Optional[str]:
    """
    Generate audio file using Piper TTS.
    
    This function calls piper.exe via subprocess to generate a WAV file
    from the given text using the specified language and voice model.
    
    Args:
        language (str): Language code (e.g., "vi", "en")
        voice (str): Voice model name (e.g., "banmai", "lessac")
        text (str): Text to convert to speech
    
    Returns:
        Optional[str]: Path to the generated WAV file, or None if generation failed
    
    Raises:
        FileNotFoundError: If piper.exe or model files are not found
        subprocess.TimeoutExpired: If Piper takes too long to generate
        subprocess.CalledProcessError: If Piper returns an error
    """
    # Validate model exists
    if not validate_model(language, voice):
        raise FileNotFoundError(
            f"Model not found: piper/voices/{language}/{voice}.onnx"
        )
    
    # Ensure output directory exists
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    
    # Generate unique filename
    output_filename = f"tts_{uuid.uuid4().hex}.wav"
    output_path = os.path.join(OUTPUT_DIR, output_filename)
    
    # Build Piper command
    model_path = os.path.join(VOICES_DIR, language, f"{voice}.onnx")
    config_path = os.path.join(VOICES_DIR, language, f"{voice}.onnx.json")
    
    command = [
        PIPER_EXE,
        "--model", model_path,
        "--config", config_path,
        "--output_file", output_path
    ]
    
    try:
        # Run Piper with text input
        result = subprocess.run(
            command,
            input=text,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=30  # 30 second timeout
        )
        
        # Check if Piper succeeded
        if result.returncode != 0:
            error_msg = result.stderr or result.stdout or "Unknown error"
            raise subprocess.CalledProcessError(
                result.returncode,
                command,
                stderr=error_msg
            )
        
        # Verify output file was created
        if not os.path.exists(output_path):
            raise FileNotFoundError(
                f"Piper did not create output file: {output_path}"
            )
        
        print(f"[Piper] Generated audio: {output_path}")
        return output_path
    
    except subprocess.TimeoutExpired:
        print(f"[Piper] Timeout generating audio for text: {text[:50]}...")
        raise
    except subprocess.CalledProcessError as e:
        print(f"[Piper] Error generating audio: {e.stderr}")
        raise
    except Exception as e:
        print(f"[Piper] Unexpected error: {e}")
        raise


def cleanup_audio_file(file_path: str) -> bool:
    """
    Delete a generated audio file.
    
    Args:
        file_path (str): Path to the audio file to delete
    
    Returns:
        bool: True if deletion succeeded, False otherwise
    """
    try:
        if os.path.exists(file_path):
            os.remove(file_path)
            print(f"[Piper] Cleaned up audio file: {file_path}")
            return True
    except Exception as e:
        print(f"[Piper] Error cleaning up audio file {file_path}: {e}")
    return False


# Cache for discovered models to avoid repeated directory scans
_voice_models_cache: Optional[Dict[str, List[str]]] = None


def get_cached_models() -> Dict[str, List[str]]:
    """
    Get cached voice models, discovering them if not cached.
    
    Returns:
        Dict[str, List[str]]: Dictionary of language to voice models
    """
    global _voice_models_cache
    if _voice_models_cache is None:
        _voice_models_cache = discover_voice_models()
    return _voice_models_cache


def clear_model_cache() -> None:
    """
    Clear the cached voice models to force rediscovery.
    """
    global _voice_models_cache
    _voice_models_cache = None
