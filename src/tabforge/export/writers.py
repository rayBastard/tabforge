"""
Result export. The main format is .gp5 — opened by Guitar Pro,
TuxGuitar, and MuseScore. Plus MIDI as a fallback.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from ..audio.keydetect import Key
from ..core.articulation import classify_articulation
from ..core.fretboard import Shape, TabConfig
from ..core.instruments import InstrumentProfile, profile_for

# A bend is only notated when it is at least this deep (semitones):
# drawing every 0.3-semitone wobble of a distorted guitar as a bend
# turns the score into noise. Better to miss one than to fake one.
NOTATED_BEND_MIN = 0.5


def export_midi(shapes: Sequence[Shape], path: Path, program: int = 25,
                is_drum: bool = False) -> None:
    """program 25 = steel guitar, 33 = fingered bass; is_drum puts the
    part on percussion channel 10, where pitches name kit voices."""
    import pretty_midi

    pm = pretty_midi.PrettyMIDI()
    inst = pretty_midi.Instrument(program=program, is_drum=is_drum)
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


def _write_atomic(gp, song, path: Path) -> None:
    """gp.write raises MID-FILE on bad input, leaving a truncated score
    that the player silently chokes on — write a sibling temp file and
    rename only on success, so a failed export leaves nothing behind."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        gp.write(song, str(tmp))
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
    tmp.replace(path)


@dataclass(slots=True)
class SongPart:
    """One instrument of a multi-track score."""
    name: str
    shapes: Sequence[Shape]
    cfg: TabConfig
    profile: InstrumentProfile
    legato: Sequence[tuple] | None = None


def export_gp5(shapes: Sequence[Shape], path: Path, cfg: TabConfig,
               bpm: float = 120.0, beats_per_measure: int = 4,
               subdivision: int = 4,
               title: str = "TabForge", artist: str = "",
               key: Key | None = None, origin: float = 0.0,
               legato: Sequence[tuple] | None = None,
               grid=None,
               profile: InstrumentProfile | None = None) -> None:
    """Single-track .gp5 — a thin wrapper over export_song_gp5."""
    part = SongPart(name=title, shapes=shapes, cfg=cfg,
                    profile=profile or profile_for("guitar"), legato=legato)
    export_song_gp5([part], path, bpm=bpm,
                    beats_per_measure=beats_per_measure,
                    subdivision=subdivision, title=title, artist=artist,
                    key=key, origin=origin, grid=grid)


