"""
Instrument profiles: what each stem is allowed to do in notation.

The pipeline used to apply guitar analysis to every instrument, which is
how pianos ended up with bends and hammer-ons. A profile declares, per
instrument family: whether a tablature staff makes sense, which
articulations are physically real, how legato should be written (a
technique on strings, a plain slur on keys), which tuning encodes the
notes, and the MIDI program for playback.
"""

from __future__ import annotations

from dataclasses import dataclass, replace


@dataclass(frozen=True, slots=True)
class InstrumentProfile:
    name: str
    midi_program: int
    tablature: bool         # show a tab staff at all
    allow_bends: bool
    allow_vibrato: bool
    allow_slides: bool
    allow_hammer: bool      # hammer-on/pull-off as a string technique
    legato_as_slur: bool    # legato pairs become notation slurs instead
    tuning: str | None      # fixed tuning key; None = the user's choice
    max_fret: int = 22
    # rolled-chord gathering window, seconds (0 = off): sustained-keyboard
    # transcription smears one chord's onsets across neighboring ticks
    chord_gather_window: float = 0.0
    # GM channel 10: pitches are kit voices, not notes on strings
    percussion: bool = False
    # sustain-pedal feel: notes ring past their written duration (keys)
    let_ring: bool = False
    # note source for this stem: "basic_pitch" (polyphonic, default) or
    # "mono" (f0 tracker + onset segmentation — kills octave twins by
    # construction; measured on the golden stand, task 53)
    transcriber: str = "basic_pitch"

    @property
    def wants_legato_pairs(self) -> bool:
        return self.allow_hammer or self.legato_as_slur


_GUITAR = InstrumentProfile(
    name="guitar", midi_program=25, tablature=True,   # 25 = steel guitar
    allow_bends=True, allow_vibrato=True, allow_slides=True,
    allow_hammer=True, legato_as_slur=False, tuning=None)

_BASS = InstrumentProfile(
    name="bass", midi_program=33, tablature=True,
    allow_bends=True, allow_vibrato=True, allow_slides=True,
    allow_hammer=True, legato_as_slur=False, tuning="bass_4", max_fret=20,
    transcriber="mono")

# Keys: no strings — no bends, slides, or hammer-ons; legato is written
# as a slur, and a tab staff makes no sense.
_PIANO = InstrumentProfile(
    name="piano", midi_program=0, tablature=False,
    allow_bends=False, allow_vibrato=False, allow_slides=False,
    allow_hammer=False, legato_as_slur=True,
    tuning="notation_wide", max_fret=24, chord_gather_window=0.08,
    let_ring=True)

# Drums: everything a string can do is meaningless here — the whole
# track is percussion channel 10, where the "pitch" names a kit voice.
_DRUMS = InstrumentProfile(
    name="drums", midi_program=0, tablature=False,
    allow_bends=False, allow_vibrato=False, allow_slides=False,
    allow_hammer=False, legato_as_slur=False,
    tuning="percussion", max_fret=127, percussion=True)

# Voice: notes and slides (portamento is real), nothing fretted.
_VOCALS = InstrumentProfile(
    name="vocals", midi_program=52, tablature=False,
    allow_bends=False, allow_vibrato=False, allow_slides=True,
    allow_hammer=False, legato_as_slur=True,
    tuning="notation_wide", max_fret=24, transcriber="mono")

PROFILES: dict[str, InstrumentProfile] = {
    "guitar": _GUITAR,
    # same family, different voices: the lead speaks as a clean electric
    "guitar_lead": replace(_GUITAR, name="guitar_lead", midi_program=27),
    "guitar_rhythm": replace(_GUITAR, name="guitar_rhythm"),
    "bass": _BASS,
    "piano": _PIANO,
    "piano_left": replace(_PIANO, name="piano_left"),
    "vocals": _VOCALS,
    "drums": _DRUMS,
}


def profile_for(stem: str) -> InstrumentProfile:
    """Profile for a stem/part name; unknown stems ('other', 'mix') are
    treated as guitars — the historical behavior."""
    return PROFILES.get(stem, _GUITAR)
