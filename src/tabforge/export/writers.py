"""
Result export. The main format is .gp5 — opened by Guitar Pro,
TuxGuitar, and MuseScore. Plus MIDI as a fallback.
"""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

from ..audio.keydetect import Key
from ..core.fretboard import Shape, TabConfig


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
               title: str = "TabForge", artist: str = "",
               key: Key | None = None) -> None:
    """
    Builds a .gp5. In PyGuitarPro string #1 is the THINNEST,
    while our index 0 is the thickest. Hence the flip.
    """
    import guitarpro as gp

    song = gp.Song()
    song.title = title
    song.artist = artist
    song.tempo = int(round(bpm))
    signature = None
    if key is not None:
        # The gp5 song-level field only stores the accidental count (the
        # reader assumes major); the real signature lives on measure
        # headers — and it is a per-header property, so EVERY header must
        # carry it: a later header left at the default C major would be
        # written as a key change back to naturals.
        signature = gp.KeySignature((key.accidentals, 1 if key.minor else 0))
        song.key = signature
        for header in song.measureHeaders:
            header.keySignature = signature

    track = song.tracks[0]
    track.name = "Guitar"
    n_strings = len(cfg.tuning)
    track.strings = [
        gp.GuitarString(number=i + 1, value=cfg.tuning[n_strings - 1 - i])
        for i in range(n_strings)
    ]

    def _pad_empty_voices() -> None:
        # Guitar Pro never writes a voice with zero beats: unused measures
        # carry a whole-measure rest (voice 1) or an empty beat (voice 2).
        # alphaTab's importer/renderer crashes on truly empty voices.
        for tr in song.tracks:
            for measure in tr.measures:
                for vi, voice in enumerate(measure.voices[:2]):
                    if not voice.beats:
                        pad = gp.Beat(voice)
                        pad.status = (gp.BeatStatus.rest if vi == 0
                                      else gp.BeatStatus.empty)
                        pad.duration = gp.Duration(value=1)
                        voice.beats.append(pad)

    quarter = 60.0 / bpm
    slot_len = quarter / 4.0                    # grid resolution: sixteenths
    slots_per_measure = beats_per_measure * 4

    # Durations expressible as one gp5 Duration, in sixteenth units.
    DURATIONS = ((16, 1, False), (12, 2, True), (8, 2, False), (6, 4, True),
                 (4, 4, False), (3, 8, True), (2, 8, False), (1, 16, False))

    def _largest_fit(units: int) -> tuple[int, bool, int]:
        for u, value, dotted in DURATIONS:
            if u <= units:
                return value, dotted, u
        return 16, False, 1

    def _add_beat(voice, units: int, shape: Shape | None) -> int:
        """One beat (notes or rest) of the largest duration <= units.
        Returns the sixteenths consumed."""
        value, dotted, used = _largest_fit(units)
        beat = gp.Beat(voice)
        # Beat.status defaults to empty; empty beats don't advance the
        # reader's time cursor, so positions would collapse.
        beat.status = gp.BeatStatus.normal if shape else gp.BeatStatus.rest
        beat.duration = gp.Duration(value=value, isDotted=dotted)
        if shape:
            for p in shape.placements:
                note = gp.Note(beat)
                note.value = p.fret
                note.string = n_strings - p.string  # flipped numbering
                note.velocity = p.note.velocity
                note.type = gp.NoteType.normal
                beat.notes.append(note)
        voice.beats.append(beat)
        return used

    def _fill_rests(voice, units: int) -> None:
        while units > 0:
            units -= _add_beat(voice, units, None)

    # Events land on absolute sixteenth slots; a collision (two events
    # quantized onto one slot) pushes the later one to the next free slot.
    placed: dict[int, Shape] = {}
    for shape in shapes:
        if not shape.placements:
            continue
        slot = int(round(shape.start / slot_len))
        while slot in placed:
            slot += 1
        placed[slot] = shape

    if not placed:
        _pad_empty_voices()
        gp.write(song, str(path))
        return

    n_measures = max(placed) // slots_per_measure + 1
    while len(song.measureHeaders) < n_measures:
        header = gp.MeasureHeader()
        if signature is not None:
            header.keySignature = signature
        song.addMeasureHeader(header)
    for tr in song.tracks:
        while len(tr.measures) < n_measures:
            tr.measures.append(gp.Measure(tr, song.measureHeaders[len(tr.measures)]))

    # Assemble each measure as a gapless timeline: rests up to the event,
    # the event beat, rests to the next event — so durations always sum
    # to the full time signature and positions survive a re-read.
    for m_idx in range(n_measures):
        base = m_idx * slots_per_measure
        events = sorted(s for s in placed if base <= s < base + slots_per_measure)
        if not events:
            continue                            # _pad_empty_voices covers it
        voice = track.measures[m_idx].voices[0]
        cursor = 0
        for i, slot in enumerate(events):
            local = slot - base
            _fill_rests(voice, local - cursor)
            nxt = (events[i + 1] - base) if i + 1 < len(events) else slots_per_measure
            shape = placed[slot]
            longest = max(p.note.duration for p in shape.placements)
            sounded = max(1, min(nxt - local, round(longest / slot_len)))
            used = _add_beat(voice, sounded, shape)
            _fill_rests(voice, nxt - local - used)
            cursor = nxt

    _pad_empty_voices()
    gp.write(song, str(path))


def export_musicxml(shapes: Sequence[Shape], path: Path, bpm: float = 120.0,
                    key: Key | None = None) -> None:
    """For MuseScore / Sibelius. Staff notation without tablature."""
    from music21 import stream, note as m21note, tempo, chord as m21chord
    from music21 import key as m21key

    part = stream.Part()
    part.append(tempo.MetronomeMark(number=bpm))
    if key is not None:
        part.append(m21key.KeySignature(key.accidentals))
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
