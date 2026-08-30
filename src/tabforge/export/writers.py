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
    rename only on success, so a failed export leaves nothing behind.

    Encoding: the gp5 format stores strings in a single-byte codepage
    (PyGuitarPro defaults to cp1252). Whisper lyrics and user-renamed
    section markers are routinely Cyrillic — that used to kill the
    WHOLE song.gp5 ('charmap' codec error) and the app showed download
    cards with no score. Fallback chain: cp1252 -> cp1251 (ASCII-
    compatible, what Guitar Pro on Russian systems reads) -> cp1252
    with unencodable characters replaced by '?' (a lossy lyric beats
    a missing score; the .lrc keeps the true text in UTF-8)."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    last_err = None
    for encoding in ("cp1252", "cp1251", None):
        try:
            if encoding is None:
                _replace_unencodable(gp, song, "cp1252")
                gp.write(song, str(tmp))
            else:
                gp.write(song, str(tmp), encoding=encoding)
            tmp.replace(path)
            return
        except UnicodeEncodeError as e:
            last_err = e
            tmp.unlink(missing_ok=True)
        except Exception:
            tmp.unlink(missing_ok=True)
            raise
    raise last_err


def _replace_unencodable(gp, song, encoding: str) -> None:
    """Last-resort sanitizer: rewrite every string field that cannot
    survive the target codepage with '?' placeholders."""
    def fix(s):
        return s.encode(encoding, errors="replace").decode(encoding)

    song.title = fix(song.title or "")
    if getattr(song, "lyrics", None):
        for line in song.lyrics.lines:
            line.lyrics = fix(line.lyrics or "")
    for track in song.tracks:
        track.name = fix(track.name or "")
        for measure in track.measures:
            marker = measure.header.marker
            if marker:
                marker.title = fix(marker.title or "")


@dataclass(slots=True)
class SongPart:
    """One instrument of a multi-track score."""
    name: str
    shapes: Sequence[Shape]
    cfg: TabConfig
    profile: InstrumentProfile
    legato: Sequence[tuple] | None = None


# ---------------------------------------------------------------------------
# The adaptive display grid (the durations war, 2026-08-30).
# FINE = 24 units per beat — the LCM of every display grid we can
# notate: 32nds (3 units), 16th triplets (4), 16ths (6), 8th triplets
# (8), 8ths (12).
# ---------------------------------------------------------------------------

FINE = 24
CANDIDATES = (2, 3, 4, 6, 8)      # 8ths .. 32nds, triplet families


# The rents and the merge price are CALIBRATED, not guessed
# (scratchpad grid_bench, 2026-08-30, the rhythm-mess regression):
# clean Suno truth MIDI must keep its real 16ths (Loken: 74 measures
# of d4, 0% junk) while noisy transcription onsets must NOT escalate —
# the first shipped version had hard escalate-on-any-conflict rules
# and turned 37-66% of transcribed measures into junk triplets/32nds
# ("каша с ритмом"). A merge is CHEAP (1.2 — a lone flam gets pushed
# one slot, the pre-adaptive behavior) so only a RUN of fine notes,
# where merges pile up, pays for a finer grid; triplet grids rent
# higher than straight ones so jitter can't masquerade as shuffle.
_GRID_RENT = {2: 0.0, 3: 2.0, 4: 1.2, 6: 3.2, 8: 3.5}
_MERGE_PRICE = 1.2


