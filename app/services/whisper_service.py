# WhisperX service - handles speech-to-text transcription and alignment
import time
import torch
import whisperx

from app.config import settings


def get_device_config():
    """
    Detect GPU and return device configuration.
    
    Returns:
        Tuple of (device, compute_type, batch_size)
    """
    if torch.cuda.is_available():
        device = "cuda"
        compute_type = "float16"
        batch_size = 8
    else:
        device = "cpu"
        compute_type = "int8"
        batch_size = 4
    return device, compute_type, batch_size


def transcribe_audio(audio_path: str):
    """
    Transcribe audio using WhisperX.
    
    Args:
        audio_path: Path to the audio file
    
    Returns:
        Dictionary containing transcription result with segments
    """
    device, compute_type, batch_size = get_device_config()
    
    print(f"Loading WhisperX model (size: {settings.MODEL_SIZE})...")
    model = whisperx.load_model(settings.MODEL_SIZE, device=device, compute_type=compute_type)
    
    print("Transcribing audio...")
    trans_start = time.time()
    
    result = model.transcribe(
        audio_path,
        batch_size=batch_size,
        language=None
    )
    
    trans_time = time.time() - trans_start
    lang_prob = result.get("language_probability", 0.0)
    print(f"Detected language: {result['language']} (confidence {lang_prob:.0%})")
    print(f"Transcription complete — {trans_time:.1f}s")
    
    return result


def align_transcription(result: dict, audio_path: str):
    """
    Align transcript with audio for improved timestamp accuracy.
    
    Args:
        result: Transcription result from whisperx
        audio_path: Path to the audio file
    
    Returns:
        Aligned transcription result
    """
    device, _, _ = get_device_config()
    
    print("Running alignment...")
    align_start = time.time()
    
    model_a, metadata = whisperx.load_align_model(
        language_code=result["language"],
        device=device
    )
    result = whisperx.align(result["segments"], model_a, metadata, audio_path, device)
    
    align_time = time.time() - align_start
    print(f"Alignment complete — {align_time:.1f}s")
    
    return result


def cleanup_gpu_memory(model, model_a):
    """
    Free GPU memory before diarization.
    
    Args:
        model: WhisperX transcription model
        model_a: Alignment model
    """
    print("Freeing VRAM before diarization...")
    del model
    del model_a
    torch.cuda.empty_cache()
    print("VRAM cleared")
