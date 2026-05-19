"""Abstract base class for speech-to-text backends."""

from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from typing import Any

log: logging.Logger = logging.getLogger("livecaption")


class SpeechBackend(ABC):
    """Base class that all speech backends must implement.

    Subclasses provide a ``name`` property for logging/display and a
    ``process_audio_loop`` method that runs the main recognition loop.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable name of this backend (e.g. 'whisper (large-v3-turbo, float16, cuda)')."""
        ...

    @abstractmethod
    def process_audio_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Start the audio processing loop(s) for all registered sources.

        Args:
            loop: The asyncio event loop used for broadcasting messages
                  back to WebSocket clients.
        """
        ...

    def cleanup(self) -> None:
        """Release resources held by this backend.

        Optional override for backends that hold GPU memory, file handles,
        or other resources that should be freed on shutdown.
        """
