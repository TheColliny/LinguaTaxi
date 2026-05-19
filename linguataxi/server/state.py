"""Global server state flags and coordination events."""

from __future__ import annotations

import threading

translation_paused: bool = True
captioning_paused: bool = True
dictation_active: bool = False
save_transcripts: bool = True

shutdown_event: threading.Event = threading.Event()
mic_restart_event: threading.Event = threading.Event()
