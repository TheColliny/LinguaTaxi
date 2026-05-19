"""Voice ID entry point — delegates to linguataxi.models.voice_id."""

from linguataxi.models.voice_id import (  # noqa: F401
    SAMPLE_RATE,
    MIN_ENROLL_SECONDS,
    MIN_IDENTIFY_SECONDS,
    set_models_dir,
    download_model,
    extract_embedding,
    is_available,
    SpeakerRegistry,
    registry,
)
