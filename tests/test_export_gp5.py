"""Round-trip tests for the .gp5 export. Skipped when PyGuitarPro
is not installed (CI runs the core without the export extra)."""
import tempfile
import unittest
from collections import Counter
from pathlib import Path

from tabforge.core.fretboard import NoteEvent, TabConfig, TUNINGS, assign_tab

try:
    import guitarpro as gp
    HAVE_GP = True
except ImportError:
    HAVE_GP = False


def _roundtrip(shapes, cfg, **kwargs):
    from tabforge.export.gp5_read import read_gp5
    from tabforge.export.writers import export_gp5

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "test.gp5"
        export_gp5(shapes, path, cfg, **kwargs)
        contents = read_gp5(str(path))
    # rest/empty padding beats carry no notes; the note-bearing ones are
    # what the melodic assertions care about
    return contents.track, contents.note_beats, [p for _, p in contents.notes]


@unittest.skipUnless(HAVE_GP, "PyGuitarPro is not installed")
class TestGp5Roundtrip(unittest.TestCase):
    def setUp(self):
        self.cfg = TabConfig()

    def test_pitches_survive_roundtrip(self):
        source = [60, 62, 64, 65, 67, 69, 71, 72]
        notes = [NoteEvent(p, i * 0.5, 0.4) for i, p in enumerate(source)]
        shapes = assign_tab(notes, self.cfg)
        _, beats, pitches = _roundtrip(shapes, self.cfg, bpm=120.0)
        self.assertEqual(Counter(pitches), Counter(source))

    def test_beats_stay_separate(self):
        # Eight notes inside one measure must come back as eight beats,
        # not collapse into one (the BeatStatus.empty regression).
        notes = [NoteEvent(60, i * 0.2, 0.15) for i in range(8)]
        shapes = assign_tab(notes, self.cfg)
        _, beats, _ = _roundtrip(shapes, self.cfg, bpm=120.0)
        self.assertEqual(len(beats), len(shapes))
        for beat in beats:
            strings = [n.string for n in beat.notes]
            self.assertEqual(len(strings), len(set(strings)),
                             "two notes on one string within a beat")

    def test_chord_kept_in_one_beat(self):
        chord = [40, 47, 52, 55, 59, 64]  # open Em
        notes = [NoteEvent(p, 0.0, 1.0) for p in chord]
        shapes = assign_tab(notes, self.cfg)
        _, beats, pitches = _roundtrip(shapes, self.cfg, bpm=120.0)
        self.assertEqual(len(beats), 1)
        self.assertEqual(Counter(pitches), Counter(chord))

    def test_no_empty_voices(self):
        # A line starting late leaves leading measures without notes; Guitar
        # Pro pads such voices with rests and alphaTab crashes without them.
        import guitarpro as gp
        from tabforge.export.writers import export_gp5

        notes = [NoteEvent(60, 20.0 + i * 0.5, 0.4) for i in range(4)]
        shapes = assign_tab(notes, self.cfg)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "late.gp5"
            export_gp5(shapes, path, self.cfg, bpm=120.0)
            song = gp.parse(str(path))
        for m in song.tracks[0].measures:
            for voice in m.voices[:2]:
                self.assertGreaterEqual(len(voice.beats), 1,
                                        f"measure {m.number} has an empty voice")

    def test_measures_sum_to_time_signature(self):
        import guitarpro as gp
        from tabforge.export.writers import export_gp5

        # sparse line with gaps at 120 BPM: sixteenth slot = 0.125 s
        notes = [NoteEvent(60, 0.0, 0.4), NoteEvent(64, 0.75, 0.2),
                 NoteEvent(67, 3.625, 0.5)]
        shapes = assign_tab(notes, self.cfg)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "gaps.gp5"
            export_gp5(shapes, path, self.cfg, bpm=120.0)
            song = gp.parse(str(path))
        measure_ticks = 4 * gp.Duration.quarterTime
        for m in song.tracks[0].measures:
            total = sum(b.duration.time for b in m.voices[0].beats)
            self.assertEqual(total, measure_ticks,
                             f"measure {m.number} durations sum to {total}")

    def test_note_positions_roundtrip(self):
        import guitarpro as gp
        from tabforge.export.writers import export_gp5

        starts = [0.0, 0.75, 3.625]            # slots 0, 6, 29 at 120 BPM
        notes = [NoteEvent(60 + i, t, 0.2) for i, t in enumerate(starts)]
        shapes = assign_tab(notes, self.cfg)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "pos.gp5"
            export_gp5(shapes, path, self.cfg, bpm=120.0)
            song = gp.parse(str(path))
        origin = song.measureHeaders[0].start
        sixteenth = gp.Duration.quarterTime // 4
        got = sorted((b.start - origin) // sixteenth
                     for m in song.tracks[0].measures
                     for b in m.voices[0].beats if b.notes)
        self.assertEqual(got, [0, 6, 29])

    def test_origin_anchors_measures_to_first_beat(self):
        # Regression: the slot grid was anchored at t=0, so a track with a
        # lead-in had every note shifted off the barline. Shifting all
        # notes by +0.7 s with origin=0.7 must keep slots identical.
        import guitarpro as gp
        from tabforge.export.writers import export_gp5

        starts = [0.0, 0.75, 3.625]

        def slots(offset, origin):
            notes = [NoteEvent(60 + i, t + offset, 0.2)
                     for i, t in enumerate(starts)]
            shapes = assign_tab(notes, self.cfg)
            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "o.gp5"
                export_gp5(shapes, path, self.cfg, bpm=120.0, origin=origin)
                song = gp.parse(str(path))
            base = song.measureHeaders[0].start
            sixteenth = gp.Duration.quarterTime // 4
            return sorted((b.start - base) // sixteenth
                          for m in song.tracks[0].measures
                          for b in m.voices[0].beats if b.notes)

        self.assertEqual(slots(0.0, 0.0), [0, 6, 29])
        self.assertEqual(slots(0.7, 0.7), [0, 6, 29],
                         "a 0.7 s lead-in shifted the measures")

    def test_breathing_tempo_does_not_drift_measures(self):
        # Regression for the "jumping tempo": Suno tracks breathe, and a
        # fixed seconds->BPM conversion accumulated drift until notes
        # landed in wrong measures. With the real Grid the note position
        # is its tick index, so drift is impossible by construction.
        import guitarpro as gp
        from tabforge.core.quantize import Grid
        from tabforge.export.writers import export_gp5

        # 48 beats whose duration slows steadily to +4% (nominal 0.5 s)
        beats, t = [], 0.0
        for i in range(48):
            beats.append(t)
            t += 0.5 * (1.0 + 0.04 * i / 47)
        grid = Grid(beats, subdivision=4)

        # one note exactly on every 4th beat = every measure's downbeat
        downbeats = list(range(0, 48, 4))
        notes = [NoteEvent(60 + (i % 12), beats[b], 0.3)
                 for i, b in enumerate(downbeats)]
        shapes = assign_tab(notes, self.cfg)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "drift.gp5"
            export_gp5(shapes, path, self.cfg, bpm=120.0, grid=grid)
            song = gp.parse(str(path))

        note_measures = [m.number for m in song.tracks[0].measures
                         for v in m.voices for b in v.beats if b.notes
                         for _ in [0]]
        self.assertEqual(note_measures, list(range(1, 13)),
                         "every downbeat note must sit in its own measure")
        # and each one exactly ON the downbeat (first slot of the measure)
        origin = song.measureHeaders[0].start
        for m in song.tracks[0].measures:
            for v in m.voices:
                for b in v.beats:
                    if b.notes:
                        self.assertEqual(b.start, m.start,
                                         f"note off the downbeat in "
                                         f"measure {m.number}")

    def test_key_signature_roundtrip(self):
        import guitarpro as gp
        from tabforge.audio.keydetect import Key
        from tabforge.export.writers import export_gp5

        # A multi-measure song: the key must hold in EVERY measure header,
        # otherwise gp5 encodes a key change back to C major at measure 2.
        notes = [NoteEvent(60, t, 0.4) for t in (0.0, 3.0, 6.0, 9.0)]
        shapes = assign_tab(notes, self.cfg)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "key.gp5"
            export_gp5(shapes, path, self.cfg, bpm=96.0,
                       key=Key(5, True, 0.8))  # F minor, 4 flats
            song = gp.parse(str(path))
        self.assertGreater(len(song.measureHeaders), 1)
        for header in song.measureHeaders:
            self.assertEqual(header.keySignature, gp.KeySignature.FMinor,
                             f"measure {header.number} lost the key")
        self.assertEqual(song.key.value[0], -4)

    def test_triplet_subdivision_positions_roundtrip(self):
        import guitarpro as gp
        from tabforge.export.writers import export_gp5

        # subdivision=3: slot = quarter/3; at 120 BPM a slot is 1/6 s.
        slot = 0.5 / 3
        slots = [0, 2, 7]
        notes = [NoteEvent(60 + i, s * slot, 0.1) for i, s in enumerate(slots)]
        shapes = assign_tab(notes, self.cfg)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "trip.gp5"
            export_gp5(shapes, path, self.cfg, bpm=120.0, subdivision=3)
            song = gp.parse(str(path))
        origin = song.measureHeaders[0].start
        slot_ticks = gp.Duration.quarterTime // 3
        # attacks only: a tie continuation is the same note held over
        got = sorted((b.start - origin) // slot_ticks
                     for m in song.tracks[0].measures
                     for b in m.voices[0].beats
                     if any(n.type != gp.NoteType.tie for n in b.notes))
        self.assertEqual(got, slots)
        # durations still fill every measure exactly
        measure_ticks = 4 * gp.Duration.quarterTime
        for m in song.tracks[0].measures:
            self.assertEqual(sum(b.duration.time for b in m.voices[0].beats),
                             measure_ticks)

    def test_three_four_time_signature_roundtrip(self):
        import guitarpro as gp
        from tabforge.export.writers import export_gp5

        # 3/4 at 120 BPM: measure = 1.5 s; a note in measures 1 and 2
        notes = [NoteEvent(60, 0.0, 0.4), NoteEvent(64, 1.5, 0.4)]
        shapes = assign_tab(notes, self.cfg)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "waltz.gp5"
            export_gp5(shapes, path, self.cfg, bpm=120.0, beats_per_measure=3)
            song = gp.parse(str(path))
        self.assertGreaterEqual(len(song.measureHeaders), 2)
        for header in song.measureHeaders:
            self.assertEqual(header.timeSignature.numerator, 3)
            self.assertEqual(header.timeSignature.denominator.value, 4)
        measure_ticks = 3 * gp.Duration.quarterTime
        for m in song.tracks[0].measures:
            self.assertEqual(sum(b.duration.time for b in m.voices[0].beats),
                             measure_ticks,
                             f"measure {m.number} does not fill 3/4")

    def test_effects_roundtrip(self):
        from tabforge.core.articulation import detect_legato_pairs
        from tabforge.export.gp5_read import read_gp5
        from tabforge.export.writers import export_gp5

        rise_fall = ([i * 0.1 for i in range(10)]
                     + [1.0 - i * 0.1 for i in range(10)])
        notes = [
            NoteEvent(55, 0.0, 0.28, 100),                       # hammer from
            NoteEvent(59, 0.3, 0.3, 70),                         # hammer to
            NoteEvent(64, 1.0, 0.5, 90,
                      bends=[0.3 * (i % 2) - 0.15 + 0.15 for i in range(2)]),
            NoteEvent(67, 2.0, 0.6, 90, bends=rise_fall),        # bend 1.0 st
            NoteEvent(60, 3.0, 0.6, 90,
                      bends=[i * 0.05 for i in range(20)]),      # slide up 0.95
        ]
        import math
        notes[2].bends = [0.3 * math.sin(2 * math.pi * i / 8)
                          for i in range(32)]                    # vibrato
        legato = detect_legato_pairs(notes)
        self.assertEqual(len(legato), 1)
        shapes = assign_tab(notes, self.cfg, legato=legato)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "fx.gp5"
            export_gp5(shapes, path, self.cfg, bpm=120.0, legato=legato)
            contents = read_gp5(str(path))
        self.assertEqual(contents.effects["hammer"], 1)
        self.assertEqual(contents.effects["vibrato"], 1)
        self.assertEqual(contents.effects["bend"], 1)
        self.assertEqual(contents.effects["slide"], 1)
        self.assertEqual(contents.hammer_violations, 0)

    def test_shallow_bend_is_not_notated(self):
        from tabforge.export.gp5_read import read_gp5
        from tabforge.export.writers import export_gp5

        shallow = ([i * 0.04 for i in range(10)]
                   + [0.36 - i * 0.04 for i in range(10)])       # peak 0.36
        notes = [NoteEvent(60, 0.0, 0.6, 90, bends=shallow)]
        shapes = assign_tab(notes, self.cfg)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "shallow.gp5"
            export_gp5(shapes, path, self.cfg, bpm=120.0)
            contents = read_gp5(str(path))
        self.assertEqual(contents.effects["bend"], 0,
                         "sub-0.5st bends must not clutter the score")

    def test_multitrack_song(self):
        import guitarpro as gp
        from tabforge.core.instruments import profile_for
        from tabforge.export.writers import SongPart, export_song_gp5

        g_cfg = TabConfig()
        b_cfg = TabConfig(tuning=TUNINGS["bass_4"], max_fret=20)
        g_notes = [NoteEvent(60 + i, i * 0.5, 0.4) for i in range(4)]
        b_notes = [NoteEvent(28 + i, i * 1.0, 0.8) for i in range(3)]
        parts = [
            SongPart("guitar", assign_tab(g_notes, g_cfg), g_cfg,
                     profile_for("guitar")),
            SongPart("bass", assign_tab(b_notes, b_cfg), b_cfg,
                     profile_for("bass")),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "song.gp5"
            export_song_gp5(parts, path, bpm=120.0)
            song = gp.parse(str(path))

        self.assertEqual(len(song.tracks), 2)
        names = [t.name for t in song.tracks]
        self.assertEqual(names, ["guitar", "bass"])
        self.assertEqual([t.channel.instrument for t in song.tracks],
                         [25, 33])
        # channels must differ or the synth blends the sounds
        self.assertNotEqual(song.tracks[0].channel.channel,
                            song.tracks[1].channel.channel)
        # both tracks share the same measure count, every measure full
        measure_ticks = 4 * gp.Duration.quarterTime
        for t in song.tracks:
            self.assertEqual(len(t.measures), len(song.tracks[0].measures))
            for m in t.measures:
                self.assertEqual(
                    sum(b.duration.time for b in m.voices[0].beats),
                    measure_ticks)
        # each part's pitches live on its own track
        def pitches(track):
            sv = {s.number: s.value for s in track.strings}
            return sorted(sv[n.string] + n.value
                          for m in track.measures for v in m.voices
                          for b in v.beats for n in b.notes)
        self.assertEqual(pitches(song.tracks[0]), [60, 61, 62, 63])
        self.assertEqual(pitches(song.tracks[1]), [28, 29, 30])

    def test_bass_tuning(self):
        cfg = TabConfig(tuning=TUNINGS["bass_4"], max_fret=20)
        source = [28, 31, 33, 35]
        notes = [NoteEvent(p, i * 0.5, 0.4) for i, p in enumerate(source)]
        shapes = assign_tab(notes, cfg)
        track, _, pitches = _roundtrip(shapes, cfg, bpm=100.0)
        self.assertEqual(len(track.strings), 4)
        self.assertEqual(Counter(pitches), Counter(source))

    def test_drums_track_is_percussion_on_channel_9(self):
        import guitarpro as gp
        from tabforge.audio.drums import drum_shapes
        from tabforge.core.instruments import profile_for
        from tabforge.export.writers import SongPart, export_song_gp5

        d_cfg = TabConfig(tuning=TUNINGS["percussion"], max_fret=127)
        hits = [NoteEvent(36, 0.0, 0.1), NoteEvent(42, 0.0, 0.1),
                NoteEvent(38, 0.5, 0.1)]
        g_cfg = TabConfig()
        g_notes = [NoteEvent(60 + i, i * 0.5, 0.4) for i in range(4)]
        parts = [
            SongPart("guitar", assign_tab(g_notes, g_cfg), g_cfg,
                     profile_for("guitar")),
            SongPart("drums", drum_shapes(hits), d_cfg,
                     profile_for("drums")),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "song.gp5"
            export_song_gp5(parts, path, bpm=120.0)
            song = gp.parse(str(path))

        guitar, drums = song.tracks
        self.assertTrue(drums.isPercussionTrack)
        self.assertEqual(drums.channel.channel, 9,
                         "GM percussion lives on channel 10 (index 9)")
        self.assertNotEqual(guitar.channel.channel, 9)
        # the GM kit numbers ride in the fret field on zero-tuned strings
        values = sorted(n.value
                        for m in drums.measures for v in m.voices
                        for b in v.beats for n in b.notes)
        self.assertEqual(values, [36, 38, 42])

    def _parse_single(self, notes, stem="guitar", bpm=120.0):
        import guitarpro as gp
        from tabforge.core.instruments import profile_for
        from tabforge.export.writers import export_gp5

        profile = profile_for(stem)
        cfg = TabConfig(tuning=TUNINGS[profile.tuning or "standard"],
                        max_fret=profile.max_fret)
        shapes = assign_tab(notes, cfg)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "x.gp5"
            export_gp5(shapes, path, cfg, bpm=bpm, profile=profile)
            song = gp.parse(str(path))
        return song.tracks[0]

    @staticmethod
    def _flat_beats(track):
        return [(m_i, b) for m_i, m in enumerate(track.measures)
                for b in m.voices[0].beats]

    def test_long_note_is_tied_not_truncated(self):
        import guitarpro as gp
        # 1.25 s at 120 BPM subdiv 4 = 10 sixteenth slots: one duration
        # can't write that — expect an attack plus TIED continuations
        track = self._parse_single([NoteEvent(64, 0.0, 1.25)])
        played = [(m, b) for m, b in self._flat_beats(track)
                  if b.status == gp.BeatStatus.normal and b.notes]
        self.assertGreaterEqual(len(played), 2)
        self.assertTrue(all(n.type == gp.NoteType.tie
                            for _, b in played[1:] for n in b.notes),
                        "continuation beats must be ties, not restrikes")
        slot_ticks = gp.Duration.quarterTime // 4
        total = sum(b.duration.time for _, b in played)
        self.assertEqual(total, 10 * slot_ticks,
                         "the whole sounded length must be written")

    def test_note_rings_across_the_barline(self):
        import guitarpro as gp
        # starts on the last beat of measure 1, sounds into measure 2
        track = self._parse_single([NoteEvent(64, 1.75, 0.5)])
        played = [(m, b) for m, b in self._flat_beats(track)
                  if b.status == gp.BeatStatus.normal and b.notes]
        self.assertEqual({m for m, _ in played}, {0, 1},
                         "the note must live in both measures")
        second_measure = [b for m, b in played if m == 1]
        self.assertTrue(all(n.type == gp.NoteType.tie
                            for b in second_measure for n in b.notes))

    def test_small_gap_is_absorbed_not_a_rest(self):
        import guitarpro as gp
        # two short notes 3 slots apart: the 2-slot gap (< one beat)
        # must be absorbed into the first note, not chopped into rests
        track = self._parse_single([NoteEvent(64, 0.0, 0.125),
                                    NoteEvent(65, 0.375, 0.125)])
        beats = self._flat_beats(track)
        first_rest = next((i for i, (_, b) in enumerate(beats)
                           if b.status == gp.BeatStatus.rest), None)
        last_note = max(i for i, (_, b) in enumerate(beats)
                        if b.status == gp.BeatStatus.normal and b.notes)
        self.assertTrue(first_rest is None or first_rest > last_note,
                        "no rest may interrupt a near-legato line")

    def test_real_silence_stays_a_rest(self):
        import guitarpro as gp
        # a 7-slot gap is a genuine pause — it must NOT be absorbed
        track = self._parse_single([NoteEvent(64, 0.0, 0.125),
                                    NoteEvent(65, 1.0, 0.125)])
        beats = self._flat_beats(track)
        last_note = max(i for i, (_, b) in enumerate(beats)
                        if b.status == gp.BeatStatus.normal and b.notes)
        rests_between = [i for i, (_, b) in enumerate(beats)
                         if b.status == gp.BeatStatus.rest and i < last_note]
        self.assertTrue(rests_between, "a long gap must stay a rest")

    def test_piano_notes_let_ring(self):
        track = self._parse_single([NoteEvent(60, 0.0, 0.25)], stem="piano")
        notes = [n for _, b in self._flat_beats(track) for n in b.notes]
        self.assertTrue(notes and all(n.effect.letRing for n in notes))
        g = self._parse_single([NoteEvent(60, 0.0, 0.25)], stem="guitar")
        g_notes = [n for _, b in self._flat_beats(g) for n in b.notes]
        self.assertFalse(any(n.effect.letRing for n in g_notes))

    def test_eight_strings_refused_loudly_and_atomically(self):
        from tabforge.core.instruments import profile_for
        from tabforge.export.writers import SongPart, export_song_gp5

        cfg = TabConfig(tuning=(30, 35, 40, 45, 50, 55, 59, 64),
                        max_fret=24)
        notes = [NoteEvent(40, 0.0, 0.4)]
        part = SongPart("g8", assign_tab(notes, cfg), cfg,
                        profile_for("guitar"))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "song.gp5"
            with self.assertRaises(ValueError):
                export_song_gp5([part], path, bpm=120.0)
            self.assertFalse(path.exists(),
                             "a refused export must leave NO file")

    def test_melodic_channels_never_collide_with_percussion(self):
        import guitarpro as gp
        from tabforge.core.instruments import profile_for
        from tabforge.export.writers import SongPart, export_song_gp5

        cfg = TabConfig()
        notes = [NoteEvent(60, 0.0, 0.4)]
        shapes = assign_tab(notes, cfg)
        # NINE parts: the real Techno roster (two guitars, bass, piano
        # split in two hands, vocals, other split, drums) exhausted the
        # single-port 16-channel range and killed song.gp5 — the gp5
        # channel table has 64 slots, use them
        parts = [SongPart(f"m{i}", shapes, cfg, profile_for("guitar"))
                 for i in range(9)]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "song.gp5"
            export_song_gp5(parts, path, bpm=120.0)
            song = gp.parse(str(path))
        used = [t.channel.channel for t in song.tracks[:9]]
        self.assertTrue(all(c % 16 != 9 for c in used),
                        "channel 9 of every port is percussion")
        self.assertEqual(len(used), len(set(used)))


@unittest.skipUnless(HAVE_GP, "PyGuitarPro is not installed")
class TestAdaptiveSubdivision(unittest.TestCase):
    """The durations war (2026-08-30): each measure picks the coarsest
    display grid that keeps its notes distinct — a verse of eighths must
    not shatter, a 32nd solo run must not drag, triplets stay triplets,
    all inside ONE track with no global precision choice."""

    def _render(self, notes):
        import guitarpro as gp
        from tabforge.export.writers import export_gp5
        cfg = TabConfig()
        shapes = assign_tab(notes, cfg)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "test.gp5"
            export_gp5(shapes, path, cfg, bpm=120.0)
            return gp.parse(str(path))

    def test_mixed_material_gets_per_measure_grids(self):
        import guitarpro as gp
        beat = 0.5                              # 120 BPM
        notes = []
        # measure 1: four quarters — must stay COARSE (no junk 32nds)
        for k in range(4):
            notes.append(NoteEvent(60 + k, k * beat, 0.4))
        # measure 2: a 32nd-note run — must get REAL 32nds
        for k in range(16):
            notes.append(NoteEvent(52 + k, 4 * beat + k * beat / 8,
                                   beat / 10))
        # measure 3: eighth-note triplets — must come out as tuplets
        for k in range(6):
            notes.append(NoteEvent(60 + k, 8 * beat + k * beat / 3,
                                   beat / 4))
        song = self._render(notes)
        measures = song.tracks[0].measures

        m1 = [b for b in measures[0].voices[0].beats
              if b.status == gp.BeatStatus.normal]
        self.assertTrue(all(b.duration.value <= 8 for b in m1),
                        [b.duration.value for b in m1])

        m2 = [b for b in measures[1].voices[0].beats
              if b.status == gp.BeatStatus.normal]
        self.assertEqual(len(m2), 16)
        self.assertTrue(any(b.duration.value == 32 for b in m2),
                        [b.duration.value for b in m2])

        m3 = [b for b in measures[2].voices[0].beats
              if b.status == gp.BeatStatus.normal]
        self.assertEqual(len(m3), 6)
        self.assertTrue(all(b.duration.tuplet.enters == 3 for b in m3),
                        [(b.duration.value, b.duration.tuplet.enters)
                         for b in m3])

    def test_noisy_eighths_stay_coarse(self):
        # the rhythm-mess regression (2026-08-30): transcription onsets
        # jitter by ±2 fine units; that must NOT escalate the measure
        # into junk 32nds or fake triplets — the coarse grid absorbs it
        from tabforge.export.writers import pick_subdivision
        jittered = [0, 14, 22, 37, 49, 58, 74, 83]     # 8ths ± jitter
        self.assertEqual(pick_subdivision(jittered), 2)

    def test_lone_flam_does_not_escalate(self):
        # one pair of near-simultaneous attacks amid quarters: a 16th
        # pickup at worst — never a reason for 32nds or fake triplets
        from tabforge.export.writers import pick_subdivision
        self.assertIn(pick_subdivision([0, 4, 24, 48, 72]), (2, 4))

    def test_real_fine_material_still_escalates(self):
        from tabforge.export.writers import pick_subdivision
        # a 32nd run (IOI = 3 fine units)
        self.assertEqual(pick_subdivision(list(range(0, 48, 3))), 8)
        # eighth-note triplets (IOI = 8)
        self.assertEqual(pick_subdivision(list(range(0, 48, 8))), 3)
        # clean sixteenths (IOI = 6)
        self.assertEqual(pick_subdivision(list(range(0, 96, 6))), 4)

    def test_thirtysecond_run_keeps_every_note(self):
        # the user's solo complaint: on a coarse grid consecutive 32nds
        # collapsed/pushed and the passage sounded slowed — every attack
        # must survive at its own position now
        beat = 0.5
        notes = [NoteEvent(52 + k, k * beat / 8, beat / 10)
                 for k in range(16)]
        cfg = TabConfig()
        shapes = assign_tab(notes, cfg)
        _, _, pitches = _roundtrip(shapes, cfg, bpm=120.0)
        self.assertEqual(sorted(pitches), [52 + k for k in range(16)])


if __name__ == "__main__":
    unittest.main()
