"""
Ядро проекта: перевод последовательности нот (pitch + время) в аппликатуру
на грифе — то есть в табулатуру.

Это НЕ решается жадно "взять ближайший лад": выбор для текущей ноты зависит
от того, где рука окажется через 3 ноты. Поэтому — динамическое
программирование по всей последовательности (алгоритм Витерби).
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from typing import Iterable, Sequence

# MIDI-номера открытых струн. Индекс 0 = самая НИЗКАЯ струна (6-я).
TUNINGS: dict[str, tuple[int, ...]] = {
    "standard": (40, 45, 50, 55, 59, 64),      # E2 A2 D3 G3 B3 E4
    "drop_d": (38, 45, 50, 55, 59, 64),
    "eb_standard": (39, 44, 49, 54, 58, 63),   # полтона вниз
    "dadgad": (38, 45, 50, 55, 57, 62),
    "open_g": (38, 43, 50, 55, 59, 62),
    "bass_4": (28, 33, 38, 43),                # E1 A1 D2 G2
    "bass_5": (23, 28, 33, 38, 43),
    "ukulele": (67, 60, 64, 69),
}


@dataclass(slots=True)
class NoteEvent:
    """Нота после транскрипции аудио."""
    pitch: int          # MIDI note number
    start: float        # секунды
    duration: float     # секунды
    velocity: int = 96

    @property
    def end(self) -> float:
        return self.start + self.duration


@dataclass(slots=True)
class Placement:
    """Нота, привязанная к струне и ладу."""
    note: NoteEvent
    string: int         # индекс в tuning, 0 = самая низкая
    fret: int


@dataclass(slots=True)
class Shape:
    """Один аккорд/событие: набор одновременных зажатий."""
    start: float
    placements: list[Placement] = field(default_factory=list)

    @property
    def hand_position(self) -> float:
        """Где находится указательный палец. Открытые струны не считаем."""
        frets = [p.fret for p in self.placements if p.fret > 0]
        return min(frets) if frets else 0.0

    @property
    def span(self) -> int:
        frets = [p.fret for p in self.placements if p.fret > 0]
        return (max(frets) - min(frets)) if frets else 0


@dataclass(slots=True)
class TabConfig:
    tuning: tuple[int, ...] = TUNINGS["standard"]
    max_fret: int = 22
    reach: int = 3              # лады pos..pos+reach берутся без растяжки
    max_stretch: int = 5        # предельная растяжка со штрафом
    open_string_bonus: float = 0.35
    high_fret_penalty: float = 0.05   # тянет играть ближе к порожку
    stretch_penalty: float = 1.2      # за каждый лад сверх reach
    move_penalty: float = 0.55        # за лад смещения руки
    string_change_penalty: float = 0.10
    beam_width: int = 80
    onset_tolerance: float = 0.045    # ноты ближе этого = один аккорд


# ---------------------------------------------------------------------------
# 1. Группировка в события
# ---------------------------------------------------------------------------

def group_into_events(notes: Sequence[NoteEvent], tolerance: float) -> list[list[NoteEvent]]:
    """Ноты, начинающиеся почти одновременно, — это аккорд."""
    if not notes:
        return []
    ordered = sorted(notes, key=lambda n: (n.start, n.pitch))
    events: list[list[NoteEvent]] = [[ordered[0]]]
    for note in ordered[1:]:
        if note.start - events[-1][0].start <= tolerance:
            events[-1].append(note)
        else:
            events.append([note])
    return events


# ---------------------------------------------------------------------------
# 2. Генерация вариантов аппликатуры для события
# ---------------------------------------------------------------------------

def candidates_for_pitch(pitch: int, cfg: TabConfig) -> list[tuple[int, int]]:
    """Все (струна, лад), дающие эту высоту."""
    out = []
    for s, open_pitch in enumerate(cfg.tuning):
        fret = pitch - open_pitch
        if 0 <= fret <= cfg.max_fret:
            out.append((s, fret))
    return out


def shapes_for_event(event: Sequence[NoteEvent], cfg: TabConfig) -> list[Shape]:
    """Все физически возможные способы взять этот аккорд."""
    per_note = [candidates_for_pitch(n.pitch, cfg) for n in event]
    if any(not c for c in per_note):
        # какая-то нота вне диапазона инструмента — выкидываем её
        keep = [(n, c) for n, c in zip(event, per_note) if c]
        if not keep:
            return []
        event = [n for n, _ in keep]
        per_note = [c for _, c in keep]

    shapes: list[Shape] = []
    for combo in itertools.product(*per_note):
        strings = [s for s, _ in combo]
        if len(set(strings)) != len(strings):
            continue  # две ноты на одной струне — невозможно
        frets = [f for _, f in combo if f > 0]
        if frets and max(frets) - min(frets) > cfg.max_stretch:
            continue  # рука так не растянется
        shapes.append(
            Shape(
                start=event[0].start,
                placements=[Placement(n, s, f) for n, (s, f) in zip(event, combo)],
            )
        )
    return shapes


# ---------------------------------------------------------------------------
# 3. Стоимости. Ключевая идея: состояние — не только аппликатура, но и
#    ПОЗИЦИЯ руки. Гитарист держит кисть на месте и работает пальцами,
#    а не переползает к каждой ноте.
# ---------------------------------------------------------------------------

def positions_for_shape(shape: Shape, cfg: TabConfig) -> list[int]:
    """В каких позициях руки этот аккорд вообще берётся."""
    frets = [p.fret for p in shape.placements if p.fret > 0]
    max_pos = max(0, cfg.max_fret - cfg.reach)
    if not frets:
        return list(range(0, max_pos + 1))       # всё открытое — рука где угодно
    lo, hi = min(frets), max(frets)
    if hi - lo > cfg.max_stretch:
        return []
    first = max(0, hi - cfg.max_stretch)
    last = min(lo, max_pos)
    return list(range(first, last + 1)) or [max(0, min(lo, max_pos))]


def static_cost(shape: Shape, pos: int, cfg: TabConfig) -> float:
    cost = cfg.high_fret_penalty * pos
    for p in shape.placements:
        if p.fret == 0:
            cost -= cfg.open_string_bonus
            continue
        finger = p.fret - pos
        if finger < 0:
            cost += cfg.stretch_penalty * (-finger)          # позади позиции
        elif finger > cfg.reach:
            cost += cfg.stretch_penalty * (finger - cfg.reach)
    return cost


def transition_cost(prev: Shape, prev_pos: int, cur: Shape, pos: int,
                    cfg: TabConfig) -> float:
    gap = max(cur.start - prev.start, 1e-3)
    time_factor = 1.0 / (1.0 + 3.0 * gap)    # в быстром пассаже прыжки дороже
    cost = cfg.move_penalty * abs(pos - prev_pos) * time_factor
    prev_strings = {p.string for p in prev.placements}
    cur_strings = {p.string for p in cur.placements}
    cost += cfg.string_change_penalty * len(cur_strings ^ prev_strings) * time_factor
    return cost


# ---------------------------------------------------------------------------
# 4. Витерби (beam search) по последовательности событий
# ---------------------------------------------------------------------------

def assign_tab(notes: Sequence[NoteEvent], cfg: TabConfig | None = None) -> list[Shape]:
    cfg = cfg or TabConfig()
    events = group_into_events(notes, cfg.onset_tolerance)
    if not events:
        return []

    # состояние: (стоимость, shape, позиция руки, индекс предка)
    State = tuple[float, Shape, int, int]
    history: list[list[State]] = []

    beam: list[State] = []
    for shape in shapes_for_event(events[0], cfg):
        for pos in positions_for_shape(shape, cfg):
            beam.append((static_cost(shape, pos, cfg), shape, pos, -1))
    if not beam:
        return []
    beam = sorted(beam, key=lambda x: x[0])[: cfg.beam_width]
    history.append(beam)

    for event in events[1:]:
        options = [(sh, p) for sh in shapes_for_event(event, cfg)
                   for p in positions_for_shape(sh, cfg)]
        if not options:
            continue
        new_beam: list[State] = []
        for shape, pos in options:
            base = static_cost(shape, pos, cfg)
            best_cost, best_idx = float("inf"), 0
            for idx, (acc, prev_shape, prev_pos, _) in enumerate(beam):
                c = acc + base + transition_cost(prev_shape, prev_pos, shape, pos, cfg)
                if c < best_cost:
                    best_cost, best_idx = c, idx
            new_beam.append((best_cost, shape, pos, best_idx))
        beam = sorted(new_beam, key=lambda x: x[0])[: cfg.beam_width]
        history.append(beam)

    result: list[Shape] = []
    idx = 0
    for step in reversed(range(len(history))):
        _cost, shape, _pos, back = history[step][idx]
        result.append(shape)
        idx = back if back >= 0 else 0
    result.reverse()
    return result


# 5. ASCII-таб для быстрой проверки глазами
# ---------------------------------------------------------------------------

STRING_NAMES = {
    6: ["E", "A", "D", "G", "B", "e"],
    4: ["E", "A", "D", "G"],
    5: ["B", "E", "A", "D", "G"],
}


def render_ascii(shapes: Sequence[Shape], cfg: TabConfig | None = None,
                 wrap: int = 16) -> str:
    cfg = cfg or TabConfig()
    n = len(cfg.tuning)
    names = STRING_NAMES.get(n, [str(i) for i in range(n)])

    columns: list[list[str]] = []
    for shape in shapes:
        col = ["-"] * n
        for p in shape.placements:
            col[p.string] = str(p.fret)
        width = max(len(c) for c in col)
        columns.append([c.rjust(width, "-") if c != "-" else "-" * width for c in col])

    blocks = []
    for i in range(0, len(columns), wrap):
        chunk = columns[i : i + wrap]
        lines = []
        for s in reversed(range(n)):          # верхняя строка = тонкая струна
            body = "-".join(col[s] for col in chunk)
            lines.append(f"{names[s]}|-{body}-|")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)