def pick_subdivision(onsets_fine: list[int], fine: int = FINE) -> int:
    """The display grid a measure's own notes ask for: the score is
    mean onset displacement + the price of merged attacks + the grid's
    rent; the cheapest grid wins, coarse by default."""
    if not onsets_fine:
        return CANDIDATES[0]
    onsets = sorted(set(onsets_fine))
    n = len(onsets)
    best_d, best_score = CANDIDATES[0], None
    for d in CANDIDATES:
        width = fine // d
        slots = [int(round(o / width)) for o in onsets]
        merges = sum(1 for i in range(1, n)
                     if slots[i] == slots[i - 1]
                     and onsets[i] - onsets[i - 1] >= 3)
        disp = sum(abs(o - s * width)
                   for o, s in zip(onsets, slots)) / n
        score = disp + _MERGE_PRICE * merges + _GRID_RENT[d]
        if best_score is None or score < best_score - 1e-9:
            best_d, best_score = d, score
    return best_d


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
                    grid=None, chords=None, sections=None,
                    lyrics=None) -> None:
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
    # gp5 carries a 64-slot channel table (4 MIDI ports); channel 9 of
    # EVERY port is percussion by GM convention. 16 channels ran out at
    # 8 melodic parts (piano split doubled the roster) and the bare
    # StopIteration killed the whole song.gp5 with an empty error.
    melodic_channels = (c for c in range(64) if c % 16 != 9)
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
                    _fill_rests(voice0, beats_per_measure * 2, MENUS[2])
                if not voice1.beats:
                    pad = gp.Beat(voice1)
                    pad.status = gp.BeatStatus.empty
                    pad.duration = gp.Duration(value=1)
                    voice1.beats.append(pad)

    def _apply_time_signature(header) -> None:
        header.timeSignature = gp.TimeSignature(
            numerator=beats_per_measure, denominator=gp.Duration(value=4))

    # ---- the fine timeline (the durations war, 2026-08-30) ----
    # Notes land on a grid of FINE=24 units per beat — the LCM of every
    # display grid we can notate: 32nds (3 units), 16th triplets (4),
    # 16ths (6), 8th triplets (8), 8ths (12). The DISPLAY subdivision is
    # then chosen PER MEASURE per track: the coarsest one that keeps
    # distinguishable notes distinct. A verse of 8ths stays clean 8ths,
    # a 32nd run in ANY measure gets real 32nds — no global "precision"
    # choice, the notes themselves decide (the legacy `subdivision`
    # argument is accepted and ignored).
    if grid is not None and len(grid.beats) > 1:
        from bisect import bisect_left
        beats_t = grid.beats
        fine_ticks: list[float] = []
        for bi in range(len(beats_t) - 1):
            a, b = beats_t[bi], beats_t[bi + 1]
            for k in range(FINE):
                fine_ticks.append(a + (b - a) * k / FINE)
        fine_ticks.append(beats_t[-1])
        avg_fine = ((fine_ticks[-1] - fine_ticks[0])
                    / max(1, len(fine_ticks) - 1)) or 1e-6

        def fine_of(t: float) -> int:
            if t <= fine_ticks[0]:
                return max(0, -int(round((fine_ticks[0] - t) / avg_fine)))
            if t >= fine_ticks[-1]:
                return (len(fine_ticks) - 1
                        + int(round((t - fine_ticks[-1]) / avg_fine)))
            i = bisect_left(fine_ticks, t)
            return i if (fine_ticks[i] - t) <= (t - fine_ticks[i - 1]) \
                else i - 1
    else:
        quarter = 60.0 / bpm
        fine_len = quarter / FINE
        def fine_of(t: float) -> int:
            return max(0, int(round((t - origin) / fine_len)))
    fine_per_measure = beats_per_measure * FINE

    # Durations expressible as one gp5 Duration for a given display
    # subdivision d (slots per beat), in slot units, largest first:
    # binary values a whole number of slots long, their dotted variants,
    # and — for triplet subdivisions — the single-slot tuplet note.
    def _duration_menu(d: int) -> tuple[tuple[int, int, bool, bool], ...]:
        menu: list[tuple[int, int, bool, bool]] = []
        per_whole = 4 * d
        for value in (1, 2, 4, 8, 16, 32):
            if per_whole % value == 0:
                menu.append((per_whole // value, value, False, False))
                dotted_units = 3 * per_whole
                if dotted_units % (2 * value) == 0:
                    u = dotted_units // (2 * value)
                    menu.append((u, value, True, False))
        if d % 3 == 0:
            menu.append((1, 8 * (d // 3), False, True))
        best: dict[int, tuple[int, int, bool, bool]] = {}
        for entry in menu:
            if entry[0] not in best or not entry[2]:   # prefer undotted
                best.setdefault(entry[0], entry)
        return tuple(sorted(best.values(), reverse=True))

    MENUS = {d: _duration_menu(d) for d in CANDIDATES}

    def _largest_fit(units: int, menu) -> tuple[int, bool, bool, int]:
        for u, value, dotted, tuplet in menu:
            if u <= units:
                return value, dotted, tuplet, u
        u, value, dotted, tuplet = menu[-1]
        return value, dotted, tuplet, u

    _pick_subdivision = pick_subdivision

    def _add_beat(voice, units: int, menu, shape: Shape | None,
                  n_strings: int = 6, apply_fx=None, tie: bool = False) -> int:
        """One beat (notes or rest) of the largest duration <= units.
        Returns the slots consumed. tie=True marks the notes as a tie
        continuation — the same pitch held over, not restruck."""
        value, dotted, tuplet, used = _largest_fit(units, menu)
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

    def _fill_rests(voice, units: int, menu) -> None:
        while units > 0:
            units -= _add_beat(voice, units, menu, None)

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

    # Band tightness (2026-08-31, the desync report; third design —
    # the first two patched AFTER per-part slot rounding and moved the
    # honest same-hit-written-apart metric only 38% -> 33%): every
    # model hears the same band hit a few tens of ms apart, so build a
    # cross-part CONSENSUS in seconds BEFORE any slotting. Steps:
    # 1. de-bias each part by its median offset to the 16th grid
    #    (inter-model latency calibration: bass −27 ms, piano −22,
    #    guitar +4 on one mix — the groove around the median stays);
    # 2. cluster de-biased attacks of DIFFERENT parts within 55 ms;
    # 3. every cluster plays at ONE time — the drum member's (the kit
    #    defines the pocket) or the median. Identical times then land
    #    on identical fine slots at any grid, which no post-rounding
    #    repair could guarantee. parts.json/MIDI keep raw times.
    def _part_bias(shapes) -> float:
        import statistics
        sixteenth = FINE // 4
        res = []
        for shape in shapes:
            if not shape.placements:
                continue
            f = fine_of(shape.start)
            near = round(f / sixteenth) * sixteenth
            res.append((f - near) * (60.0 / max(bpm, 1e-6)) / FINE)
        if len(res) < 8:
            return 0.0
        return max(-0.08, min(0.08, statistics.median(res)))

    part_bias = [_part_bias(part.shapes) for part in parts]
    adjusted: dict[int, float] = {}          # id(shape) -> consensus time
    events = []                              # (debiased start, pi, shape)
    for pi, part in enumerate(parts):
        for shape in part.shapes:
            if shape.placements:
                events.append((shape.start - part_bias[pi], pi, shape))
    events.sort(key=lambda e: e[0])
    drum_parts = {pi for pi, part in enumerate(parts)
                  if part.profile.percussion}

    def _settle(cl) -> None:
        for tstar, _pi, shape in cl:
            adjusted[id(shape)] = tstar
        if len(cl) < 2 or len({pi for _t, pi, _s in cl}) < 2:
            return
        drum_ts = [t for t, pi, _s in cl if pi in drum_parts]
        target = drum_ts[0] if drum_ts else             sorted(t for t, _pi, _s in cl)[len(cl) // 2]
        seen_parts: set[int] = set()
        multi = {pi for _t, pi, _s in cl
                 if pi in seen_parts or seen_parts.add(pi)}
        for t, pi, shape in cl:
            # a part with several attacks in one cluster keeps its own
            # rhythm (a real flam/run must not collapse); drums anchor
            if pi not in multi and pi not in drum_parts:
                adjusted[id(shape)] = target

    cluster: list = []
    for ev in events:
        if cluster and ev[0] - cluster[0][0] > 0.055:
            _settle(cluster)
            cluster = []
        cluster.append(ev)
    _settle(cluster)

    # Events land on absolute FINE slots; a collision (two events within
    # a 32nd of each other) pushes the later one a 32nd to the right so
    # neither note is silently swallowed.
    import os as _os
    _trace = ([] if _os.environ.get("TABFORGE_WRITER_TRACE") else None)
    placed_per_part: list[dict[int, Shape]] = []
    for part in parts:
        placed: dict[int, Shape] = {}
        cells: set[int] = set()             # occupied 32nd-wide cells
        for shape in part.shapes:
            if not shape.placements:
                continue
            raw_slot = fine_of(adjusted[id(shape)])
            slot = raw_slot
            while slot // 3 in cells:
                slot += 3
            cells.add(slot // 3)
            placed[slot] = shape
            if _trace is not None:
                _trace.append({"part": part.name,
                               "raw": round(shape.start, 3),
                               "adj": round(adjusted[id(shape)], 3),
                               "fine": raw_slot, "pushed": slot})
        placed_per_part.append(placed)

    # Each event becomes a SPAN (start, sounded length — fine units):
    # the sounded length comes from the transcription (same grid as the
    # position, so a breathing tempo cannot stretch values), is clipped
    # at the next event of the track, and — the anti-staccato rule — a
    # small gap to the next note (up to one beat) is absorbed into the
    # note instead of becoming a rest: transcription offsets
    # systematically end early and a score of sixteenths-plus-rests
    # plays as chopped typewriter. Real silence (longer) stays a rest.
    gap_fill = FINE                             # one beat, fine units
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
                dur = max(1, fine_of(adjusted[id(shape)] + longest)
                          - slot)
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

    n_measures = last_slot // fine_per_measure + 1
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

    # Assemble each track's measures as gapless timelines. Spans split
    # at barlines into per-measure SEGMENTS (fine units); each measure
    # then picks its own display subdivision and renders its segments
    # in those slots. A span longer than one notatable duration
    # continues as TIED beats — across beat divisions and barlines —
    # so long notes are held, not truncated.
    # Pass 1: split spans into per-measure segments and pick each
    # (track, measure)'s own display grid.
    all_segs: list[list[list[tuple[int, int, Shape, bool]]]] = []
    own_d: list[list[int | None]] = []
    for spans in spans_per_part:
        segs_per_measure: list[list[tuple[int, int, Shape, bool]]] = \
            [[] for _ in range(n_measures)]
        for slot, dur, shape in spans:
            pos, remaining, first = slot, dur, True
            while remaining > 0 and pos // fine_per_measure < n_measures:
                m_idx = pos // fine_per_measure
                local = pos % fine_per_measure
                in_measure = min(remaining, fine_per_measure - local)
                segs_per_measure[m_idx].append(
                    (local, in_measure, shape, not first))
                first = False
                pos += in_measure
                remaining -= in_measure
        for segs in segs_per_measure:
            segs.sort(key=lambda s: s[0])
        all_segs.append(segs_per_measure)
        own_d.append([(_pick_subdivision([s[0] for s in segs])
                       if segs else None)
                      for segs in segs_per_measure])

    # Pass 2: SHARE the grid across tracks per measure (the last leg
    # of band tightness): an identical consensus fine slot still split
    # across tracks when their measures rounded on different grids
    # (slot 50: d2/d4 -> 48, d8 -> 51). Within a family the grids nest
    # (2|4|8, 3|6), so every track adopts the measure's finest choice
    # of the MAJORITY family; a coarse part on a finer grid renders
    # identically, and same-slot attacks now round identically too.
    # The SONG BASE grid (2026-08-31, the user's crispness report):
    # the old fixed precision selector felt CRISPER because the whole
    # song breathed one grid; per-measure adaptivity lets neighboring
    # bars flip between 8ths and 16ths on jitter and the rhythm reads
    # loose. Compute the grid the user would have picked by hand —
    # 16ths when at least a quarter of the note mass asks for them —
    # and render nothing coarser than that (a coarse bar on a finer
    # nested grid is exact); escalation ABOVE the base (32nd solos,
    # triplet bars) still needs the picker's evidence, so the
    # adaptive win survives inside the fixed-choice feel.
    weight_fine = weight_all = 0
    for pi in range(len(parts)):
        for m_idx, d in enumerate(own_d[pi]):
            if d is None or d in (3, 6):
                continue
            w = len(all_segs[pi][m_idx])
            weight_all += w
            if d >= 4:
                weight_fine += w
    song_base = 4 if (weight_all and weight_fine / weight_all >= 0.25) \
        else 2

    shared_d: list[list[int]] = []
    for pi in range(len(parts)):
        shared_d.append([(max(d, song_base) if d in (2, 4, 8) else d)
                         if d is not None else None
                         for d in own_d[pi]])
    for m_idx in range(n_measures):
        chosen = [d[m_idx] for d in shared_d if d[m_idx] is not None]
        if not chosen:
            continue
        straight = [d for d in chosen if d in (2, 4, 8)]
        triplet = [d for d in chosen if d in (3, 6)]
        family = straight if len(straight) >= len(triplet) else triplet
        target = max(family) if family else max(chosen)
        for pi in range(len(parts)):
            cur = shared_d[pi][m_idx]
            if cur is not None and cur in (2, 4, 8, 3, 6) and \
                    ((target in (2, 4, 8)) == (cur in (2, 4, 8))):
                shared_d[pi][m_idx] = target

    for pi, (track, part) in enumerate(zip(song.tracks, parts)):
        apply_fx = _effects_for(part)
        n_strings = len(part.cfg.tuning)
        for m_idx, segs in enumerate(all_segs[pi]):
            if not segs:
                continue                        # _pad_empty_voices covers it
            d = shared_d[pi][m_idx]
            menu = MENUS[d]
            width = FINE // d
            spm = beats_per_measure * d
            voice = track.measures[m_idx].voices[0]
            cursor = 0
            # rounded slot of every segment FIRST: a duration that
            # rounds up must never overrun the next attack's slot (the
            # cursor would push that attack a slot late — measured as
            # a chief cross-track desync source: each track slipped
            # independently)
            slots_r = [int(round(local / width)) for local, *_ in segs]
            for si, (local, flen, shape, tie) in enumerate(segs):
                s = max(slots_r[si], cursor)
                if s >= spm:
                    continue
                if _trace is not None and not tie:
                    _trace.append({"part": part.name, "final": True,
                                   "raw": round(shape.start, 3),
                                   "m": m_idx, "d": d, "s": s,
                                   "cursor_push": s - slots_r[si]})
                units = min(max(1, int(round(flen / width))), spm - s)
                if si + 1 < len(segs):
                    next_s = max(slots_r[si + 1], s + 1)
                    units = min(units, max(1, next_s - s))
                _fill_rests(voice, s - cursor, menu)
                pos2, remaining2, first2 = s, units, True
                while remaining2 > 0:
                    _v, _dt, _tp, used = _largest_fit(remaining2, menu)
                    _add_beat(voice, used, menu, shape, n_strings,
                              apply_fx, tie=tie or not first2)
                    first2 = False
                    pos2 += used
                    remaining2 -= used
                cursor = s + units
            _fill_rests(voice, spm - cursor, menu)

    _pad_empty_voices()
    if chords:
        _attach_chords(gp, song, chords, parts)
    if lyrics:
        # the gp5 lyrics channel (task 60): (part_name, measure, text)
        part_name, measure, text = lyrics
        for ti, part in enumerate(parts):
            if part.name == part_name:
                song.lyrics = gp.Lyrics(trackChoice=ti)
                song.lyrics.lines[0] = gp.LyricLine(
                    startingMeasure=max(1, measure), lyrics=text)
                break
    if sections:
        # section markers on the measure headers (task 59)
        ticks_per_measure = 960 * beats_per_measure
        headers = [m.header for m in song.tracks[0].measures]
        for qticks, label in sections:
            idx = min(len(headers) - 1,
                      max(0, round(qticks / ticks_per_measure)))
            headers[idx].marker = gp.Marker(title=str(label)[:40])
    if _trace is not None:
        import json as _json
        Path(_os.environ["TABFORGE_WRITER_TRACE"]).write_text(
            _json.dumps(_trace))
    _write_atomic(gp, song, path)


def _attach_chords(gp, song, chords, parts) -> None:
    """Chord labels above the score (task 58): each (qticks, name,
    strings-or-None) lands on the nearest beat of the first GUITAR
    track — chord grids belong over a tab, not over the keys or the
    drums, and alphaTab renders the diagram header only for the
    rendered track's own chords. A project with no fretted instrument
    keeps its chord line in the app UI but gets no gp5 grids."""
    idx = next((i for i, p in enumerate(parts)
                if p.profile.tablature), None)
    if idx is None:
        return
    track = song.tracks[idx]
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
