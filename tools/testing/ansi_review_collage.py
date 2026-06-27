#!/usr/bin/env python3
"""Build human-review PNG collages from ANSI terminal renders.

This is a manual visual-review utility. It intentionally lives outside the
package runtime and keeps Pillow/Rich as optional tool dependencies.
"""

from __future__ import annotations

import argparse
import ast
import io
import json
import os
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    from PIL import Image, ImageDraw, ImageFont
except ModuleNotFoundError as exc:  # pragma: no cover - manual tool dependency
    raise SystemExit("ansi_review_collage requires Pillow: python -m pip install pillow") from exc

try:
    from rich.color import blend_rgb
    from rich.console import SVG_EXPORT_THEME, Console
    from rich.segment import Segment
    from rich.style import Style
    from rich.text import Text
except ModuleNotFoundError as exc:  # pragma: no cover - manual tool dependency
    raise SystemExit("ansi_review_collage requires Rich: python -m pip install rich") from exc


REPO_ROOT = Path(__file__).resolve().parents[2]
INKTUI_DIR = REPO_ROOT / "inktui"
PANE_FIXTURE_RENDERER = "fixtures/print-pane-fixture.ts"

BG = (30, 30, 30, 255)
TERMINAL_BG = (30, 30, 30)
RIGHT_PAD = 6
SIZE_PAIR_LEN = 2
BOX_SAMPLE_CHARS = "MW┏┓┛┃━│"
BOX_SEGMENTS = {
    "━": ("left", "right"),
    "┃": ("up", "down"),
    "┏": ("right", "down"),
    "┓": ("left", "down"),
    "┗": ("right", "up"),
    "┛": ("left", "up"),
    "┳": ("left", "right", "down"),
}


@dataclass(frozen=True)
class RenderOptions:
    pixel_scale: int = 3
    terminal_bg: tuple[int, int, int] = TERMINAL_BG
    right_pad: int = RIGHT_PAD

    @property
    def font_size(self) -> int:
        return 14 * self.pixel_scale


@dataclass(frozen=True)
class CollageOptions:
    cols: int = 2
    chunk_size: int = 10
    tile_width: int = 315
    tile_height: int = 630
    cell_pad: int = 9
    label_height: int = 16
    label_font_size: int = 9
    bg: tuple[int, int, int, int] = BG
    label_template: str = "{name}  cw={cw}  lh={lh}"


def load_size_pairs(path: Path) -> list[tuple[int, int]]:
    """Load ``(cw, lh)`` pairs from a legacy Python ledger or a JSON file."""

    if path.suffix == ".json":
        raw = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            raw = raw.get("sizes", raw.get("list_of_cw_lh"))
        return normalize_size_pairs(raw, source=path)

    if path.suffix == ".py":
        module = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in module.body:
            if not isinstance(node, ast.Assign):
                continue
            if any(
                isinstance(target, ast.Name) and target.id == "list_of_cw_lh"
                for target in node.targets
            ):
                raw = ast.literal_eval(node.value)
                return normalize_size_pairs(raw, source=path)
        raise ValueError(f"{path} must define list_of_cw_lh = [(cw, lh), ...]")

    raise ValueError(f"unsupported size file format for {path}; use .py or .json")


def normalize_size_pairs(raw: object, *, source: Path) -> list[tuple[int, int]]:
    if not isinstance(raw, list):
        raise ValueError(f"{source} must contain a list of size pairs")

    pairs: list[tuple[int, int]] = []
    for item in raw:
        if isinstance(item, dict):
            cw = item.get("cw", item.get("width"))
            lh = item.get("lh", item.get("height"))
        elif isinstance(item, (list, tuple)) and len(item) == SIZE_PAIR_LEN:
            cw, lh = item
        else:
            raise ValueError(f"bad size entry in {source}: {item!r}")

        try:
            parsed = (int(cw), int(lh))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"bad size entry in {source}: {item!r}") from exc
        if parsed[0] <= 0 or parsed[1] <= 0:
            raise ValueError(f"size entries must be positive in {source}: {item!r}")
        pairs.append(parsed)

    if not pairs:
        raise ValueError(f"{source} contains no size pairs")
    return pairs


def jetbrains_mono_font(
    size: int,
    *,
    bold: bool = False,
) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    name = "JetBrainsMono-Bold.ttf" if bold else "JetBrainsMono-Regular.ttf"
    for base in (
        Path("/usr/share/fonts/truetype/jetbrains-mono"),
        Path.home() / ".local/share/fonts",
    ):
        path = base / name
        if path.is_file():
            return ImageFont.truetype(str(path), size)
    return ImageFont.load_default()


