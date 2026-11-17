"""Build a contribution arcade from GitHub's public contribution calendar."""
from collections import deque
from datetime import date, datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.request import Request, urlopen
import json
import os
import random
import xml.etree.ElementTree as ET

USER = os.environ["PROFILE_USER"]
STEP_MS = 190
INITIAL_LENGTH = 3
MIN_LENGTH = 2
BOMB_PENALTY = 3
MAX_LENGTH = 0
PALETTES = {
    "light": ["#eff2f5", "#aceebb", "#4ac26b", "#2da44e", "#116329"],
    "dark": ["#151b23", "#033a16", "#196c2e", "#2ea043", "#56d364"],
}

class CalendarParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.cells = {}

    def handle_starttag(self, name, attrs):
        attrs = dict(attrs)
        if name == "td" and "data-date" in attrs and "data-level" in attrs:
            level = int(attrs["data-level"])
            if level not in range(5):
                raise ValueError("Unexpected contribution level")
            self.cells[attrs["data-date"]] = level

request = Request(
    f"https://github.com/users/{USER}/contributions",
    headers={"User-Agent": "contribution-arcade", "Cache-Control": "no-cache"},
)
with urlopen(request, timeout=45) as response:
    parser = CalendarParser()
    parser.feed(response.read().decode("utf-8"))
if len(parser.cells) < 350:
    raise ValueError("GitHub did not return a complete contribution calendar")

