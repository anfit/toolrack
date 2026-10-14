#!/usr/bin/env python3
"""Render the deterministic animated terminal walkthrough used by the README."""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).parents[1]
OUTPUT = ROOT / "docs" / "assets" / "toolrack-demo.gif"
SIZE = (960, 540)
FONT_PATHS = {
    False: (Path("C:/Windows/Fonts/consola.ttf"), "DejaVuSansMono.ttf"),
    True: (Path("C:/Windows/Fonts/consolab.ttf"), "DejaVuSansMono-Bold.ttf"),
}

BG = "#08111f"
PANEL = "#111b2e"
PANEL_EDGE = "#263650"
TEXT = "#e6edf7"
MUTED = "#8ea2bf"
GREEN = "#67e8a5"
CYAN = "#67d5ff"
AMBER = "#f6c85f"


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    preferred, fallback = FONT_PATHS[bold]
    return ImageFont.truetype(str(preferred) if preferred.is_file() else fallback, size)


TITLE = font(25, bold=True)
LABEL = font(16, bold=True)
MONO = font(20)
MONO_BOLD = font(20, bold=True)
SMALL = font(15)


SCENES = [
    (
        "REGISTER",
        [
            ("$ my-tools core register scripts/github/list_prs.py", GREEN),
            ("Loaded sidecar: list_prs.yml", TEXT),
            ("Registered github/list_prs.py", CYAN),
            ("", TEXT),
            ("$ my-tools core list", GREEN),
            ("github/list_prs.py    sidecar ok", TEXT),
            ("jira/show.py          sidecar ok", TEXT),
            ("db/query.py           sidecar ok", TEXT),
        ],
        "One registry line per script, one sidecar beside it",
    ),
    (
        "DISCOVER",
        [
            ("$ my-tools --help", GREEN),
            ("Commands:", AMBER),
            ("  core    Manage the script registry", TEXT),
            ("  db      query", TEXT),
            ("  github  list-prs", TEXT),
            ("  jira    show", TEXT),
            ("", TEXT),
            ("$ my-tools github list-prs --help", GREEN),
            ("  --state [open|closed]   --limit INTEGER", MUTED),
        ],
        "Typed arguments, help, and completion for free",
    ),
    (
        "RUN",
        [
            ("$ my-tools github list-prs --state open --limit 3", GREEN),
            ("#42  Refactor shell path handling      open", TEXT),
            ("#41  Defer CLI tree construction       open", TEXT),
            ("#39  Add sidecar validation            open", TEXT),
            ("", TEXT),
            ("$ my-tools jira show ISSUE-123", GREEN),
            ("ISSUE-123  In Progress  Tidy the script folder", TEXT),
            ("exit code: 0", GREEN),
        ],
        "Tiny scripts, one coherent CLI on your PATH",
    ),
]


def frame(scene_index: int, visible_lines: int) -> Image.Image:
    image = Image.new("RGB", SIZE, BG)
    draw = ImageDraw.Draw(image)
    label, lines, caption = SCENES[scene_index]

    draw.text((48, 32), "A TYPED CLI FOR YOUR SCRIPT REPO", font=LABEL, fill=CYAN)
    draw.text((48, 58), "Register a script. Get a command, help, and completion.",
              font=TITLE, fill=TEXT)

    panel = (48, 112, 912, 456)
    draw.rounded_rectangle(panel, radius=14, fill=PANEL, outline=PANEL_EDGE, width=2)
    draw.ellipse((68, 132, 80, 144), fill="#ff6b6b")
    draw.ellipse((88, 132, 100, 144), fill="#ffd166")
    draw.ellipse((108, 132, 120, 144), fill="#67e8a5")
    draw.text((138, 126), "toolrack  /  my-tools", font=SMALL, fill=MUTED)

    y = 168
    for text, color in lines[:visible_lines]:
        selected = MONO_BOLD if text.startswith("$") else MONO
        draw.text((72, y), text, font=selected, fill=color)
        y += 30

    progress = ["REGISTER", "DISCOVER", "RUN"]
    x = 50
    for index, step in enumerate(progress):
        color = GREEN if index < scene_index else CYAN if index == scene_index else MUTED
        draw.text((x, 480), step, font=LABEL, fill=color)
        x += 28 + 10 * len(step)
        if index < len(progress) - 1:
            draw.line((x - 20, 489, x - 4, 489), fill=PANEL_EDGE, width=2)
            x += 8
    draw.text((330, 480), caption, font=SMALL, fill=MUTED)
    return image


def render() -> None:
    frames: list[Image.Image] = []
    durations: list[int] = []
    for scene_index, (_, lines, _) in enumerate(SCENES):
        for visible in range(1, len(lines) + 1):
            frames.append(frame(scene_index, visible))
            durations.append(180 if visible < len(lines) else 2100)

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    palette = frames[0].convert("P", palette=Image.Palette.ADAPTIVE, colors=128)
    indexed = [item.quantize(palette=palette, dither=Image.Dither.NONE) for item in frames]
    indexed[0].save(OUTPUT, save_all=True, append_images=indexed[1:], duration=durations,
                    loop=0, optimize=True, disposal=2)
    print(f"rendered {OUTPUT} ({len(frames)} frames, {OUTPUT.stat().st_size} bytes)")


if __name__ == "__main__":
    render()
