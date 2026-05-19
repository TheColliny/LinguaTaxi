"""Language detection entry point — delegates to linguataxi.models.lang_detect."""

from linguataxi.models.lang_detect import (  # noqa: F401
    set_models_dir,
    download_model,
    detect_language,
    is_available,
)