def grid_font(size: int, *, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """DejaVu draws terminal box characters more consistently in Pillow."""

    name = "DejaVuSansMono-Bold.ttf" if bold else "DejaVuSansMono.ttf"
    for base in (
        Path("/usr/share/fonts/truetype/dejavu"),
        Path.home() / ".local/share/fonts",
    ):
        path = base / name
        if path.is_file():
            return ImageFont.truetype(str(path), size)
    return jetbrains_mono_font(size, bold=bold)


def cell_size(font: ImageFont.ImageFont) -> tuple[int, int]:
    cell_w = max(1, round(font.getlength("M")))
    heights = [font.getbbox(ch)[3] - font.getbbox(ch)[1] for ch in BOX_SAMPLE_CHARS]
    return cell_w, max(heights) + 2


def draw_grid_char(
    draw: ImageDraw.ImageDraw,
    char: str,
    col: int,
    row: int,
    cell_w: int,
    cell_h: int,
    font: ImageFont.ImageFont,
    fill: tuple[int, int, int, int],
    *,
    ascent: int,
) -> None:
    x = col * cell_w
    y = row * cell_h + ascent
    draw.text((x, y), char, fill=fill, font=font, anchor="ls")


def draw_terminal_primitive(
    draw: ImageDraw.ImageDraw,
    char: str,
    col: int,
    row: int,
    cell_w: int,
    cell_h: int,
    fill: tuple[int, int, int, int],
) -> bool:
    x0 = col * cell_w
    y0 = row * cell_h
    x1 = x0 + cell_w - 1
    y1 = y0 + cell_h - 1

    if char in BOX_SEGMENTS:
        stroke = max(2, round(min(cell_w, cell_h) * 0.16))
        half = stroke // 2
        cx = x0 + cell_w // 2
        cy = y0 + cell_h // 2
        for segment in BOX_SEGMENTS[char]:
            if segment == "left":
                draw.rectangle([x0, cy - half, cx + half, cy - half + stroke - 1], fill=fill)
            elif segment == "right":
                draw.rectangle([cx - half, cy - half, x1, cy - half + stroke - 1], fill=fill)
            elif segment == "up":
                draw.rectangle([cx - half, y0, cx - half + stroke - 1, cy + half], fill=fill)
            elif segment == "down":
                draw.rectangle([cx - half, cy - half, cx - half + stroke - 1, y1], fill=fill)
        return True

    if char == "█":
        draw.rectangle([x0, y0, x1, y1], fill=fill)
        return True
    if char == "▌":
        draw.rectangle([x0, y0, x0 + max(1, cell_w // 2) - 1, y1], fill=fill)
        return True
    if char == "▐":
        draw.rectangle([x1 - max(1, cell_w // 2) + 1, y0, x1, y1], fill=fill)
        return True
    if char == "▏":
        draw.rectangle([x0, y0, x0 + max(1, round(cell_w / 8)) - 1, y1], fill=fill)
        return True

    return False


def resolve_colors(style: Style | None) -> tuple[tuple[int, int, int], tuple[int, int, int]]:
    theme = SVG_EXPORT_THEME
    style = style or Style()
    fg = (
        theme.foreground_color
        if style.color is None or style.color.is_default
        else style.color.get_truecolor(theme)
    )
    bg = (
        theme.background_color
        if style.bgcolor is None or style.bgcolor.is_default
        else style.bgcolor.get_truecolor(theme)
    )
    if style.reverse:
        fg, bg = bg, fg
    if style.dim:
        fg = blend_rgb(fg, bg, 0.4)
    return (fg.red, fg.green, fg.blue), (bg.red, bg.green, bg.blue)


def ansi_to_png(ansi: str, width_columns: int, options: RenderOptions) -> Image.Image:
    sink = io.StringIO()
    console = Console(
        record=True,
        width=width_columns,
        force_terminal=True,
        color_system="truecolor",
        file=sink,
    )
    console.print(Text.from_ansi(ansi.rstrip("\n")))
    with console._record_buffer_lock:
        segments = list(Segment.filter_control(console._record_buffer))

    lines = list(Segment.split_and_crop_lines(segments, length=width_columns))
    font = grid_font(options.font_size)
    bold_font = grid_font(options.font_size, bold=True)
    cell_w, cell_h = cell_size(font)
    ascent, descent = font.getmetrics()
    cell_h = max(cell_h, ascent + descent + 2)
    last_col_overflow = max(font.getbbox(ch)[2] for ch in BOX_SAMPLE_CHARS) - cell_w
    right_pad = max(options.right_pad, int(last_col_overflow) + 1)

    img = Image.new(
        "RGBA",
        (width_columns * cell_w + right_pad, len(lines) * cell_h),
        (*options.terminal_bg, 255),
    )
    draw = ImageDraw.Draw(img)

    for row, line in enumerate(lines):
        col = 0
        for text, segment_style, _control in line:
            if not text:
                continue
            style = segment_style or Style()
            fg, bg = resolve_colors(style)
            face = bold_font if style.bold else font
            for char in text:
                if char == "\n" or col >= width_columns:
                    continue
                x0 = col * cell_w
                y0 = row * cell_h
                draw.rectangle(
                    [x0, y0, x0 + cell_w - 1, y0 + cell_h - 1],
                    fill=(*bg, 255),
                )
                if not draw_terminal_primitive(
                    draw, char, col, row, cell_w, cell_h, (*fg, 255)
                ):
                    draw_grid_char(
                        draw,
                        char,
                        col,
                        row,
                        cell_w,
                        cell_h,
                        face,
                        (*fg, 255),
                        ascent=ascent,
                    )
                col += 1

    return img


def render_env() -> dict[str, str]:
    env = os.environ.copy()
    env["FORCE_COLOR"] = "3"
    env.pop("NO_COLOR", None)
    return env


def run_capture(command: Sequence[str], *, cwd: Path | None = None) -> str:
    result = subprocess.run(
        list(command),
        cwd=cwd,
        env=render_env(),
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        if result.stderr:
            sys.stderr.write(result.stderr)
        joined = " ".join(command)
        raise RuntimeError(f"renderer exited with status {result.returncode}: {joined}")
    return result.stdout


def pane_fixture_command(pane_type: str, fixture_data: str, lh: int, cw: int) -> list[str]:
    return [
        "node",
        "--import",
        "tsx",
        PANE_FIXTURE_RENDERER,
        pane_type,
        fixture_data,
        str(lh),
        str(cw),
    ]


def command_from_template(template: Sequence[str], *, cw: int, lh: int) -> list[str]:
    if not any("{cw}" in part for part in template) or not any("{lh}" in part for part in template):
        raise ValueError("command template must include both {cw} and {lh} placeholders")
    return [part.format(cw=cw, lh=lh) for part in template]


def scale_to_fit(img: Image.Image, max_w: int, max_h: int) -> Image.Image:
    width, height = img.size
    if width <= max_w and height <= max_h:
        return img
    scale = min(max_w / width, max_h / height)
    new_size = (max(1, int(width * scale)), max(1, int(height * scale)))
    return img.resize(new_size, Image.Resampling.LANCZOS)


def make_collage(
    tiles: list[tuple[tuple[int, int], Image.Image]],
    name: str,
    options: CollageOptions,
) -> Image.Image:
    cols = min(options.cols, len(tiles))
    rows = (len(tiles) + cols - 1) // cols
    cell_w = options.tile_width + 2 * options.cell_pad
    cell_h = options.tile_height + options.label_height + 2 * options.cell_pad
    collage = Image.new("RGBA", (cols * cell_w, rows * cell_h), options.bg)
    draw = ImageDraw.Draw(collage)
    font = jetbrains_mono_font(options.label_font_size)

    for index, ((cw, lh), image) in enumerate(tiles):
        row, col = divmod(index, cols)
        ox = col * cell_w + options.cell_pad
        oy = row * cell_h + options.cell_pad
        scaled = scale_to_fit(image, options.tile_width, options.tile_height)
        px = ox + (options.tile_width - scaled.width) // 2
        py = oy + (options.tile_height - scaled.height) // 2
        collage.paste(scaled, (px, py), scaled)
        label = options.label_template.format(name=name, cw=cw, lh=lh)
        draw.text(
            (ox, oy + options.tile_height + 4),
            label,
            fill=(200, 200, 200, 255),
            font=font,
        )

    return collage


def collage_output_paths(
    out_dir: Path,
    name: str,
    tile_count: int,
    chunk_size: int,
) -> list[tuple[int, int, Path]]:
    """Return 1-based inclusive (start, end, path) for each collage chunk."""

    if tile_count <= chunk_size:
        return [(1, tile_count, out_dir / f"{name}.png")]

    chunks: list[tuple[int, int, Path]] = []
    start = 1
    while start <= tile_count:
        end = min(start + chunk_size - 1, tile_count)
        chunks.append((start, end, out_dir / f"{name}{start}-{end}.png"))
        start = end + 1
    return chunks


def remove_stale_collage_outputs(out_dir: Path, name: str, keep: set[Path]) -> None:
    for path in out_dir.glob(f"{name}*.png"):
        if path not in keep:
            path.unlink()


def write_collage_chunks(
    tiles: list[tuple[tuple[int, int], Image.Image]],
    *,
    name: str,
    out_dir: Path,
    options: CollageOptions,
    clean_stale: bool,
) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    chunk_specs = collage_output_paths(out_dir, name, len(tiles), options.chunk_size)
    if clean_stale:
        remove_stale_collage_outputs(out_dir, name, {path for _, _, path in chunk_specs})

    written: list[Path] = []
    for start, end, out_path in chunk_specs:
        collage = make_collage(tiles[start - 1 : end], name, options)
        collage.save(out_path)
        written.append(out_path)
    return written


def build_collages(
    *,
    sizes: list[tuple[int, int]],
    capture: Any,
    name: str,
    out_dir: Path,
    render_options: RenderOptions,
    collage_options: CollageOptions,
    clean_stale: bool,
    echo_ansi: bool,
) -> list[Path]:
    tiles: list[tuple[tuple[int, int], Image.Image]] = []
    total = len(sizes)
    for index, (cw, lh) in enumerate(sizes, start=1):
        ansi = capture(cw, lh)
        if echo_ansi:
            print(f"{'=' * 72}")
            print(f"{index}/{total} {name} cw={cw} lh={lh}")
            print(f"{'=' * 72}")
            sys.stdout.write(ansi)
            if not ansi.endswith("\n"):
                sys.stdout.write("\n")
        tiles.append(((cw, lh), ansi_to_png(ansi, cw, render_options)))

    return write_collage_chunks(
        tiles,
        name=name,
        out_dir=out_dir,
        options=collage_options,
        clean_stale=clean_stale,
    )


def positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return parsed


def common_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--sizes", type=Path, required=True, help="Path to .py or .json size list")
    parser.add_argument("--out-dir", type=Path, required=True, help="Directory for PNG collages")
    parser.add_argument("--name", required=True, help="Output basename and default label name")
    parser.add_argument("--cols", type=positive_int, default=2)
    parser.add_argument("--chunk-size", type=positive_int, default=10)
    parser.add_argument("--tile-width", type=positive_int, default=315)
    parser.add_argument("--tile-height", type=positive_int, default=630)
    parser.add_argument("--pixel-scale", type=positive_int, default=3)
    parser.add_argument("--label-template", default="{name}  cw={cw}  lh={lh}")
    parser.add_argument(
        "--echo-ansi",
        action="store_true",
        help="Print captured ANSI while rendering",
    )
    stale = parser.add_mutually_exclusive_group()
    stale.add_argument("--clean-stale", dest="clean_stale", action="store_true", default=True)
    stale.add_argument("--keep-stale", dest="clean_stale", action="store_false")


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="mode", required=True)

    pane = subparsers.add_parser("pane-fixture", help="Render an Ink pane fixture")
    pane.add_argument("pane_type")
    pane.add_argument("fixture_data")
    common_options(pane)

    command = subparsers.add_parser("command", help="Render with an arbitrary command template")
    common_options(command)
    command.add_argument(
        "command",
        nargs=argparse.REMAINDER,
        help="Command to run; include {cw} and {lh}. Use -- before the command if needed.",
    )

    return parser.parse_args(argv)


def make_render_options(args: argparse.Namespace) -> RenderOptions:
    return RenderOptions(pixel_scale=args.pixel_scale)


def make_collage_options(args: argparse.Namespace) -> CollageOptions:
    # These defaults match the old .agents collage layout at pixel_scale=3.
    scale = args.pixel_scale / 3
    return CollageOptions(
        cols=args.cols,
        chunk_size=args.chunk_size,
        tile_width=args.tile_width,
        tile_height=args.tile_height,
        cell_pad=max(1, round(9 * scale)),
        label_height=max(1, round(16 * scale)),
        label_font_size=max(1, round(9 * scale)),
        label_template=args.label_template,
    )


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    sizes = load_size_pairs(args.sizes)
    render_options = make_render_options(args)
    collage_options = make_collage_options(args)

    if args.mode == "pane-fixture":

        def capture(cw: int, lh: int) -> str:
            return run_capture(
                pane_fixture_command(args.pane_type, args.fixture_data, lh, cw),
                cwd=INKTUI_DIR,
            )

    elif args.mode == "command":
        template = list(args.command)
        if template and template[0] == "--":
            template = template[1:]
        if not template:
            raise SystemExit("command mode requires a command template")
        command_from_template(template, cw=sizes[0][0], lh=sizes[0][1])

        def capture(cw: int, lh: int) -> str:
            return run_capture(command_from_template(template, cw=cw, lh=lh))

    else:  # pragma: no cover - argparse enforces this
        raise SystemExit(f"unknown mode: {args.mode}")

    out_paths = build_collages(
        sizes=sizes,
        capture=capture,
        name=args.name,
        out_dir=args.out_dir,
        render_options=render_options,
        collage_options=collage_options,
        clean_stale=args.clean_stale,
        echo_ansi=args.echo_ansi,
    )
    for out_path in out_paths:
        with Image.open(out_path) as collage:
            print(f"Wrote {out_path} ({collage.width}x{collage.height})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