def export_song_gp5(parts: Sequence[SongPart], path: Path,
                    bpm: float = 120.0, beats_per_measure: int = 4,
                    subdivision: int = 4,
                    title: str = "TabForge", artist: str = "",
                    key: Key | None = None, origin: float = 0.0,
                    grid=None, chords=None) -> None:
    """
    Builds a .gp5 where every part is a track of ONE score — the project
    player plays them together, mutes and solos per track.

    In PyGuitarPro string #1 is the THINNEST, while our index 0 is the
    thickest. Hence the flip.

    grid: the quantize.Grid the notes were snapped to. When given, a
    note's position is its TICK INDEX in that grid — the beats follow
    the audio, so a track whose tempo breathes never drifts into wrong
    measures. Tick 0 is the first beat, which also anchors measure 1;
    subdivision is taken from the grid. Without a grid positions fall
    back to a uniform 60/bpm grid anchored at `origin`.

    beats_per_measure is written as the time signature (n/4). Each
    track gets its profile's MIDI program on its own channel pair.
    """
    import guitarpro as gp

    for part in parts:
        if len(part.cfg.tuning) > 7:
            # gp5 keeps string flags in one 7-bit byte: an 8-string
            # would corrupt the file mid-write — refuse loudly instead
            raise ValueError(
                f"{part.name}: gp5 stores at most 7 strings, "
                f"got {len(part.cfg.tuning)}")

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

    # one gp5 track per part, each on its own MIDI channel pair so the
    # player gives every instrument its own sound; drums must sit on
    # channel 9 (GM percussion), so melodic pairs are dealt around it
    while len(song.tracks) < len(parts):
        song.tracks.append(gp.Track(song))
    melodic_channels = (c for c in range(16) if c != 9)
    for i, (track, part) in enumerate(zip(song.tracks, parts)):
        track.number = i + 1
        track.name = part.name or part.profile.name
        if part.profile.percussion:
            track.isPercussionTrack = True
            track.channel.channel = 9
            track.channel.effectChannel = 9
            track.channel.instrument = 0
        else:
            track.channel.channel = next(melodic_channels)
            track.channel.effectChannel = next(melodic_channels)
            track.channel.instrument = part.profile.midi_program
        n = len(part.cfg.tuning)
        track.strings = [
            gp.GuitarString(number=s + 1, value=part.cfg.tuning[n - 1 - s])
            for s in range(n)
        ]

    def _pad_empty_voices() -> None:
        # Guitar Pro never writes a voice with zero beats: unused measures
        # carry rests filling the whole time signature (voice 1) or an
        # empty beat (voice 2). alphaTab crashes on truly empty voices.
        for tr in song.tracks:
            for measure in tr.measures:
                voice0, voice1 = measure.voices[0], measure.voices[1]
                if not voice0.beats:
                    _fill_rests(voice0, slots_per_measure)
                if not voice1.beats:
                    pad = gp.Beat(voice1)
                    pad.status = gp.BeatStatus.empty
                    pad.duration = gp.Duration(value=1)
                    voice1.beats.append(pad)

    def _apply_time_signature(header) -> None:
        header.timeSignature = gp.TimeSignature(
            numerator=beats_per_measure, denominator=gp.Duration(value=4))

    if grid is not None:
        subdivision = grid.subdivision          # the grids must never differ
        def slot_of(t: float) -> int:
            return max(0, grid.tick_index(t))
    else:
        quarter = 60.0 / bpm
        slot_len = quarter / subdivision        # one uniform slot, seconds
        def slot_of(t: float) -> int:
            return max(0, int(round((t - origin) / slot_len)))
    slots_per_measure = beats_per_measure * subdivision

    # Durations expressible as one gp5 Duration, in slot units, largest
    # first. Binary values whose length is a whole number of slots, their
    # dotted variants, and — for triplet subdivisions — the single-slot
    # tuplet note (e.g. subdivision 3: an eighth-note triplet).
    def _duration_menu() -> tuple[tuple[int, int, bool, bool], ...]:
        menu: list[tuple[int, int, bool, bool]] = []
        per_whole = 4 * subdivision
        for value in (1, 2, 4, 8, 16, 32):
            if per_whole % value == 0:
                menu.append((per_whole // value, value, False, False))
                dotted_units = 3 * per_whole
                if dotted_units % (2 * value) == 0:
                    u = dotted_units // (2 * value)
                    menu.append((u, value, True, False))
        if subdivision % 3 == 0:
            menu.append((1, 8 * (subdivision // 3), False, True))
        best: dict[int, tuple[int, int, bool, bool]] = {}
        for entry in menu:
            if entry[0] not in best or not entry[2]:   # prefer undotted
                best.setdefault(entry[0], entry)
        return tuple(sorted(best.values(), reverse=True))

    DURATIONS = _duration_menu()

    def _largest_fit(units: int) -> tuple[int, bool, bool, int]:
        for u, value, dotted, tuplet in DURATIONS:
            if u <= units:
                return value, dotted, tuplet, u
        u, value, dotted, tuplet = DURATIONS[-1]
        return value, dotted, tuplet, u

    def _add_beat(voice, units: int, shape: Shape | None,
                  n_strings: int = 6, apply_fx=None, tie: bool = False) -> int:
        """One beat (notes or rest) of the largest duration <= units.
        Returns the slots consumed. tie=True marks the notes as a tie
        continuation — the same pitch held over, not restruck."""
        value, dotted, tuplet, used = _largest_fit(units)
        beat = gp.Beat(voice)
        # Beat.status defaults to empty; empty beats don't advance the
        # reader's time cursor, so positions would collapse.
        beat.status = gp.BeatStatus.normal if shape else gp.BeatStatus.rest
        beat.duration = gp.Duration(
            value=value, isDotted=dotted,
            tuplet=gp.Tuplet(enters=3, times=2) if tuplet else gp.Tuplet())
        if shape:
            for p in shape.placements:
                note = gp.Note(beat)
                note.value = p.fret
                note.string = n_strings - p.string  # flipped numbering
                note.velocity = p.note.velocity
                note.type = (gp.NoteType.tie if tie
                             else gp.NoteType.dead if p.note.dead
                             else gp.NoteType.normal)
                if apply_fx and not tie:   # articulations live on the attack
                    apply_fx(note, p.note)
                beat.notes.append(note)
        voice.beats.append(beat)
        return used

    def _fill_rests(voice, units: int) -> None:
        while units > 0:
            units -= _add_beat(voice, units, None)

    def _effects_for(part: SongPart):
        """Per-part articulation writer.

        String technique (hammer-on/pull-off): only when the pair landed
        on one string — laid out across two strings it is played picked.
        A slur (piano/vocal legato) is just an arc: no string constraint;
        gp5 encodes both via the hammer flag."""
        profile, legato = part.profile, part.legato
        hammer_ids = frozenset()
        if legato and (profile.allow_hammer or profile.legato_as_slur):
            placement_of = {id(p.note): p
                            for s in part.shapes for p in s.placements}
            hammer_ids = frozenset(
                id(pair[0]) for pair in legato
                if profile.legato_as_slur
                or ((a := placement_of.get(id(pair[0]))) is not None
                    and (b := placement_of.get(id(pair[1]))) is not None
                    and a.string == b.string))

        def apply(gp_note, src) -> None:
            if profile.let_ring:               # sustain-pedal feel on keys
                gp_note.effect.letRing = True
            if id(src) in hammer_ids:
                gp_note.effect.hammer = True
            kind = classify_articulation(src.bends)
            if kind == "vibrato" and profile.allow_vibrato:
                gp_note.effect.vibrato = True
            elif kind == "slide" and profile.allow_slides:
                net = src.bends[-1] - src.bends[0]
                gp_note.effect.slides = [gp.SlideType.outUpwards if net > 0
                                         else gp.SlideType.outDownwards]
            elif kind == "bend" and profile.allow_bends:
                peak = max(abs(b - src.bends[0]) for b in src.bends)
                if peak >= NOTATED_BEND_MIN:
                    # GP bend values are quarter-tones: a whole-tone bend
                    # is 4 (alphaTab annotates 2 as "1/2", 4 as "full")
                    v = max(1, min(gp.BendEffect.maxValue, round(peak * 2)))
                    gp_note.effect.bend = gp.BendEffect(
                        type=gp.BendType.bendRelease, value=v,
                        points=[gp.BendPoint(0, 0), gp.BendPoint(4, v),
                                gp.BendPoint(8, v), gp.BendPoint(12, 0)])
        return apply

    # Events land on absolute slots; a collision (two events quantized
    # onto one slot) pushes the later one to the next free slot.
    placed_per_part: list[dict[int, Shape]] = []
    for part in parts:
        placed: dict[int, Shape] = {}
        for shape in part.shapes:
            if not shape.placements:
                continue
            slot = slot_of(shape.start)
            while slot in placed:
                slot += 1
            placed[slot] = shape
        placed_per_part.append(placed)

    # Each event becomes a SPAN (start slot, sounded slots): the sounded
    # length comes from the transcription (same grid as the position, so
    # a breathing tempo cannot stretch values), is clipped at the next
    # event of the track, and — the anti-staccato rule — a small gap to
    # the next note (up to one beat) is absorbed into the note instead
    # of becoming a rest: transcription offsets systematically end early
    # and a score of sixteenths-plus-rests plays as chopped typewriter.
    # Real silence (a gap longer than a beat) stays a rest.
    gap_fill = subdivision                      # one beat, in slots
    spans_per_part: list[list[tuple[int, int, Shape]]] = []
    for part, placed in zip(parts, placed_per_part):
        spans: list[tuple[int, int, Shape]] = []
        order = sorted(placed)
        for i, slot in enumerate(order):
            shape = placed[slot]
            nxt = order[i + 1] if i + 1 < len(order) else None
            if part.profile.percussion:
                dur = 1                         # a hit is a transient
            else:
                longest = max(p.note.duration for p in shape.placements)
                dur = max(1, slot_of(shape.start + longest) - slot)
                if nxt is not None:
                    if dur < nxt - slot <= dur + gap_fill:
                        dur = nxt - slot        # absorb the small gap
                    dur = min(dur, nxt - slot)  # never overlap the next
            spans.append((slot, dur, shape))
        spans_per_part.append(spans)

    last_slot = max((s + d - 1 for spans in spans_per_part
                     for s, d, _ in spans), default=None)
    if last_slot is None:
        for header in song.measureHeaders:
            _apply_time_signature(header)
        _pad_empty_voices()
        _write_atomic(gp, song, path)
        return

    n_measures = last_slot // slots_per_measure + 1
    while len(song.measureHeaders) < n_measures:
        header = gp.MeasureHeader()
        if signature is not None:
            header.keySignature = signature
        song.addMeasureHeader(header)
    for header in song.measureHeaders:
        _apply_time_signature(header)
    for tr in song.tracks:
        while len(tr.measures) < n_measures:
            tr.measures.append(gp.Measure(tr, song.measureHeaders[len(tr.measures)]))

    # Assemble each track's measures as gapless timelines. A span longer
    # than one notatable duration continues as TIED beats — across beat
    # divisions and across barlines — so long notes are held, not
    # truncated at the next slot boundary.
    for track, part, spans in zip(song.tracks, parts, spans_per_part):
        apply_fx = _effects_for(part)
        n_strings = len(part.cfg.tuning)
        # split every span into menu-sized chunks per measure
        chunks_per_measure: list[list[tuple[int, int, Shape, bool]]] = \
            [[] for _ in range(n_measures)]
        for slot, dur, shape in spans:
            pos, remaining, first = slot, dur, True
            while remaining > 0 and pos // slots_per_measure < n_measures:
                m_idx = pos // slots_per_measure
                local = pos % slots_per_measure
                in_measure = min(remaining, slots_per_measure - local)
                while in_measure > 0:
                    _v, _d, _t, used = _largest_fit(in_measure)
                    chunks_per_measure[m_idx].append(
                        (local, used, shape, not first))
                    first = False
                    local += used
                    pos += used
                    in_measure -= used
                    remaining -= used
        for m_idx, chunks in enumerate(chunks_per_measure):
            if not chunks:
                continue                        # _pad_empty_voices covers it
            voice = track.measures[m_idx].voices[0]
            cursor = 0
            for local, units, shape, tie in chunks:
                _fill_rests(voice, local - cursor)
                _add_beat(voice, units, shape, n_strings, apply_fx, tie=tie)
                cursor = local + units
            _fill_rests(voice, slots_per_measure - cursor)

    _pad_empty_voices()
    if chords:
        _attach_chords(gp, song, chords)
    _write_atomic(gp, song, path)


def _attach_chords(gp, song, chords) -> None:
    """Chord labels above the score (task 58): each (qticks, name,
    strings-or-None) lands on the nearest beat of the first track —
    alphaTab and Guitar Pro render them over the system, so one track
    carries the labels for the whole song."""
    track = song.tracks[0]
    # a freshly built song has no beat.start values — walk the beats
    # accumulating durations to know where each one sits
    positions: list[tuple[int, object]] = []
    t = 0
    for m in track.measures:
        for v in m.voices[:1]:
            for b in v.beats:
                positions.append((t, b))
                t += b.duration.time
    if not positions:
        return
    for qticks, name, strings in chords:
        pos, target = min(positions, key=lambda pb: abs(pb[0] - qticks))
        if abs(pos - qticks) > 960:
            continue                     # no beat anywhere near the span
        n = len(track.strings) or 6
        frets = list(strings)[:n] if strings else [-1] * n
        frets += [-1] * (n - len(frets))
        pressed = [f for f in frets if f > 0]
        target.effect.chord = gp.Chord(
            length=n, name=name, strings=frets,
            firstFret=min(pressed) if pressed else 1, show=True)


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


def export_ascii(shapes: Sequence[Shape], path: Path, cfg: TabConfig,
                 legato: Sequence[tuple] | None = None) -> None:
    from ..core.fretboard import render_ascii
    path.write_text(render_ascii(shapes, cfg, legato=legato), encoding="utf-8")
