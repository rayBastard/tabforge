"""TabForge: аудио -> табулатура и ноты."""
from .core.fretboard import (NoteEvent, Placement, Shape, TabConfig, TUNINGS,
                             assign_tab, render_ascii)
__all__ = ["NoteEvent", "Placement", "Shape", "TabConfig", "TUNINGS",
           "assign_tab", "render_ascii"]
__version__ = "0.1.0"
