"""
Result export. The main format is .gp5 — opened by Guitar Pro,
TuxGuitar, and MuseScore. Plus MIDI as a fallback.
"""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

from ..core.fretboard import Shape, TabConfig
from ..core.quantize import duration_symbol


def export_midi(shapes: Sequence[Shape], path: Path, program: int = 25) -> None:
    """program 25 = steel guitar, 33 = fingered bass."""
    import pretty_midi

    pm = pretty_midi.PrettyMIDI()
    inst = pretty_midi.Instrument(program=program)
    for shape in shapes:
        for p in shape.placements:
            inst.notes.append(
                pretty_midi.Note(
                    velocity=p.note.velocity,
                    pitch=p.note.pitch,
                    start=p.note.start,
                    end=p.note.end,
                )
            )
    pm.instruments.append(inst)
    pm.write(str(path))


def export_gp5(shapes: Sequence[Shape], path: Path, cfg: TabConfig,
               bpm: float = 120.0, beats_per_measure: int = 4,
               title: str = "TabForge", artist: str = "") -> None:
    """
    Builds a .gp5. In PyGuitarPro string #1 is the THINNEST,
    while our index 0 is the thickest. Hence the flip.
    """
    import guitarpro as gp

    song = gp.Song()
    song.title = title
    song.artist = artist
    song.tempo = int(round(bpm))

    track = song.tracks[0]
    track.name = "Guitar"
    n_strings = len(cfg.tuning)
    track.strings = [
        gp.GuitarString(number=i + 1, value=cfg.tuning[n_strings - 1 - i])
        for i in range(n_strings)
    ]

    quarter = 60.0 / bpm
    measure_len = quarter * beats_per_measure
    if not shapes:
        gp.write(song, str(path))
        return

    total = max(s.placements[-1].note.end for s in shapes if s.placements)
    n_measures = max(1, int(total / measure_len) + 1)

    while len(song.measureHeaders) < n_measures:
        song.addMeasureHeader(gp.MeasureHeader())
    for tr in song.tracks:
        while len(tr.measures) < n_measures:
            tr.measures.append(gp.Measure(tr, song.measureHeaders[len(tr.measures)]))

    for shape in shapes:
        if not shape.placements:
            continue
        m_idx = min(int(shape.start / measure_len), n_measures - 1)
        measure = track.measures[m_idx]
        voice = measure.voices[0]

        longest = max(p.note.duration for p in shape.placements)
        value, dotted = duration_symbol(longest, bpm)

        beat = gp.Beat(voice)
        beat.duration = gp.Duration(value=value, isDotted=dotted)
        beat.start = None
        for p in shape.placements:
            note = gp.Note(beat)
            note.value = p.fret
            note.string = n_strings - p.string      # flipped numbering
            note.velocity = p.note.velocity
            note.type = gp.NoteType.normal
            beat.notes.append(note)
        voice.beats.append(beat)

    gp.write(song, str(path))


def export_musicxml(shapes: Sequence[Shape], path: Path, bpm: float = 120.0) -> None:
    """For MuseScore / Sibelius. Staff notation without tablature."""
    from music21 import stream, note as m21note, tempo, chord as m21chord

    part = stream.Part()
    part.append(tempo.MetronomeMark(number=bpm))
    quarter = 60.0 / bpm

    for shape in shapes:
        pitches = [p.note.pitch for p in shape.placements]
        ql = max(p.note.duration for p in shape.placements) / quarter
        ql = max(round(ql * 4) / 4, 0.25)
        el = (m21chord.Chord(pitches, quarterLength=ql) if len(pitches) > 1
              else m21note.Note(pitches[0], quarterLength=ql))
        part.append(el)

    stream.Score([part]).write("musicxml", fp=str(path))


def export_ascii(shapes: Sequence[Shape], path: Path, cfg: TabConfig) -> None:
    from ..core.fretboard import render_ascii
    path.write_text(render_ascii(shapes, cfg), encoding="utf-8")