first = date.fromisoformat(min(parser.cells))
origin = first - timedelta(days=(first.weekday() + 1) % 7)
calendar = {}
for day, level in sorted(parser.cells.items()):
    d = date.fromisoformat(day)
    calendar[((d - origin).days // 7, (d.weekday() + 1) % 7)] = (day, level)
columns = max(x for x, y in calendar) + 1
available = set(calendar)
food = [p for p, (_, level) in calendar.items() if level > 0]

def distance(a, b):
    return abs(a[0] - b[0]) + abs(a[1] - b[1])

# Keep hazards off real contribution highlights and spread them across the grid.
center = min(available, key=lambda p: (distance(p, (columns // 2, 3)), p))
candidates = [
    p for p in available
    if calendar[p][1] == 0 and 2 <= p[0] < columns - 2
    and 1 <= p[1] <= 5 and distance(p, center) > 3
]
random.Random(33).shuffle(candidates)
near_food = [p for p in candidates if food and min(distance(p, f) for f in food) <= 3]
far_food = [p for p in candidates if p not in near_food]
chosen = []
for pool, limit in [(near_food, 12), (far_food + near_food, 22)]:
    for p in pool:
        if all(max(abs(p[0] - q[0]), abs(p[1] - q[1])) > 1 for q in chosen):
            chosen.append(p)
        if len(chosen) >= limit:
            break
obstacles = {p: ("bomb" if i % 3 == 0 else "hurdle") for i, p in enumerate(chosen)}
bombs = [p for p in chosen if obstacles[p] == "bomb"]
hurdles = [p for p in chosen if obstacles[p] == "hurdle"]

def route(start, end, blocked):
    queue = deque([start])
    previous = {start: None}
    while queue:
        p = queue.popleft()
        if p == end:
            result = []
            while p != start:
                result.append(p)
                p = previous[p]
            return result[::-1]
        x, y = p
        neighbors = [(x + 1, y), (x, y + 1), (x - 1, y), (x, y - 1)]
        neighbors.sort(key=lambda q: distance(q, end))
        for q in neighbors:
            if q in available and q not in previous and (q not in blocked or q == end):
                previous[q] = p
                queue.append(q)
    return None

# Separate the feeding lanes so the actors do not crowd the same corner.
foods = {
    "snake": [p for p in food if p[1] >= 3],
    "pacman": [p for p in food if p[1] < 3],
}
MAX_LENGTH = INITIAL_LENGTH + len(foods["snake"])
food_events = {}
collisions = []

class Actor:
    def __init__(self, name, start):
        self.name = name
        self.pos = start
        self.angle = 0
        self.length = INITIAL_LENGTH
        self.trail = [(max(0, start[0] - i), start[1]) for i in range(MAX_LENGTH)]
        self.frames = []
        self.exploded = set()
        self.record()

    def record(self):
        self.frames.append({
            "pos": self.pos, "angle": self.angle,
            "length": self.length, "trail": tuple(self.trail),
        })

    def consume(self, p):
        if p in foods[self.name] and p not in food_events:
            if self.name == "snake":
                self.length += 1
                self.frames[-1]["length"] = self.length
            food_events[p] = {"actor": self.name, "frame": len(self.frames) - 1}

    def pause(self, count):
        for _ in range(count):
            self.record()

    def move(self, destination):
        dx, dy = destination[0] - self.pos[0], destination[1] - self.pos[1]
        if dx or dy:
            target_angle = {(1, 0): 0, (0, 1): 90, (-1, 0): 180, (0, -1): 270}[(dx, dy)]
            self.angle += (target_angle - self.angle + 180) % 360 - 180
            self.pos = destination
            self.trail = ([destination] + self.trail)[:MAX_LENGTH]
        self.record()
        self.consume(destination)

    def travel(self, destination):
        blocked = set(obstacles) - self.exploded
        in_lane = lambda p: p[1] >= 3 if self.name == "snake" else p[1] < 3
        outside_lane = {p for p in available if not in_lane(p)}
        lane_blocks = outside_lane if in_lane(self.pos) and in_lane(destination) else set()
        path = route(self.pos, destination, blocked | lane_blocks)
        if path is None:
            path = route(self.pos, destination, lane_blocks)
        if path is None:
            # Keep even a dense future contribution calendar playable.
            path = route(self.pos, destination, set())
        if path is None:
            raise ValueError("Contribution cells are not connected")
        for p in path:
            self.move(p)
            kind = obstacles.get(p)
            if kind and (kind != "bomb" or p not in self.exploded):
                before = self.length
                if kind == "bomb":
                    self.exploded.add(p)
                    if self.name == "snake":
                        self.length = max(MIN_LENGTH, self.length - BOMB_PENALTY)
                        self.frames[-1]["length"] = self.length
                collisions.append({
                    "actor": self.name, "kind": kind,
                    "cell": p, "frame": len(self.frames) - 1,
                    "length_before": before, "length_after": self.length,
                })
                self.pause(4 if kind == "bomb" else 1)

    def eat(self, p):
        self.travel(p)
        self.consume(p)

    def bump(self, p):
        self.travel(p)
        # Retreat one square after impact, then resume hunting food.
        if len(self.trail) > 1 and distance(self.pos, self.trail[1]) == 1:
            self.move(self.trail[1])

def starting_cell(name):
    targets = foods[name]
    if targets:
        target = min(targets) if name == "snake" else max(targets)
        wanted = (target[0] - 3, target[1]) if name == "snake" else (target[0] - 2, target[1])
    else:
        wanted = (2, 3) if name == "snake" else (columns - 3, 3)
    safe = available - set(obstacles) - set(food)
    return min(safe or available, key=lambda p: (distance(p, wanted), p))

actors = {name: Actor(name, starting_cell(name)) for name in foods}
pending_food = {name: set(targets) for name, targets in foods.items()}
approaches = {"snake": (center[0], center[1] + 2), "pacman": (center[0], center[1] - 2)}

# Let viewers see feeding first, then stage one shared collision in the middle.
for name, actor in actors.items():
    pending = pending_food[name]
    while sum(event["actor"] == name for event in food_events.values()) < 3:
        pending.difference_update(food_events)
        if not pending:
            break
        target = min(pending, key=lambda p: (distance(actor.pos, p), p))
        actor.eat(target)
        pending.discard(target)
    actor.travel(approaches[name])

meeting_frame = max(len(a.frames) for a in actors.values())
for actor in actors.values():
    actor.pause(meeting_frame - len(actor.frames))
for offset in (1, 0):
    actors["snake"].move((center[0], center[1] + offset))
    actors["pacman"].move((center[0], center[1] - offset))
collisions.append({
    "actor": "both", "kind": "duel", "cell": center,
    "frame": len(actors["snake"].frames) - 1,
})
for actor in actors.values():
    actor.pause(4)
actors["snake"].move((center[0], center[1] + 1))
actors["pacman"].move((center[0], center[1] - 1))

# After the central bump, each actor returns to its own feeding lane.
for name, actor in actors.items():
    pending = pending_food[name]
    in_lane = lambda p: p[1] >= 3 if name == "snake" else p[1] < 3
    actor_bombs = [p for p in bombs if in_lane(p)]
    actor_hurdles = [p for p in hurdles if in_lane(p)]
    visits = []
    if actor_hurdles:
        visits.append((1, actor_hurdles[0]))
    if actor_bombs:
        visits.append((2, actor_bombs[0]))
    if len(actor_hurdles) > 1:
        visits.append((4, actor_hurdles[1]))
    if len(actor_bombs) > 1:
        visits.append((5, actor_bombs[1]))
    fed_before = sum(event["actor"] == name for event in food_events.values())
    while pending:
        pending.difference_update(food_events)
        if not pending:
            break
        target = min(pending, key=lambda p: (distance(actor.pos, p), p))
        actor.eat(target)
        pending.discard(target)
        pickups = sum(event["actor"] == name for event in food_events.values()) - fed_before
        for threshold, hazard in list(visits):
            if pickups >= threshold:
                actor.bump(hazard)
                visits.remove((threshold, hazard))
    for threshold, hazard in visits:
        actor.bump(hazard)

frame_count = max(len(a.frames) for a in actors.values()) + 12
duration = frame_count * STEP_MS
for actor in actors.values():
    actor.pause(frame_count - len(actor.frames))

# Replay invariants: real highlights stay fixed, movements stay inside the grid,
# food is consumed once, and snake growth is tied to its own pickups.
assert set(food_events) == set(food)
assert all(calendar[p][1] == 0 for p in obstacles)
assert all(frame["pos"] in available for a in actors.values() for frame in a.frames)
snake_lengths = [f["length"] for f in actors["snake"].frames]
assert snake_lengths[0] == INITIAL_LENGTH
growth_frames = {event["frame"] for event in food_events.values() if event["actor"] == "snake"}
bomb_frames = {event["frame"]: event for event in collisions if event["actor"] == "snake" and event["kind"] == "bomb"}
for i, (before, after) in enumerate(zip(snake_lengths, snake_lengths[1:]), start=1):
    if i in growth_frames:
        assert after == before + 1
    elif i in bomb_frames:
        assert after == max(MIN_LENGTH, before - BOMB_PENALTY)
    else:
        assert after == before
duels = [event for event in collisions if event["kind"] == "duel"]
assert len(duels) == 1 and duels[0]["cell"] == center
assert actors["snake"].frames[duels[0]["frame"]]["pos"] == center
assert actors["pacman"].frames[duels[0]["frame"]]["pos"] == center
assert all(event["kind"] == "duel" or event["cell"] in obstacles for event in collisions)

NS = "http://www.w3.org/2000/svg"
ET.register_namespace("", NS)
def element(parent, name, **attrs):
    return ET.SubElement(parent, f"{{{NS}}}{name}", {k: str(v) for k, v in attrs.items()})

def percent(frame):
    return frame / (frame_count - 1) * 100

def keyframes(name, values):
    keys = []
    for i, value in enumerate(values):
        if i in (0, len(values) - 1) or value != values[i - 1] or value != values[i + 1]:
            keys.append(f"{i / (len(values) - 1) * 100:.5f}%{{{value}}}")
    return f"@keyframes {name}{{" + "".join(keys) + "}"

def motion_values(frames, segment=None):
    """Interpolate four samples per step for rounded, continuous movement."""
    values = []
    def position(frame):
        return frame["pos"] if segment is None else frame["trail"][segment]
    def spline(a, b, c, d, t):
        return .5 * (2*b + (-a+c)*t + (2*a-5*b+4*c-d)*t*t + (-a+3*b-3*c+d)*t*t*t)
    for i in range(len(frames) - 1):
        p = [position(frames[j]) for j in (max(0, i-1), i, i+1, min(len(frames)-1, i+2))]
        for sample in range(4):
            t = sample / 4
            x = min(columns * 16 - 8, max(8, spline(*(q[0] * 16 + 8 for q in p), t)))
            y = min(104, max(8, spline(*(q[1] * 16 + 8 for q in p), t)))
            text = f"transform:translate({x:.3f}px,{y:.3f}px)"
            if segment is None:
                ease = t*t*(3-2*t)
                angle = frames[i]["angle"] + (frames[i+1]["angle"]-frames[i]["angle"]) * ease
                text += f" rotate({angle:.3f}deg)"
            else:
                before = 1 if segment < frames[i]["length"] else 0
                after = 1 if segment < frames[i+1]["length"] else 0
                blend = max(0, (t - .5) * 2)
                text += f";opacity:{before + (after-before)*blend:.3f}"
            values.append(text)
    frame = frames[-1]
    x, y = position(frame)
    text = f"transform:translate({x*16+8}px,{y*16+8}px)"
    if segment is None:
        text += f" rotate({frame['angle']}deg)"
    else:
        text += ";opacity:1" if segment < frame["length"] else ";opacity:0"
    values.append(text)
    return values

def flash_frames(actor):
    values = ["fill:var(--actor-color)" for _ in range(frame_count)]
    for event in collisions:
        if event["actor"] in (actor.name, "both"):
            for offset, color in enumerate(["#ffffff", "#fb7185", "#ffffff", "#fb7185"]):
                i = event["frame"] + offset
                if i < frame_count:
                    values[i] = f"fill:{color}"
    return values

Path("dist").mkdir(exist_ok=True)
for theme, palette in PALETTES.items():
    css = [
        ":root{" + "".join(f"--c{i}:{color};" for i, color in enumerate(palette)) + "}",
        ".cell{stroke:#1b1f2310;stroke-width:.6}",
        ".food{fill:#fff7ad;stroke:#854d0e;stroke-width:.6}",
        ".spark{animation:fuse .6s steps(2) infinite}",
        "@keyframes fuse{0%,100%{opacity:1}50%{opacity:.2}}",
        ".pac-open{animation:mouth-open .24s steps(1) infinite}",
        ".pac-closed{animation:mouth-closed .24s steps(1) infinite}",
        "@keyframes mouth-open{0%,100%{opacity:1}50%{opacity:0}}",
        "@keyframes mouth-closed{0%,100%{opacity:0}50%{opacity:1}}",
        f".actor{{animation:loop-fade {duration}ms linear infinite}}",
        "@keyframes loop-fade{0%,98%{opacity:1}100%{opacity:0}}",
    ]
    root = ET.Element(f"{{{NS}}}svg", {
        "viewBox": f"-20 -20 {columns * 16 + 40} 152",
        "width": str(columns * 16 + 40), "height": "152",
        "role": "img", "aria-label": "Growing snake and Pac-Man collecting food with hurdles and exploding bombs on GitHub contributions",
    })
    element(root, "title").text = "Suraj's contribution arcade"
    style = element(root, "style")
    grid = element(root, "g", id="contribution-grid")
    food_ids = {p: index for index, p in enumerate(food)}
    for (x, y), (day, level) in calendar.items():
        cell = element(grid, "rect", **{
            "class": "cell", "x": x * 16 + 1, "y": y * 16 + 1,
            "width": 14, "height": 14, "rx": 2, "ry": 2,
            "fill": f"var(--c{level})", "data-date": day, "data-level": level,
        })
        if (x, y) in food_events:
            cell_id = f"cell-{food_ids[(x, y)]}"
            cell.set("id", cell_id)
            eaten = food_events[(x, y)]["frame"]
            values = [f"fill:var(--c{level})" if i < eaten else "fill:var(--c0)" for i in range(frame_count)]
            css.extend([keyframes(cell_id, values), f"#{cell_id}{{animation:{cell_id} {duration}ms steps(1) infinite}}"])
    for index, p in enumerate(food):
        food_name = f"food-{index}"
        event = food_events[p]
        element(root, "circle", **{
            "class": "food", "cx": p[0] * 16 + 8, "cy": p[1] * 16 + 8,
            "r": 3.2, "id": food_name, "data-date": calendar[p][0],
        })
        values = ["opacity:1" if i < event["frame"] else "opacity:0" for i in range(frame_count)]
        css.extend([keyframes(food_name, values), f"#{food_name}{{animation:{food_name} {duration}ms linear infinite}}"])
    for index, (p, kind) in enumerate(obstacles.items()):
        hazard_id = f"hazard-{index}"
        obj = element(root, "g", **{
            "class": kind, "id": hazard_id,
            "transform": f"translate({p[0] * 16},{p[1] * 16})",
            "data-date": calendar[p][0],
        })
        icon = element(obj, "g", transform="translate(-2.4,-2.4) scale(1.3)")
        if kind == "bomb":
            element(icon, "circle", cx=8, cy=9, r=4.5, fill="#334155", stroke="#94a3b8", **{"stroke-width": .7})
            element(icon, "path", d="M10 5Q13 2 12 1", fill="none", stroke="#f59e0b", **{"stroke-width": 1.2})
            element(icon, "path", d="M12 0V3M10.5 1.5H13.5", stroke="#fbbf24", **{"class": "spark", "stroke-width": 1})
            element(icon, "circle", cx=6.5, cy=7.5, r=1, fill="#cbd5e1")
            hit = min((e["frame"] for e in collisions if e["cell"] == p and e["kind"] == "bomb"), default=None)
            if hit is not None:
                values = ["opacity:1" if i < hit else "opacity:0" for i in range(frame_count)]
                css.extend([keyframes(hazard_id, values), f"#{hazard_id}{{animation:{hazard_id} {duration}ms linear infinite}}"])
        else:
            element(icon, "rect", x=2, y=4, width=2, height=10, rx=.5, fill="#d97706")
            element(icon, "rect", x=12, y=4, width=2, height=10, rx=.5, fill="#d97706")
            element(icon, "rect", x=1, y=4, width=14, height=3, rx=1, fill="#fb923c")
            element(icon, "path", d="M4 4L7 7M9 4L12 7", stroke="#fff7ed", **{"stroke-width": 1})

    snake = actors["snake"]
    snake_group = element(root, "g", **{"id": "snake", "class": "actor"})
    for segment in range(max(snake_lengths) - 1, 0, -1):
        segment_id = f"snake-body-{segment}"
        group = element(snake_group, "g", id=segment_id)
        element(group, "rect", x=-6, y=-6, width=12, height=12, rx=4.5, fill="#a855f7", stroke="#7e22ce", **{"stroke-width": .6})
        element(group, "circle", cx=-1.7, cy=-1.7, r=1, fill="#d8b4fe")
        values = motion_values(snake.frames, segment)
        css.extend([keyframes(segment_id, values), f"#{segment_id}{{animation:{segment_id} {duration}ms linear infinite}}"])
    head = element(snake_group, "g", id="snake-head")
    face = element(head, "g", id="snake-face", transform="scale(1.3)", style="--actor-color:#a855f7;fill:var(--actor-color)")
    element(face, "rect", x=-6, y=-6, width=12, height=12, rx=4.5, stroke="#7e22ce", **{"stroke-width": .6})
    element(face, "circle", cx=3, cy=-3, r=1.5, fill="white")
    element(face, "circle", cx=3.4, cy=-3, r=.7, fill="#111827")
    element(face, "circle", cx=3, cy=3, r=1.5, fill="white")
    element(face, "circle", cx=3.4, cy=3, r=.7, fill="#111827")
    element(face, "path", d="M6 0H9M9 0L10 -1M9 0L10 1", stroke="#fb7185", **{"stroke-width": .7})
    css.extend([
        keyframes("snake-head", motion_values(snake.frames)),
        f"#snake-head{{animation:snake-head {duration}ms linear infinite}}",
        keyframes("snake-flash", flash_frames(snake)),
        f"#snake-face{{animation:snake-flash {duration}ms linear infinite}}",
    ])
    pac = actors["pacman"]
    pac_group = element(root, "g", **{"id": "pacman-actor", "class": "actor"})
    pacman = element(pac_group, "g", id="pacman")
    face = element(pacman, "g", id="pacman-face", transform="scale(1.3)", style="--actor-color:#facc15;fill:var(--actor-color)")
    element(face, "path", **{"class": "pac-open", "d": "M0 0L5.2 -4A6.6 6.6 0 1 0 5.2 4Z", "stroke": "#a16207", "stroke-width": .6})
    element(face, "circle", **{"class": "pac-closed", "cx": 0, "cy": 0, "r": 6.6, "stroke": "#a16207", "stroke-width": .6})
    element(face, "circle", cx=1, cy=-3.2, r=1, fill="#111827")
    css.extend([
        keyframes("pacman-route", motion_values(pac.frames)),
        f"#pacman{{animation:pacman-route {duration}ms linear infinite}}",
        keyframes("pacman-flash", flash_frames(pac)),
        f"#pacman-face{{animation:pacman-flash {duration}ms linear infinite}}",
    ])

    for index, event in enumerate(collisions):
        name = f"impact-{index}"
        p = event["cell"]
        effect_class = "explosion" if event["kind"] == "bomb" else "duel" if event["kind"] == "duel" else "impact"
        blast = element(root, "g", **{"class": effect_class, "id": name})
        if event["kind"] == "bomb":
            element(blast, "path", d="M0 -18L4 -8L14 -13L9 -3L19 1L9 5L13 16L3 10L-3 19L-6 8L-17 11L-10 1L-18 -7L-7 -6L-7 -17L-1 -9Z", fill="#fb923c")
            element(blast, "circle", cx=0, cy=0, r=7, fill="#fef08a")
            element(blast, "circle", cx=0, cy=0, r=3.5, fill="white")
            for dx, dy in [(18, 4), (-16, -5), (5, -19), (-6, 18)]:
                element(blast, "circle", cx=dx, cy=dy, r=1.8, fill="#fde047")
        elif event["kind"] == "duel":
            element(blast, "path", d="M0 -16L4 -7L13 -12L8 -3L16 0L8 5L12 13L3 9L0 17L-4 8L-13 12L-8 2L-16 -2L-7 -6L-10 -14L-2 -8Z", fill="#fde047", stroke="#a855f7", **{"stroke-width": 1})
            element(blast, "circle", cx=0, cy=0, r=5, fill="white")
        else:
            element(blast, "path", d="M-9 -9L-5 -5M9 -9L5 -5M-9 9L-5 5M9 9L5 5", stroke="#fbbf24", **{"stroke-width": 2})
        values = []
        for i in range(frame_count):
            delta = i - event["frame"]
            active = 0 <= delta <= 5
            scale = .3 + delta * .23 if active else .1
            opacity = max(0, 1 - delta / 6) if active else 0
            values.append(f"transform:translate({p[0] * 16 + 8}px,{p[1] * 16 + 8}px) scale({scale:.2f});opacity:{opacity:.2f}")
        css.extend([keyframes(name, values), f"#{name}{{animation:{name} {duration}ms linear infinite}}"])
    style.text = "".join(css)
    actual = {e.get("data-date"): int(e.get("data-level")) for e in grid}
    assert actual == parser.cells
    filename = "github-contribution-grid-snake" + ("-dark" if theme == "dark" else "") + ".svg"
    ET.ElementTree(root).write(Path("dist") / filename, encoding="unicode")

metadata = {
    "generated_at": datetime.now(timezone.utc).isoformat(),
    "source": "GitHub public contribution calendar",
    "duration_ms": duration,
    "step_ms": STEP_MS,
    "smooth_samples_per_step": 4,
    "central_collision": {"cell": center, "frame": duels[0]["frame"]},
    "snake_initial_length": INITIAL_LENGTH,
    "snake_final_length": snake_lengths[-1],
    "snake_peak_length": max(snake_lengths),
    "snake_growth": [{"frame": i, "length": length} for i, length in enumerate(snake_lengths) if i == 0 or length != snake_lengths[i-1]],
    "food": [{"date": calendar[p][0], **event} for p, event in food_events.items()],
    "collisions": [{**event, "date": calendar[event["cell"]][0]} for event in collisions],
    "cells": [{"date": day, "level": level} for day, level in sorted(parser.cells.items())],
}
Path("dist/contribution-calendar.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
print(f"Validated {len(calendar)} dates; snake grows {INITIAL_LENGTH} to {snake_lengths[-1]}; "
      f"{len(food_events)} pickups, {len(collisions)} collisions; both characters stay inside.")
