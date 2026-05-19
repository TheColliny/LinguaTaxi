"""HTTP and WebSocket route handlers.

Sub-modules:
    display   -- audience-facing caption displays (main + extended)
    operator  -- operator control panel
    dictation -- dictation mode
    transcribe -- batch/live file transcription
"""

from linguataxi.server.routes.display import register_display_routes
from linguataxi.server.routes.operator import register_operator_routes
from linguataxi.server.routes.dictation import register_dictation_routes
from linguataxi.server.routes.transcribe import register_transcribe_routes

__all__ = [
    "register_display_routes",
    "register_operator_routes",
    "register_dictation_routes",
    "register_transcribe_routes",
]
