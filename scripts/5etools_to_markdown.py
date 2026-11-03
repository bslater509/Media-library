#!/usr/bin/env python3
"""Convert a 5e.tools book or adventure into Markdown.

Examples:
    python3 scripts/5etools_to_markdown.py 'https://5e.tools/book.html#PHB'
    python3 scripts/5etools_to_markdown.py 'https://5e.tools/adventure.html#CoS' -o cos.md
    python3 scripts/5etools_to_markdown.py PHB --split ./phb-parts --images

The tool reads the same JSON 5e.tools itself renders (data/book/book-<id>.json,
data/adventure/adventure-<id>.json), so it needs no browser. Creature
references are inlined from the bestiary file of the source they point at; pass
--no-statblocks to keep them as a one-line reference instead.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

SITE = "https://5e.tools"
DATA_URL = SITE + "/data"
IMG_URL = SITE + "/img"
# Fallback when the site sits behind a challenge; same data, plain git hosting.
MIRROR_URL = "https://raw.githubusercontent.com/5etools-mirror-3/5etools-src/main/data"

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36"
)

SIZES = {
    "T": "Tiny",
    "S": "Small",
    "M": "Medium",
    "L": "Large",
    "H": "Huge",
    "G": "Gargantuan",
}

XP_BY_CR = {
    "0": "10",
    "1/8": "25",
    "1/4": "50",
    "1/2": "100",
    "1": "200",
    "2": "450",
    "3": "700",
    "4": "1,100",
    "5": "1,800",
    "6": "2,300",
    "7": "2,900",
    "8": "3,900",
    "9": "5,000",
    "10": "5,900",
    "11": "7,200",
    "12": "8,400",
    "13": "10,000",
    "14": "11,500",
    "15": "13,000",
    "16": "15,000",
    "17": "18,000",
    "18": "20,000",
    "19": "22,000",
    "20": "25,000",
    "21": "33,000",
    "22": "41,000",
    "23": "50,000",
    "24": "62,000",
    "25": "75,000",
    "26": "90,000",
    "27": "105,000",
    "28": "120,000",
    "29": "135,000",
    "30": "155,000",
}


DISPLAY_NAME_TAGS = {
    "spell",
    "item",
    "creature",
    "race",
    "condition",
    "action",
    "status",
    "language",
    "object",
    "vehicle",
    "feat",
    "background",
    "boon",
    "reward",
    "optfeature",
    "hazard",
    "disease",
    "madness",
    "variantrule",
    "classFeature",
    "subclassFeature",
    "sense",
    "recipe",
    "cult",
    "psionic",
    "book",
    "table",
}

ABILITY_NAMES = {
    "str": "Strength",
    "dex": "Dexterity",
    "con": "Constitution",
    "int": "Intelligence",
    "wis": "Wisdom",
    "cha": "Charisma",
}

SCHOOLS = {
    "A": "abjuration",
    "C": "conjuration",
    "D": "divination",
    "E": "enchantment",
    "V": "evocation",
    "I": "illusion",
    "N": "necromancy",
    "T": "transmutation",
    "P": "psionic",
}

ITEM_TYPES = {
    "A": "ammunition",
    "AF": "ammunition",
    "G": "adventuring gear",
    "HA": "heavy armor",
    "MA": "medium armor",
    "LA": "light armor",
    "S": "shield",
    "M": "melee weapon",
    "R": "ranged weapon",
    "P": "potion",
    "SC": "scroll",
    "SCF": "spellcasting focus",
    "WD": "wand",
    "RD": "rod",
    "ST": "staff",
    "RG": "ring",
    "T": "tool",
    "MNT": "mount",
    "VEH": "vehicle",
    "EXP": "explosive",
    "TG": "trade good",
    "OTH": "other",
    "$": "treasure",
    "$A": "art object",
    "$G": "gemstone",
    "GV": "generic variant",
    "AIR": "air vehicle",
    "SHP": "ship",
}

FEAT_CATEGORIES = {
    "G": "General feat",
    "O": "Origin feat",
    "FS": "Fighting Style feat",
    "EB": "Epic Boon feat",
    "DG": "Dragonmark feat",
}

OPTIONAL_FEATURE_TYPES = {
    "EI": "Eldritch Invocation",
    "FS": "Fighting Style",
    "AS": "Artificer Infusion",
    "MV": "Maneuver",
    "MV:B": "Maneuver",
    "AI": "Artificer Infusion",
    "DD": "Divine Domain",
    "ED": "Elemental Discipline",
}

TRAP_TYPES = {
    "MECH": "Mechanical trap",
    "MAG": "Magical trap",
    "SMPL": "Simple hazard",
    "CMPLX": "Complex trap",
    "ENV": "Environmental hazard",
    "WTHR": "Weather hazard",
}


def spec_level(parts: list[str]) -> int | None:
    if len(parts) > 3 and parts[3].isdigit():
        return int(parts[3])
    return None


def ordinal(number: int) -> str:
    if 10 <= number % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(number % 10, "th")
    return f"{number}{suffix}"


ATTACK_KINDS = {
    "mw": "*Melee Weapon Attack:*",
    "rw": "*Ranged Weapon Attack:*",
    "mw,rw": "*Melee or Ranged Weapon Attack:*",
    "rw,mw": "*Melee or Ranged Weapon Attack:*",
    "ms": "*Melee Spell Attack:*",
    "rs": "*Ranged Spell Attack:*",
}


class FetchError(RuntimeError):
    pass


def fetch_bytes(url: str, timeout: int = 60) -> bytes:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/json,image/*,*/*",
        },
    )
    last = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except urllib.error.HTTPError as exc:
            last = exc
            if exc.code in (403, 429) and attempt < 2:
                time.sleep(2 + attempt * 3)
                continue
            raise FetchError(f"{exc.code} for {url}") from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            last = exc
            if attempt < 2:
                time.sleep(2 + attempt * 3)
                continue
            raise FetchError(str(exc)) from exc
    raise FetchError(str(last))


class Fetcher:
    """Downloads 5e.tools JSON with an optional on-disk cache and a mirror fallback."""

    def __init__(self, cache_dir: Path | None) -> None:
        self.cache_dir = cache_dir
        if cache_dir:
            cache_dir.mkdir(parents=True, exist_ok=True)

    def _cache_path(self, rel: str) -> Path | None:
        if not self.cache_dir:
            return None
        safe = rel.replace("/", "_")
        return self.cache_dir / safe

    def json(self, rel: str) -> dict:
        cached = self._cache_path(rel)
        if cached and cached.exists():
            try:
                return json.loads(cached.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                cached.unlink(missing_ok=True)

        errors = []
        for base in (DATA_URL, MIRROR_URL):
            url = f"{base}/{rel}"
            try:
                raw = fetch_bytes(url)
                data = json.loads(raw)
            except (FetchError, ValueError) as exc:
                errors.append(f"{url}: {exc}")
                continue
            if cached:
                cached.write_bytes(raw)
            return data
        raise SystemExit("Could not download " + rel + "\n  " + "\n  ".join(errors))

    def image(self, url: str, dest: Path) -> None:
        if dest.exists() and dest.stat().st_size:
            return
        raw = fetch_bytes(url)
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(dest.name + ".part")
        tmp.write_bytes(raw)
        tmp.replace(dest)


def squash(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


class Converter:
    def __init__(
        self,
        fetcher: Fetcher,
        *,
        statblocks: bool = True,
        images_dir: Path | None = None,
        drop_images: bool = False,
        debug: bool = False,
    ) -> None:
        self.fetcher = fetcher
        self.statblocks = statblocks
        self.images_dir = images_dir
        self.drop_images = drop_images
        self.debug = debug
        self._book_index: dict[str, dict] | None = None
        self._adventure_index: dict[str, dict] | None = None
        self._bestiary_index: dict[str, str] | None = None
        self._bestiary_files: dict[str, list] = {}
        self._monsters: dict[tuple[str, str], dict | None] = {}
        self._pools: dict[tuple[str, str], list[dict]] = {}
        self._indexes: dict[str, dict] = {}
        self.book_source = ""
        self._class_rel = ""
        self._class_source = ""
        self.rendered_names: set[str] = set()
        self._last_heading = ""
        self._last_caption = ""
        self._group_caption = ""

    # ------------------------------------------------------------------ lookup

    def resolve(self, target: str) -> tuple[str, str, str]:
        """Return (kind, id, title) for a URL or bare 5etools id."""
        kind = None
        ident = target.strip()

        if ident.startswith(("http://", "https://")):
            parsed = urlparse(ident)
            path = parsed.path.lower()
            if "adventure" in path:
                kind = "adventure"
            elif "book" in path:
                kind = "book"
            fragment = parsed.fragment or ""
            ident = fragment.split(",")[0].split("=")[-1] if fragment else ""
            if not ident:
                raise SystemExit(f"No book id in the fragment of {target!r}")
        elif ident.lower().startswith(("book-", "adventure-")):
            prefix = ident.lower().split("-", 1)[0]
            kind = prefix
            ident = ident[len(prefix) + 1 :]

        if kind == "book":
            entry = self.book_index().get(ident.upper()) or self.book_index().get(ident)
            if entry:
                return "book", entry["id"], entry.get("name") or entry["id"]
            raise SystemExit(f"{ident!r} is not a book id known to 5e.tools")
        if kind == "adventure":
            entry = self.adventure_index().get(
                ident.upper()
            ) or self.adventure_index().get(ident)
            if entry:
                return "adventure", entry["id"], entry.get("name") or entry["id"]
            raise SystemExit(f"{ident!r} is not an adventure id known to 5e.tools")

        entry = self.book_index().get(ident.upper()) or self.book_index().get(ident)
        if entry:
            return "book", entry["id"], entry.get("name") or entry["id"]
        entry = self.adventure_index().get(ident.upper()) or self.adventure_index().get(
            ident
        )
        if entry:
            return "adventure", entry["id"], entry.get("name") or entry["id"]
        raise SystemExit(f"{ident!r} is neither a 5e.tools book nor adventure id")

    def book_index(self) -> dict[str, dict]:
        if self._book_index is None:
            data = self.fetcher.json("books.json")
            books = data.get("book", data) if isinstance(data, dict) else data
            self._book_index = {
                str(b.get("id")): b
                for b in books
                if isinstance(b, dict) and b.get("id")
            }
        return self._book_index

    def adventure_index(self) -> dict[str, dict]:
        if self._adventure_index is None:
            data = self.fetcher.json("adventures.json")
            advs = data.get("adventure", data) if isinstance(data, dict) else data
            self._adventure_index = {
                str(a.get("id")): a for a in advs if isinstance(a, dict) and a.get("id")
            }
        return self._adventure_index

    def bestiary_index(self) -> dict[str, str]:
        if self._bestiary_index is None:
            try:
                self._bestiary_index = self.fetcher.json("bestiary/index.json")
            except SystemExit:
                self._bestiary_index = {}
        return self._bestiary_index

    # ---------------------------------------------------------------- document

    def document(self, title: str, entries: list, source_url: str) -> str:
        self._last_heading = ""
        self._last_caption = ""
        parts = [f"# {self.inline(title)}", f"*Source: <{source_url}>*"]
        parts.extend(self.blocks(entries, 2))
        text = "\n\n".join(p for p in parts if p and p.strip())
        return re.sub(r"\n{3,}", "\n\n", text).strip() + "\n"

    def chapter_documents(
        self, title: str, entries: list, source_url: str
    ) -> list[tuple[str, str]]:
        docs = []
        for index, entry in enumerate(entries):
            name = entry.get("name") if isinstance(entry, dict) else None
            label = squash(self.inline(name)) if name else f"Part {index + 1}"
            body = self.document(label, [entry], source_url)
            docs.append((label, body))
        return docs

    # ------------------------------------------------------------ block level

    def blocks(self, node, level: int = 2) -> list[str]:
        if node is None:
            return []
        if isinstance(node, str):
            text = self.inline(node).strip()
            return [text] if text else []
        if isinstance(node, (list, tuple)):
            out: list[str] = []
            for item in node:
                out.extend(self.blocks(item, level))
            return out
        if not isinstance(node, dict):
            return []

        handler = getattr(self, "b_" + str(node.get("type")), None)
        if handler is not None:
            return [b for b in handler(node, level) if b and b.strip()]

        if self.debug:
            print(f"[?] unhandled entry type: {node.get('type')!r}", file=sys.stderr)

        name = node.get("name")
        out = [self.heading(name, level)] if name else []
        for key in ("entries", "items", "wrappeds", "entry"):
            if key in node:
                return out + self.blocks(node[key], level + 1 if name else level)
        return out

    def heading(self, name, level: int) -> str:
        text = squash(self.inline(str(name))) if name else ""
        if not text:
            return ""
        self._last_heading = text
        if level <= 6:
            return "#" * level + " " + text
        return f"**{text}**"

    def quote(self, chunks: list[str]) -> str:
        lines: list[str] = []
        for chunk in chunks:
            chunk_lines = [line for line in chunk.split("\n") if line.strip()]
            nested = chunk_lines and all(line.startswith(">") for line in chunk_lines)
            for line in chunk.split("\n"):
                if nested:
                    lines.append(line)
                else:
                    lines.append("> " + line if line else ">")
            lines.append(">")
        while lines and lines[-1] == ">":
            lines.pop()
        return "\n".join(lines)

    # -- specific block types

    def b_entries(self, node, level: int) -> list[str]:
        name = node.get("name")
        out: list[str] = []
        if name:
            out.append(self.heading(name, level))
            level += 1
        out.extend(self.blocks(node.get("entries") or [], level))
        return out

    def b_section(self, node, level: int) -> list[str]:
        return self.b_entries(node, level)

    def b_variant(self, node, level: int) -> list[str]:
        return self.b_entries(node, level)

    def b_variantSub(self, node, level: int) -> list[str]:
        return self.b_entries(node, level)

    def b_options(self, node, level: int) -> list[str]:
        return self.b_entries(node, level)

    def b_inlineBlock(self, node, level: int) -> list[str]:
        return self.blocks(node.get("entries") or node.get("entry") or [], level)

    def b_inline(self, node, level: int) -> list[str]:
        return self.blocks(node.get("entries") or node.get("entry") or [], level)

    def b_abilityDc(self, node, level: int) -> list[str]:
        name = str(node.get("name") or "Spell")
        mods = self.ability_modifiers(node.get("attributes"))
        return [f"{name} save DC = 8 + your proficiency bonus + {mods}"]

    def b_abilityAttackMod(self, node, level: int) -> list[str]:
        name = str(node.get("name") or "Spell")
        mods = self.ability_modifiers(node.get("attributes"))
        return [f"{name} attack modifier = your proficiency bonus + {mods}"]

    def ability_modifiers(self, attributes) -> str:
        parts = []
        for attribute in attributes or []:
            attribute = str(attribute)
            if attribute == "spellcasting":
                parts.append("your spellcasting ability modifier")
            else:
                parts.append(f"your {ABILITY_NAMES.get(attribute, attribute)} modifier")
        return " or ".join(parts) or "your spellcasting ability modifier"

    def b_refClassFeature(self, node, level: int) -> list[str]:
        if not self._class_rel:
            return []
        spec = str(node.get("classFeature") or "").split("|")
        feature = self.find_class_feature(
            self.pool(self._class_rel, "classFeature"),
            spec[0],
            {self._class_source} if self._class_source else set(),
            spec_level(spec),
        )
        if feature is None:
            return []
        label = f"**{squash(self.inline(str(feature.get('name', spec[0]))))}.**"
        return [self.labeled(label, feature.get("entries"), level)]

    def b_refSubclassFeature(self, node, level: int) -> list[str]:
        if not self._class_rel:
            return []
        spec = str(node.get("subclassFeature") or "").split("|")
        feature = self.find_class_feature(
            self.pool(self._class_rel, "subclassFeature"),
            spec[0],
            set(),
            spec_level(spec),
        )
        if feature is None:
            return []
        label = f"**{squash(self.inline(str(feature.get('name', spec[0]))))}.**"
        return [self.labeled(label, feature.get("entries"), level)]

    def b_refOptionalfeature(self, node, level: int) -> list[str]:
        spec = str(node.get("optionalfeature") or "").split("|")
        record = self.named_pool(
            "optionalfeatures.json",
            "optionalfeature",
            spec[0],
            spec[1] if len(spec) > 1 else "",
        )
        if record is None:
            return [f"**{squash(self.inline(spec[0]))}**"]
        label = f"**{squash(self.inline(str(record.get('name', spec[0]))))}.**"
        return [self.labeled(label, record.get("entries"), level)]

    def b_refFeat(self, node, level: int) -> list[str]:
        spec = str(node.get("feat") or "").split("|")
        record = self.named_pool(
            "feats.json", "feat", spec[0], spec[1] if len(spec) > 1 else ""
        )
        if record is None:
            return [f"**{squash(self.inline(spec[0]))}**"]
        label = f"**{squash(self.inline(str(record.get('name', spec[0]))))}.**"
        return [self.labeled(label, record.get("entries"), level)]

    def b_attack(self, node, level: int) -> list[str]:
        name = node.get("name")
        body = node.get("entries") or ([node["entry"]] if "entry" in node else [])
        if body:
            label = f"**{squash(self.inline(str(name)))}.**" if name else ""
            return [self.labeled(label, body, level)]
        return [f"**{squash(self.inline(str(name)))}**"] if name else []

    def b_abilityGeneric(self, node, level: int) -> list[str]:
        name = node.get("name")
        text = node.get("text") or node.get("entry") or ""
        body = squash(self.inline(str(text)))
        if name and body:
            return [f"**{squash(self.inline(str(name)))}** {body}"]
        return [body] if body else []

    def b_wrapper(self, node, level: int) -> list[str]:
        return self.blocks(node.get("wrappeds") or node.get("entries") or [], level)

    def b_inset(self, node, level: int) -> list[str]:
        name = node.get("name")
        body = self.blocks(node.get("entries") or [], level + 1)
        chunks = ([f"**{squash(self.inline(name))}**"] if name else []) + body
        return [self.quote(chunks)] if chunks else []

    def b_insetReadaloud(self, node, level: int) -> list[str]:
        name = node.get("name")
        body = self.blocks(node.get("entries") or [], level + 1)
        readaloud = []
        for chunk in body:
            if "\n" not in chunk and not chunk.startswith(
                ("#", "|", ">", "-", "*", "!")
            ):
                readaloud.append(f"*{chunk}*")
            else:
                readaloud.append(chunk)
        chunks = ([f"**{squash(self.inline(name))}**"] if name else []) + readaloud
        return [self.quote(chunks)] if chunks else []

    def b_quote(self, node, level: int) -> list[str]:
        body = self.blocks(node.get("entries") or [], level + 1)
        by = node.get("by")
        where = node.get("from")
        credit = ", ".join(str(x) for x in (by, where) if x)
        if credit:
            body.append(f"— *{squash(self.inline(credit))}*")
        return [self.quote(body)] if body else []

    def b_list(self, node, level: int) -> list[str]:
        style = str(node.get("style") or "").lower()
        ordered = (
            any(k in style for k in ("decimal", "alpha", "roman"))
            and "no-bullet" not in style
        )
        lines: list[str] = []
        for index, item in enumerate(node.get("items") or []):
            marker = f"{index + 1}." if ordered else "-"
            sub = self.item_lines(item, level)
            if not sub:
                continue
            lines.append(f"{marker} {sub[0]}")
            pad = " " * (len(marker) + 1)
            for extra in sub[1:]:
                lines.append(pad + extra if extra else "")
        return ["\n".join(lines)] if lines else []

    def item_lines(self, item, level: int) -> list[str]:
        if isinstance(item, str):
            return [squash(self.inline(item))]
        if not isinstance(item, dict):
            return [b for b in self.blocks(item, level)]
        name = item.get("name")
        body = item["entry"] if "entry" in item else item.get("entries")
        if name:
            label = f"**{squash(self.inline(str(name)))}**"
            if body in (None, "", []):
                return [label]
            rest = self.blocks(body, level + 1)
            if not rest:
                return [label]
            if len(rest) == 1 and "\n" not in rest[0]:
                return [f"{label} {rest[0]}"]
            return [label] + rest
        if body is not None:
            return self.blocks(body, level)
        return []

    def b_item(self, node, level: int) -> list[str]:
        return self.item_lines(node, level)

    def b_itemSub(self, node, level: int) -> list[str]:
        return self.item_lines(node, level)

    def b_tableGroup(self, node, level: int) -> list[str]:
        out: list[str] = []
        name = node.get("name")
        if name:
            out.append(self.heading(name, level))
            level += 1
        previous = self._group_caption
        self._group_caption = squash(self.inline(str(name))) if name else ""
        try:
            for table in node.get("tables") or []:
                out.extend(self.blocks(table, level))
        finally:
            self._group_caption = previous
        return out

    def b_table(self, node, level: int) -> list[str]:
        rows = node.get("rows") or []
        if not rows:
            return []

        normalized: list[list[str]] = []
        for row in rows:
            cells = row.get("row") if isinstance(row, dict) else row
            if cells is None:
                cells = []
            out: list[str] = []
            for cell in cells:
                span = 1
                content = cell
                if isinstance(cell, dict) and cell.get("type") == "cell":
                    span = int(cell.get("colSpan") or 1)
                    content = cell.get("entry", cell.get("entries"))
                out.append(self.cell_text(content))
                out.extend([""] * max(0, span - 1))
            normalized.append(out)

        labels = [self.cell_text(label) for label in (node.get("colLabels") or [])]
        width = max([len(labels)] + [len(row) for row in normalized])
        labels += [""] * (width - len(labels))
        normalized = [row + [""] * (width - len(row)) for row in normalized]

        caption = squash(
            self.inline(str(node.get("caption") or node.get("name") or ""))
        )
        if width == 1 and not any(label.strip() for label in labels):
            out: list[str] = []
            if (
                caption
                and caption != self._group_caption
                and caption != self._last_caption
            ):
                self._last_caption = caption
                out.append(f"**{caption}**")
            for row in normalized:
                parts = [part for part in row[0].split("<br>") if part.strip()]
                if parts:
                    out.append("  \n".join(parts))
            return out

        lines = [
            "| " + " | ".join(labels) + " |",
            "|" + "|".join(["---"] * width) + "|",
        ]
        for row in normalized:
            lines.append("| " + " | ".join(row) + " |")

        block = "\n".join(lines)
        if caption and caption != self._group_caption and caption != self._last_caption:
            self._last_caption = caption
            return [f"**{caption}**", block]
        return [block]

    def cell_text(self, cell) -> str:
        if cell is None:
            return ""
        if isinstance(cell, (list, tuple)):
            parts = [self.cell_text(part) for part in cell]
            return "<br>".join(p for p in parts if p)
        if isinstance(cell, dict):
            if "entry" in cell:
                return self.cell_text(cell["entry"])
            if "entries" in cell:
                return self.cell_text(cell["entries"])
            if "items" in cell:
                return self.cell_text(cell["items"])
            if cell.get("type") == "cell":
                return self.cell_text(cell.get("entry"))
            name = cell.get("name")
            return self.cell_text(name) if name else ""
        text = self.inline(str(cell))
        text = squash(text)
        return text.replace("|", "\\|")

    # -- images

    def image_url(self, href) -> str | None:
        if isinstance(href, str):
            return href
        if not isinstance(href, dict):
            return None
        if href.get("type") == "internal" and href.get("path"):
            return f"{IMG_URL}/{href['path']}"
        if href.get("type") == "external":
            return href.get("url")
        return href.get("url") or None

    def image_block(self, img: dict) -> list[str]:
        if self.drop_images:
            return []
        url = self.image_url(img.get("href"))
        if not url:
            return []
        title = squash(self.inline(str(img.get("title") or img.get("name") or "")))
        alt = title.replace("[", "(").replace("]", ")")
        if self.images_dir is not None:
            digest = hashlib.sha1(url.encode("utf-8")).hexdigest()[:12]
            suffix = Path(urlparse(url).path).suffix or ".jpg"
            name = f"{digest}{suffix}"
            try:
                self.fetcher.image(url, self.images_dir / name)
            except FetchError as exc:
                print(f"[!] image {url}: {exc}", file=sys.stderr)
            else:
                url = f"{self.images_dir.name}/{name}"
        out = [f"![{alt}]({url})"]
        if title:
            out.append(f"*{title}*")
        return out

    def b_image(self, node, level: int) -> list[str]:
        return self.image_block(node)

    def b_gallery(self, node, level: int) -> list[str]:
        out: list[str] = []
        for img in node.get("images") or []:
            out.extend(self.blocks(img, level))
        return out

    def b_hr(self, node, level: int) -> list[str]:
        return ["---"]

    def b_code(self, node, level: int) -> list[str]:
        code = node.get("preformatted") or node.get("code") or ""
        return ["```\n" + str(code).rstrip() + "\n```"]

    # ------------------------------------------------------- referenced content

    def index_file(self, rel: str) -> dict:
        if rel not in self._indexes:
            try:
                self._indexes[rel] = self.fetcher.json(rel)
            except SystemExit as exc:
                print(f"[!] {rel}: {exc}", file=sys.stderr)
                self._indexes[rel] = {}
        return self._indexes[rel]

    def pool(self, rel: str, key: str) -> list[dict]:
        cache_key = (rel, key)
        if cache_key not in self._pools:
            try:
                data = self.fetcher.json(rel)
            except SystemExit as exc:
                print(f"[!] {rel}: {exc}", file=sys.stderr)
                data = {}
            records = data if isinstance(data, list) else data.get(key) or []
            self._pools[cache_key] = [r for r in records if isinstance(r, dict)]
        return self._pools[cache_key]

    @staticmethod
    def resolve_copy(record: dict, records: list[dict]) -> dict:
        copy = record.get("_copy")
        if not copy:
            return record
        base = None
        for candidate in records:
            if (
                str(candidate.get("name", "")).lower()
                != str(copy.get("name", "")).lower()
            ):
                continue
            if copy.get("source") and candidate.get("source") != copy.get("source"):
                continue
            base = candidate
            break
        if base is None:
            return record
        merged = {k: v for k, v in base.items() if k != "_copy"}
        merged.update({k: v for k, v in record.items() if k != "_copy"})
        return merged

    def find_in(self, records: list[dict], name: str, source: str = "") -> dict | None:
        wanted = str(name).lower()
        fallback = None
        for record in records:
            if str(record.get("name", "")).lower() != wanted:
                continue
            if source and record.get("source") == source:
                return self.resolve_copy(record, records)
            if fallback is None:
                fallback = record
        return self.resolve_copy(fallback, records) if fallback else None

    def ref_source(self, node: dict) -> str:
        return str(node.get("source") or self.book_source)

    def named_pool(self, rel: str, key: str, name: str, source: str) -> dict | None:
        return self.find_in(self.pool(rel, key), name, source)

    def class_bundle(self, class_name: str, source: str) -> tuple[str, dict] | None:
        filename = self.index_file("class/index.json").get(class_name.lower())
        if not filename:
            return None
        rel = f"class/{filename}"
        record = self.find_in(self.pool(rel, "class"), class_name, source)
        if record is None:
            record = self.find_in(self.pool(rel, "class"), class_name, "")
        if record is None:
            return None
        return rel, record

    # -- per-tag renderers

    def ref_creature(self, node: dict, level: int) -> list[str]:
        name, source = str(node.get("name") or ""), self.ref_source(node)
        monster = self.monster(name, source) if self.statblocks else None
        if monster is None:
            return [self.reference_line(name, source)]
        return [self.render_monster(monster, level)]

    def reference_line(self, name: str, source: str) -> str:
        label = f"**{squash(self.inline(name))}**"
        if source:
            label += f" (*{source}*)"
        return label + " — see the source book for the full write-up."

    def ref_spell(self, node: dict, level: int) -> list[str]:
        name, source = str(node.get("name") or ""), self.ref_source(node)
        filename = self.index_file("spells/index.json").get(source)
        record = None
        if filename:
            record = self.named_pool(f"spells/{filename}", "spell", name, source)
        if record is None:
            return [self.reference_line(name, source)]
        return [self.render_spell(record, level)]

    def render_spell(self, spell: dict, level: int) -> str:
        lines = [self.heading(spell.get("name", "Spell"), level)]
        grade = int(spell.get("level") or 0)
        school = SCHOOLS.get(
            str(spell.get("school") or ""), str(spell.get("school") or "")
        )
        if grade == 0:
            lines.append(f"*{school} cantrip*")
        else:
            lines.append(f"*{ordinal(grade)}-level {school}*")
        stats = [
            ("Casting Time", self.spell_time(spell.get("time"))),
            ("Range", self.spell_range(spell.get("range"))),
            ("Components", self.spell_components(spell.get("components"))),
            ("Duration", self.spell_duration(spell.get("duration"))),
        ]
        stats = [(label, value) for label, value in stats if value]
        if stats:
            lines.append("  \n".join(f"**{label}:** {value}" for label, value in stats))
        lines.extend(self.blocks(spell.get("entries") or [], level + 1))
        higher = spell.get("entriesHigherLevel") or []
        for block in higher:
            if not isinstance(block, dict):
                continue
            label = str(block.get("name") or "At Higher Levels")
            text = " ".join(self.blocks(block.get("entries") or [], level + 1)).strip()
            lines.append(f"**{label}.** {text}".strip())
        return "\n\n".join(line for line in lines if line)

    def spell_time(self, value) -> str:
        if not value:
            return ""
        parts = []
        for item in value if isinstance(value, list) else [value]:
            if isinstance(item, dict):
                unit = str(item.get("unit", "action"))
                if unit == "bonus":
                    unit = "bonus action"
                text = f"{item.get('number', 1)} {unit}"
                condition = item.get("condition")
                if condition:
                    text = f"{text}, {condition}"
                parts.append(text)
            else:
                parts.append(str(item))
        return ", ".join(parts)

    def spell_range(self, value) -> str:
        if not value:
            return ""
        if isinstance(value, str):
            return value
        kind = value.get("type")
        if kind == "special":
            return "Special"
        if kind == "unlimited":
            return "Unlimited"
        if kind == "self":
            return "Self"
        if kind == "touch":
            return "Touch"
        distance = value.get("distance") or {}
        if isinstance(distance, dict) and distance.get("amount") is not None:
            text = f"{distance['amount']} {distance.get('type', 'feet')}"
        else:
            text = "Self"
        if value.get("type") in (
            "radius",
            "sphere",
            "cone",
            "line",
            "cube",
            "hemisphere",
        ):
            text = f"Self ({text} {kind})"
        return text

    def spell_components(self, value) -> str:
        if not value:
            return ""
        parts = []
        for key in ("v", "s", "m"):
            item = value.get(key)
            if not item:
                continue
            if key == "m":
                material = item if isinstance(item, str) else ""
                parts.append(f"M ({material})" if material else "M")
            else:
                parts.append(key.upper())
        return ", ".join(parts)

    def spell_duration(self, value) -> str:
        if not value:
            return ""
        parts = []
        for item in value if isinstance(value, list) else [value]:
            if not isinstance(item, dict):
                parts.append(str(item))
                continue
            kind = item.get("type")
            if kind == "instant":
                parts.append("Instantaneous")
            elif kind == "permanent":
                parts.append("Permanent")
            elif kind == "special":
                parts.append("Special")
            elif kind == "timed":
                duration = item.get("duration") or {}
                amount = duration.get("amount", 1)
                unit = duration.get("type", "minute")
                if amount != 1:
                    unit += "s"
                text = (
                    f"up to {amount} {unit}"
                    if item.get("concentration")
                    else f"{amount} {unit}"
                )
                if item.get("concentration"):
                    text = f"Concentration, {text}"
                parts.append(text)
            else:
                parts.append(str(kind or ""))
        return ", ".join(p for p in parts if p)

    def ref_race(self, node: dict, level: int) -> list[str]:
        name, source = str(node.get("name") or ""), self.ref_source(node)
        records = self.pool("races.json", "race")
        record = self.find_in(records, name, source)
        if record is None:
            return [self.reference_line(name, source)]
        out = [self.heading(record.get("name", name), level)]
        meta = self.race_meta(record)
        if meta:
            out.append(f"*{meta}*")
        ability = self.ability_bonus(record.get("ability"))
        if ability:
            out.append(f"**Ability Score Increase:** {ability}")
        out.extend(self.blocks(record.get("entries") or [], level + 1))
        race_name = str(record.get("name") or "")
        subs = [
            sub
            for sub in self.pool("races.json", "subrace")
            if sub.get("raceName") == race_name
            and sub.get("raceSource") == record.get("source")
            and str(sub.get("source")) in (str(record.get("source")), self.book_source)
        ]
        for sub in subs:
            sub = self.resolve_copy(sub, self.pool("races.json", "subrace"))
            sub_name = str(sub.get("name") or "Subrace")
            if race_name.lower() not in sub_name.lower():
                sub_name = f"{sub_name} {race_name}"
            out.append(self.heading(sub_name, level + 1))
            sub_ability = self.ability_bonus(sub.get("ability"))
            if sub_ability:
                out.append(f"**Ability Score Increase:** {sub_ability}")
            out.extend(self.blocks(sub.get("entries") or [], level + 2))
        return out

    def race_meta(self, record: dict) -> str:
        size = record.get("size")
        if isinstance(size, list) and size:
            size_text = " or ".join(SIZES.get(str(s), str(s)) for s in size)
        else:
            size_text = SIZES.get(str(size), str(size)) if size else ""
        speed = self.monster_speed(record.get("speed"))
        return ", ".join(p for p in (size_text, f"speed {speed}" if speed else "") if p)

    def ability_bonus(self, ability) -> str:
        parts = []
        for item in ability or []:
            if not isinstance(item, dict):
                continue
            choose = item.get("choose")
            if choose:
                amount = choose.get("amount", 1)
                from_list = choose.get("from") or []
                names = ", ".join(str(x).upper() for x in from_list[:6])
                parts.append(f"+{amount} to one of {names}")
                continue
            for key, value in item.items():
                if isinstance(value, int):
                    parts.append(f"+{value} {key.upper()}")
        return ", ".join(parts)

    def ref_class(self, node: dict, level: int) -> list[str]:
        name, source = str(node.get("name") or ""), self.ref_source(node)
        bundle = self.class_bundle(name, source)
        if bundle is None:
            return [self.reference_line(name, source)]
        rel, record = bundle
        class_source = str(record.get("source") or source)
        previous_context = (self._class_rel, self._class_source)
        self._class_rel, self._class_source = rel, class_source
        features = self.pool(rel, "classFeature")
        out = [self.heading(record.get("name", name), level)]

        hit_die = record.get("hd") or {}
        faces = hit_die.get("faces")
        if faces:
            out.append(
                "  \n".join(
                    [
                        f"**Hit Dice:** 1d{faces} per {str(record.get('name', name)).lower()} level",
                        f"**Hit Points at 1st Level:** {faces} + your Constitution modifier",
                        (
                            f"**Hit Points at Higher Levels:** 1d{faces} "
                            f"(or {faces // 2 + 1}) + your Constitution modifier per level after 1st"
                        ),
                    ]
                )
            )

        lines = []
        proficiencies = record.get("startingProficiencies") or {}
        for key, label in (
            ("armor", "Armor"),
            ("weapons", "Weapons"),
            ("tools", "Tools"),
        ):
            value = proficiencies.get(key)
            if value:
                lines.append(f"**{label}:** {self.proficiency_text(value)}")
        saves = record.get("proficiency")
        if saves:
            names = [ABILITY_NAMES.get(str(code), str(code)) for code in saves]
            lines.append(f"**Saving Throws:** {', '.join(names)}")
        skills = proficiencies.get("skills")
        if skills and isinstance(skills, list) and isinstance(skills[0], dict):
            choose = skills[0].get("choose") or {}
            options = ", ".join(str(x).title() for x in (choose.get("from") or []))
            if options:
                count = choose.get("count", 1)
                lines.append(f"**Skills:** Choose {count} from {options}")
            elif choose.get("any"):
                lines.append(
                    f"**Skills:** Choose {choose['any']} skills of your choice"
                )
        if lines:
            out.append("  \n".join(lines))

        equipment = (record.get("startingEquipment") or {}).get("default")
        if equipment:
            items = "\n".join(f"- {self.inline(str(item))}" for item in equipment)
            out.append(f"**Equipment:**\n{items}")

        table = self.class_level_table(
            rel, record, features, {class_source, self.book_source}
        )
        if table:
            out.append(table)

        sources = {class_source, self.book_source}
        rendered: list[tuple[int, str]] = []
        seen: set[tuple[str, int | None]] = set()
        for feature_name, feature_level in self.class_feature_order(record):
            if (feature_name, feature_level) in seen:
                continue
            seen.add((feature_name, feature_level))
            feature = self.find_class_feature(
                features, feature_name, sources, feature_level
            )
            if feature is None:
                continue
            shown_level = feature.get("level") or feature_level or 0
            label = f"**Level {shown_level}: {feature.get('name', feature_name)}.**"
            rendered.append(
                (
                    int(shown_level),
                    self.labeled(label, feature.get("entries"), level + 2),
                )
            )
        for _, block in sorted(rendered, key=lambda item: item[0]):
            out.append(block)

        subclasses = [
            sub
            for sub in self.pool(rel, "subclass")
            if sub.get("classSource") == class_source
            and str(sub.get("source")) == class_source
        ]
        for sub in subclasses:
            out.append(self.heading(sub.get("name", "Subclass"), level + 1))
            for entry in sub.get("subclassFeatures") or []:
                spec = str(entry).split("|")
                feature_name = spec[0]
                entry_level = (
                    int(spec[3]) if len(spec) > 3 and spec[3].isdigit() else None
                )
                feature = self.find_class_feature(
                    self.pool(rel, "subclassFeature"),
                    feature_name,
                    {str(sub.get("source"))},
                    entry_level,
                )
                if feature is None:
                    continue
                shown_level = feature.get("level", entry_level)
                label = f"**Level {shown_level}: {feature.get('name', feature_name)}.**"
                out.append(self.labeled(label, feature.get("entries"), level + 2))
        self._class_rel, self._class_source = previous_context
        return out

    def class_feature_order(self, record: dict) -> list[tuple[str, int | None]]:
        ordered: list[tuple[str, int | None]] = []
        for entry in record.get("classFeatures") or []:
            if isinstance(entry, str) and entry:
                spec = entry
            elif isinstance(entry, dict) and entry.get("classFeature"):
                spec = str(entry["classFeature"])
            else:
                continue
            parts = spec.split("|")
            level = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else None
            ordered.append((parts[0], level))
        return ordered

    def find_class_feature(
        self,
        records: list[dict],
        name: str,
        sources: set[str],
        level: int | None = None,
    ) -> dict | None:
        wanted = name.lower()
        fallback = None
        for record in records:
            if str(record.get("name", "")).lower() != wanted:
                continue
            if level is not None and record.get("level") != level:
                continue
            if sources and record.get("source") not in sources:
                if fallback is None:
                    fallback = record
                continue
            return record
        if fallback is not None and not sources:
            return fallback
        return None

    def labeled(self, label: str, entries, level: int) -> str:
        parts = self.blocks(entries or [], level)
        if not parts:
            return label
        return label + " " + "\n\n".join(parts)

    def class_level_table(
        self,
        rel: str,
        record: dict,
        features: list[dict],
        sources: set[str],
    ) -> str:
        groups = record.get("classTableGroups") or []
        extra_labels: list[str] = []
        extra_rows: list[list] = []
        for group in groups:
            rows = group.get("rows") or []
            if len(rows) != 20:
                continue
            extra_labels.extend(group.get("colLabels") or [])
            for index, row in enumerate(rows):
                if index >= len(extra_rows):
                    extra_rows.append([])
                extra_rows[index].extend(row)
        by_level: dict[int, list[str]] = {}
        for name, level in self.class_feature_order(record):
            if not level:
                continue
            feature = self.find_class_feature(features, name, sources, level)
            if feature is None:
                continue
            by_level.setdefault(level, []).append(str(feature.get("name", name)))
        header = ["Level", "Proficiency Bonus", "Features"] + extra_labels
        lines = [
            "| " + " | ".join(header) + " |",
            "|" + "|".join(["---"] * len(header)) + "|",
        ]
        for index in range(20):
            level = index + 1
            cells = [
                str(level),
                f"+{2 + (level - 1) // 4}",
                ", ".join(by_level.get(level, [])),
            ]
            row_values = extra_rows[index] if index < len(extra_rows) else []
            cells.extend(self.table_value(value) for value in row_values)
            cells.extend([""] * (len(header) - len(cells)))
            lines.append("| " + " | ".join(cells) + " |")
        if not extra_labels and not by_level:
            return ""
        return "\n".join(lines)

    def table_value(self, value) -> str:
        if isinstance(value, dict):
            if value.get("type") == "bonus":
                return self.signed(str(value.get("value", 0)))
            if value.get("type") == "dice":
                dice = value.get("toRoll") or []
                return ", ".join(
                    f"{d.get('number', 1)}d{d.get('faces', 0)}"
                    for d in dice
                    if isinstance(d, dict)
                )
            if value.get("type") == "cell":
                return self.cell_text(value.get("entry") or value.get("roll"))
            if value.get("type") == "row":
                return self.cell_text(value.get("row"))
            return self.cell_text(value.get("entry") or "")
        return self.cell_text(value)

    def proficiency_text(self, values) -> str:
        parts = []
        for value in values:
            if isinstance(value, dict):
                if value.get("proficiency"):
                    text = self.inline(str(value["proficiency"]))
                    if value.get("optional"):
                        text += " (optional)"
                    parts.append(text)
                else:
                    parts.append(
                        ", ".join(f"{k.title()}" for k, v in value.items() if v is True)
                    )
                continue
            text = self.inline(str(value))
            if text in ("simple", "martial"):
                text += " weapons"
            elif text == "shield":
                text = "shields"
            elif text in ("light", "medium", "heavy"):
                text += " armor"
            parts.append(text)
        return ", ".join(p for p in parts if p)

    def ref_subclass(self, node: dict, level: int) -> list[str]:
        name, source = str(node.get("name") or ""), self.ref_source(node)
        index = self.index_file("class/index.json")
        for filename in index.values():
            rel = f"class/{filename}"
            sub = self.find_in(self.pool(rel, "subclass"), name, source)
            if sub is None:
                continue
            out = [self.heading(sub.get("name", name), level)]
            for entry in sub.get("subclassFeatures") or []:
                spec = str(entry).split("|")
                feature_name = spec[0]
                entry_level = (
                    int(spec[3]) if len(spec) > 3 and spec[3].isdigit() else None
                )
                feature = self.find_class_feature(
                    self.pool(rel, "subclassFeature"),
                    feature_name,
                    {str(sub.get("source"))},
                    entry_level,
                )
                if feature is None:
                    continue
                shown_level = feature.get("level", entry_level)
                label = f"**Level {shown_level}: {feature.get('name', feature_name)}.**"
                out.append(self.labeled(label, feature.get("entries"), level + 2))
            return out
        return [self.reference_line(name, source)]

    def ref_item(self, node: dict, level: int) -> list[str]:
        name, source = str(node.get("name") or ""), self.ref_source(node)
        record = self.named_pool("items.json", "item", name, source)
        if record is None:
            record = self.named_pool("items-base.json", "item", name, source)
        if record is None:
            record = self.named_pool("items.json", "itemGroup", name, source)
        if record is None:
            return [self.reference_line(name, source)]
        return self.render_item(record, level)

    def render_item(self, record: dict, level: int) -> list[str]:
        self.rendered_names.add(str(record.get("name") or ""))
        out = [self.heading(record.get("name", "Item"), level)]
        meta = self.item_meta(record)
        if meta:
            out.append(f"*{meta}*")
        body = record.get("entries") or ([record["entry"]] if "entry" in record else [])
        out.extend(self.blocks(body, level + 1))
        return out

    def item_appendix(self, source: str, level: int = 2) -> list[str]:
        records = [
            record
            for record in self.pool("items.json", "item")
            if str(record.get("source")) == source
            and (record.get("rarity") or record.get("wondrous"))
            and str(record.get("name")) not in self.rendered_names
        ]
        records.sort(key=lambda record: str(record.get("name", "")).lower())
        out: list[str] = []
        if not records:
            return out
        out.append(self.heading("Magic Items", level))
        for record in records:
            out.extend(self.render_item(record, level + 1))
        return out

    def item_meta(self, record: dict) -> str:
        kind = record.get("type")
        if isinstance(kind, dict):
            kind = kind.get("type")
        type_text = ITEM_TYPES.get(str(kind), "")
        if not type_text:
            type_text = str(record.get("wondrous") and "wondrous item" or "").strip()
        rarity = str(record.get("rarity") or "").strip()
        headline = ", ".join(p for p in (type_text, rarity) if p)
        attune = record.get("reqAttune")
        if attune:
            text = str(attune)
            suffix = (
                f"(requires attunement {text})"
                if text not in ("true", "True")
                else "(requires attunement)"
            )
            headline = f"{headline} {suffix}".strip()
        return headline[:1].upper() + headline[1:] if headline else ""

    def ref_background(self, node: dict, level: int) -> list[str]:
        name, source = str(node.get("name") or ""), self.ref_source(node)
        record = self.named_pool("backgrounds.json", "background", name, source)
        if record is None:
            return [self.reference_line(name, source)]
        out = [self.heading(record.get("name", name), level)]
        out.extend(self.blocks(record.get("entries") or [], level + 1))
        return out

    def ref_condition(self, node: dict, level: int) -> list[str]:
        return self.simple_record(node, level, "conditionsdiseases.json", "condition")

    def ref_disease(self, node: dict, level: int) -> list[str]:
        return self.simple_record(node, level, "conditionsdiseases.json", "disease")

    def ref_status(self, node: dict, level: int) -> list[str]:
        return self.simple_record(node, level, "conditionsdiseases.json", "status")

    def ref_action(self, node: dict, level: int) -> list[str]:
        return self.simple_record(node, level, "actions.json", "action")

    def ref_feat(self, node: dict, level: int) -> list[str]:
        name, source = str(node.get("name") or ""), self.ref_source(node)
        record = self.named_pool("feats.json", "feat", name, source)
        if record is None:
            return [self.reference_line(name, source)]
        out = [self.heading(record.get("name", name), level)]
        meta = FEAT_CATEGORIES.get(str(record.get("category") or ""), "")
        if meta:
            out.append(f"*{meta}*")
        prerequisite = record.get("prerequisite")
        if prerequisite:
            out.append(f"**Prerequisite:** {self.text_list(prerequisite)}")
        out.extend(self.blocks(record.get("entries") or [], level + 1))
        return out

    def ref_optfeature(self, node: dict, level: int) -> list[str]:
        name, source = str(node.get("name") or ""), self.ref_source(node)
        record = self.named_pool(
            "optionalfeatures.json", "optionalfeature", name, source
        )
        if record is None:
            return [self.reference_line(name, source)]
        out = [self.heading(record.get("name", name), level)]
        types = record.get("featureType") or []
        labels = [OPTIONAL_FEATURE_TYPES.get(str(t), str(t)) for t in types]
        if labels:
            out.append(f"*{', '.join(labels)}*")
        prerequisite = record.get("prerequisite")
        if prerequisite:
            out.append(f"**Prerequisite:** {self.text_list(prerequisite)}")
        out.extend(self.blocks(record.get("entries") or [], level + 1))
        return out

    def ref_deity(self, node: dict, level: int) -> list[str]:
        name, source = str(node.get("name") or ""), self.ref_source(node)
        record = self.named_pool("deities.json", "deity", name, source)
        if record is None:
            return [self.reference_line(name, source)]
        out = [self.heading(record.get("name", name), level)]
        meta = []
        if record.get("pantheon"):
            meta.append(str(record["pantheon"]))
        if record.get("alignment"):
            meta.append(self.alignment_text(record["alignment"]))
        if record.get("domains"):
            meta.append("Domains: " + self.text_list(record["domains"]))
        if record.get("symbol"):
            meta.append("Symbol: " + squash(self.inline(str(record["symbol"]))))
        if meta:
            out.append(f"*{'; '.join(meta)}*")
        out.extend(self.blocks(record.get("entries") or [], level + 1))
        return out

    def ref_trap(self, node: dict, level: int) -> list[str]:
        return self.hazard_record(node, level, "trap")

    def ref_hazard(self, node: dict, level: int) -> list[str]:
        return self.hazard_record(node, level, "hazard")

    def hazard_record(self, node: dict, level: int, key: str) -> list[str]:
        name, source = str(node.get("name") or ""), self.ref_source(node)
        record = self.named_pool("trapshazards.json", key, name, source)
        if record is None:
            return [self.reference_line(name, source)]
        out = [self.heading(record.get("name", name), level)]
        kind = TRAP_TYPES.get(str(record.get("trapHazType") or ""), "")
        if kind:
            out.append(f"*{kind}*")
        rating = record.get("rating")
        if rating:
            out.append(f"**Challenge:** {self.cell_text(rating)}")
        out.extend(self.blocks(record.get("entries") or [], level + 1))
        return out

    def ref_object(self, node: dict, level: int) -> list[str]:
        name, source = str(node.get("name") or ""), self.ref_source(node)
        record = self.named_pool("objects.json", "object", name, source)
        if record is None:
            return [self.reference_line(name, source)]
        out = [self.heading(record.get("name", name), level)]
        size = record.get("size")
        if isinstance(size, list) and size:
            size = " or ".join(SIZES.get(str(s), str(s)) for s in size)
        out.append(f"*{size or 'Object'} object*" if size else "*Object*")
        stats = []
        if record.get("ac"):
            stats.append(f"**Armor Class** {record['ac']}")
        if record.get("hp"):
            stats.append(f"**Hit Points** {self.monster_hp(record['hp'])}")
        if stats:
            out.append("  \n".join(stats))
        table = self.ability_table(record)
        if table:
            out.append(table)
        extra = []
        if record.get("immune"):
            extra.append(f"**Damage Immunities** {self.text_list(record['immune'])}")
        if record.get("senses"):
            extra.append(f"**Senses** {self.text_list(record['senses'])}")
        if extra:
            out.append("  \n".join(extra))
        out.extend(self.blocks(record.get("entries") or [], level + 1))
        out.extend(self.features(record.get("actionEntries") or [], level + 1))
        return out

    def ref_vehicle(self, node: dict, level: int) -> list[str]:
        name, source = str(node.get("name") or ""), self.ref_source(node)
        record = self.named_pool("vehicles.json", "vehicle", name, source)
        if record is None:
            record = self.named_pool("vehicles.json", "vehicleUpgrade", name, source)
        if record is None:
            return [self.reference_line(name, source)]
        out = [self.heading(record.get("name", name), level)]
        kind = str(record.get("vehicleType") or "Vehicle").title()
        size = record.get("size")
        if isinstance(size, list) and size:
            size = " or ".join(SIZES.get(str(s), str(s)) for s in size)
        out.append(f"*{size} {kind.lower()}*" if size else f"*{kind}*")
        stats = []
        if record.get("ac"):
            stats.append(f"**Armor Class** {record['ac']}")
        if record.get("hp"):
            stats.append(f"**Hit Points** {self.monster_hp(record['hp'])}")
        speed = self.monster_speed(record.get("speed"))
        if speed:
            stats.append(f"**Speed** {speed}")
        if stats:
            out.append("  \n".join(stats))
        if record.get("immune"):
            out.append(f"**Damage Immunities** {self.text_list(record['immune'])}")
        out.extend(self.blocks(record.get("entries") or [], level + 1))
        return out

    def ref_magicitem(self, node: dict, level: int) -> list[str]:
        return self.ref_item(node, level)

    def ref_boon(self, node: dict, level: int) -> list[str]:
        return self.simple_record(node, level, "cultsboons.json", "boon")

    def ref_cult(self, node: dict, level: int) -> list[str]:
        return self.simple_record(node, level, "cultsboons.json", "cult")

    def simple_record(self, node: dict, level: int, rel: str, key: str) -> list[str]:
        name, source = str(node.get("name") or ""), self.ref_source(node)
        record = self.named_pool(rel, key, name, source)
        if record is None:
            return [self.reference_line(name, source)]
        out = [self.heading(record.get("name", name), level)]
        time = record.get("time")
        if time:
            out.append(f"*{self.spell_time(time)}*")
        out.extend(self.blocks(record.get("entries") or [], level + 1))
        return out

    # -- statblocks

    def bestiary_file(self, filename: str) -> list:
        if filename not in self._bestiary_files:
            try:
                data = self.fetcher.json(f"bestiary/{filename}")
            except SystemExit as exc:
                print(f"[!] bestiary {filename}: {exc}", file=sys.stderr)
                data = {}
            if isinstance(data, dict):
                monsters = data.get("monster") or []
            else:
                monsters = data or []
            self._bestiary_files[filename] = monsters
        return self._bestiary_files[filename]

    def monster(self, name: str, source: str) -> dict | None:
        key = (name, source)
        if key in self._monsters:
            return self._monsters[key]
        self._monsters[key] = None
        filename = self.bestiary_index().get(source)
        if not filename:
            return None
        monsters = self.bestiary_file(filename)
        wanted = name.lower()
        for monster in monsters:
            if str(monster.get("name", "")).lower() != wanted:
                continue
            if "_copy" in monster:
                base_name = monster["_copy"].get("name", name)
                for base in monsters:
                    if str(base.get("name", "")).lower() == str(base_name).lower():
                        merged = {
                            **base,
                            **{k: v for k, v in monster.items() if k != "_copy"},
                        }
                        self._monsters[key] = merged
                        return merged
            self._monsters[key] = monster
            return monster
        return None

    def b_statblock(self, node, level: int) -> list[str]:
        tag = str(node.get("tag") or "creature")
        handler = getattr(self, "ref_" + tag, None)
        if handler is not None:
            return handler(node, level)
        line = self.reference_line(str(node.get("name") or ""), self.ref_source(node))
        return [line]

    def render_monster(self, monster: dict, level: int) -> str:
        name = str(monster.get("name", "Creature"))
        lines: list[str] = []
        if squash(self.inline(name)) != self._last_heading:
            lines.append(self.heading(name, level))

        meta = self.monster_meta(monster)
        if meta:
            lines.append(f"*{meta}*")

        rows: list[str] = []
        ac = self.monster_ac(monster.get("ac"))
        if ac:
            rows.append(f"**Armor Class** {ac}")
        hp = self.monster_hp(monster.get("hp"))
        if hp:
            rows.append(f"**Hit Points** {hp}")
        speed = self.monster_speed(monster.get("speed"))
        if speed:
            rows.append(f"**Speed** {speed}")
        if rows:
            lines.append("  \n".join(rows))

        abilities = self.ability_table(monster)
        if abilities:
            lines.append(abilities)

        extra: list[str] = []
        saves = monster.get("save")
        if isinstance(saves, dict) and saves:
            extra.append("**Saving Throws** " + self.modifier_list(saves))
        skills = monster.get("skill")
        if isinstance(skills, dict) and skills:
            extra.append("**Skills** " + self.modifier_list(skills))
        for key, label in (
            ("resist", "Damage Resistances"),
            ("vulnerable", "Damage Vulnerabilities"),
            ("immune", "Damage Immunities"),
            ("conditionImmune", "Condition Immunities"),
        ):
            value = monster.get(key)
            if value:
                extra.append(f"**{label}** " + self.text_list(value))
        senses = monster.get("senses")
        if senses:
            items = (
                [self.text_list(senses)] if isinstance(senses, list) else [str(senses)]
            )
            passive = monster.get("passive")
            if passive:
                items.append(f"passive Perception {passive}")
            extra.append("**Senses** " + ", ".join(i for i in items if i))
        languages = monster.get("languages")
        if languages:
            extra.append("**Languages** " + self.text_list(languages))
        cr = monster.get("cr")
        if cr:
            extra.append("**Challenge** " + self.challenge(cr))
        if extra:
            lines.append("  \n".join(extra))

        for key, label in (
            ("trait", None),
            ("action", None),
            ("bonus", "Bonus Actions"),
            ("reaction", "Reactions"),
            ("legendary", "Legendary Actions"),
            ("mythic", "Mythic Actions"),
        ):
            entries = monster.get(key)
            if not entries:
                continue
            if label:
                lines.append(f"**{label}**")
            lines.extend(self.features(entries, level))
        lines.extend(self.spellcasting(monster.get("spellcasting"), level))
        return "\n\n".join(line for line in lines if line and line.strip())

    def monster_meta(self, monster: dict) -> str:
        size = monster.get("size")
        if isinstance(size, list):
            size_text = " or ".join(SIZES.get(str(s), str(s)) for s in size)
        elif size:
            size_text = SIZES.get(str(size), str(size))
        else:
            size_text = ""

        kind = monster.get("type")
        type_text = ""
        if isinstance(kind, dict):
            type_text = str(kind.get("type") or "")
            tags = kind.get("tags")
            if tags:
                tag_text = []
                for tag in tags if isinstance(tags, list) else [tags]:
                    if isinstance(tag, dict):
                        tag = tag.get("tag") or ""
                    tag_text.append(str(tag).replace("{@i ", "").replace("}", ""))
                if tag_text:
                    type_text += " (" + ", ".join(tag_text) + ")"
            if kind.get("swarmSize"):
                swarm = kind["swarmSize"]
                swarm = swarm[0] if isinstance(swarm, list) and swarm else swarm
                type_text = f"swarm of {SIZES.get(str(swarm), str(swarm))} {type_text}s"
        elif kind:
            type_text = str(kind)

        align_text = self.alignment_text(monster.get("alignment"))
        headline = " ".join(p for p in (size_text, type_text) if p)
        return f"{headline}, {align_text}" if align_text else headline

    @staticmethod
    def alignment_text(alignment) -> str:
        codes = {
            "L": "lawful",
            "N": "neutral",
            "C": "chaotic",
            "G": "good",
            "E": "evil",
            "U": "unaligned",
            "A": "any alignment",
            "NX": "neutral",
        }
        if not alignment:
            return ""
        values = alignment if isinstance(alignment, list) else [alignment]
        words: list[str] = []
        chance = None
        for value in values:
            if isinstance(value, dict):
                if value.get("chance"):
                    chance = value["chance"]
                value = value.get("alignment") or ""
            text = str(value).strip()
            if not text:
                continue
            words.append(codes.get(text.upper(), text))
        text = " ".join(words)
        return f"{text} ({chance}%)" if chance else text

    def monster_ac(self, ac) -> str:
        if ac is None:
            return ""
        parts = []
        for item in ac if isinstance(ac, list) else [ac]:
            if isinstance(item, dict):
                text = str(item.get("ac") or "")
                source = item.get("from")
                if source:
                    source = source if isinstance(source, list) else [source]
                    text += " (" + ", ".join(self.inline(str(s)) for s in source) + ")"
                condition = item.get("condition")
                if condition:
                    text += f" {self.inline(str(condition))}"
                parts.append(text)
            else:
                parts.append(str(item))
        return "; ".join(parts)

    def monster_hp(self, hp) -> str:
        if hp is None:
            return ""
        if isinstance(hp, list):
            return "; ".join(self.monster_hp(item) for item in hp)
        if isinstance(hp, dict):
            special = hp.get("special")
            if special:
                return self.inline(str(special))
            text = str(hp.get("average", ""))
            if hp.get("formula"):
                text += f" ({hp['formula']})"
            return text
        return str(hp)

    def monster_speed(self, speed) -> str:
        if speed is None:
            return ""
        if isinstance(speed, dict):
            order = ("walk", "burrow", "climb", "fly", "swim")
            parts = []
            for key in order:
                if key not in speed:
                    continue
                value = speed[key]
                condition = None
                if isinstance(value, dict):
                    condition = value.get("condition")
                    value = value.get("number")
                label = "" if key == "walk" else f"{key} "
                text = f"{label}{value} ft."
                if condition:
                    condition = self.inline(str(condition))
                    text += (
                        condition if condition.startswith("(") else f" ({condition})"
                    )
                parts.append(text)
            return ", ".join(parts)
        return f"{speed} ft."

    def ability_table(self, monster: dict) -> str:
        labels = ("STR", "DEX", "CON", "INT", "WIS", "CHA")
        keys = ("str", "dex", "con", "int", "wis", "cha")
        if not any(monster.get(k) is not None for k in keys):
            return ""
        header = "| " + " | ".join(labels) + " |"
        sep = "|" + "|".join([":---:"] * len(labels)) + "|"
        values = []
        for key in keys:
            score = monster.get(key)
            if score is None:
                values.append(" | ")
            else:
                mod = self.modifier(int(score))
                values.append(f" {score} ({mod}) ")
        return "\n".join([header, sep, "|" + "|".join(values) + "|"])

    @staticmethod
    def modifier(score: int) -> str:
        mod = (score - 10) // 2
        return f"+{mod}" if mod >= 0 else str(mod)

    def modifier_list(self, values: dict) -> str:
        parts = []
        for key, value in values.items():
            label = str(key)
            label = label[:1].upper() + label[1:].replace("_", " ")
            parts.append(f"{label} {value}")
        return ", ".join(parts)

    def text_list(self, values) -> str:
        if isinstance(values, str):
            return squash(self.inline(values))
        out = []
        for value in values:
            if isinstance(value, dict):
                if "special" in value:
                    out.append(str(value["special"]))
                elif "note" in value:
                    out.append(squash(self.inline(str(value["note"]))))
                elif "resist" in value:
                    out.append(self.text_list(value["resist"]))
                elif "choose" in value:
                    choose = value["choose"]
                    count = choose.get("count") or choose.get("amount") or 1
                    out.append(f"{count} of your choice")
                elif any(item is True for item in value.values()):
                    out.extend(
                        str(key).replace("_", " ").title()
                        for key, flag in value.items()
                        if flag is True
                    )
                else:
                    out.append(squash(self.inline(str(value.get("value") or ""))))
            else:
                out.append(squash(self.inline(str(value))))
        return ", ".join(o for o in out if o)

    def challenge(self, cr) -> str:
        if isinstance(cr, list):
            return "; ".join(self.challenge(item) for item in cr)
        if isinstance(cr, dict):
            text = str(cr.get("cr") or "")
            xp = cr.get("xp")
        else:
            text = str(cr)
            xp = XP_BY_CR.get(text)
        pieces = [text]
        if xp:
            pieces.append(f"({xp} XP)")
        return " ".join(p for p in pieces if p)

    def features(self, entries, level: int) -> list[str]:
        out: list[str] = []
        for entry in entries:
            if isinstance(entry, str):
                out.append(squash(self.inline(entry)))
                continue
            if not isinstance(entry, dict):
                out.extend(self.blocks(entry, level))
                continue
            name = entry.get("name")
            body = entry.get("entries")
            if body is None and "entry" in entry:
                body = [entry["entry"]]
            text = " ".join(self.blocks(body or [], level + 1)).strip()
            if name:
                out.append(f"**{squash(self.inline(str(name)))}.** {text}".strip())
            elif text:
                out.append(text)
        return out

    def spellcasting(self, blocks, level: int) -> list[str]:
        if not blocks:
            return []
        out: list[str] = []
        for block in blocks if isinstance(blocks, list) else [blocks]:
            if not isinstance(block, dict):
                continue
            name = block.get("name") or "Spellcasting"
            header = " ".join(
                self.blocks(block.get("headerEntries") or [], level + 1)
            ).strip()
            out.append(f"**{squash(self.inline(str(name)))}.** {header}".strip())
            will = block.get("will")
            if will:
                out.append("*At will:* " + ", ".join(self.spell_names(will)))
            daily = block.get("daily")
            if isinstance(daily, dict):
                for key in sorted(daily, key=lambda k: (len(str(k)), str(k))):
                    times = str(key)
                    each = times.endswith("e")
                    if each:
                        times = times[:-1]
                    label = f"{times}/day" + (" each" if each else "")
                    out.append(f"*{label}:* " + ", ".join(self.spell_names(daily[key])))
            elif daily:
                out.append("*Daily:* " + ", ".join(self.spell_names(daily)))
            spells = block.get("spells")
            if isinstance(spells, dict):
                for key, value in spells.items():
                    names = value if isinstance(value, list) else [value]
                    out.append(f"*{key}:* " + ", ".join(self.spell_names(names)))
        return out

    def spell_names(self, values) -> list[str]:
        if isinstance(values, dict):
            values = [values]
        out = []
        for value in values:
            if isinstance(value, dict):
                value = value.get("entry") or value.get("name") or ""
            text = squash(self.inline(str(value)))
            if text:
                out.append(text)
        return out

    # ----------------------------------------------------------- inline level

    def inline(self, text) -> str:
        if text is None:
            return ""
        if not isinstance(text, str):
            text = str(text)
        out: list[str] = []
        index = 0
        length = len(text)
        while True:
            start = text.find("{@", index)
            if start < 0:
                out.append(text[index:])
                break
            out.append(text[index:start])
            depth = 0
            end = -1
            for position in range(start, length):
                char = text[position]
                if char == "{":
                    depth += 1
                elif char == "}":
                    depth -= 1
                    if depth == 0:
                        end = position
                        break
            if end < 0:
                out.append(text[start:])
                break
            out.append(self.tag(text[start + 2 : end]))
            index = end + 1
        return "".join(out)

    @staticmethod
    def split_args(body: str) -> list[str]:
        args: list[str] = []
        depth = 0
        current: list[str] = []
        for char in body:
            if char == "{":
                depth += 1
            elif char == "}":
                depth = max(0, depth - 1)
            if char == "|" and depth == 0:
                args.append("".join(current))
                current = []
                continue
            current.append(char)
        args.append("".join(current))
        return args

    def tag(self, inner: str) -> str:
        if not inner:
            return ""
        name, _, body = inner.partition(" ")
        name = name.strip()
        args = [a.strip() for a in self.split_args(body)] if body else []
        first = args[0] if args else ""
        second = args[1] if len(args) > 1 else ""

        styled = {
            "b": ("**", "**"),
            "bold": ("**", "**"),
            "strong": ("**", "**"),
            "highlight": ("**", "**"),
            "i": ("*", "*"),
            "italic": ("*", "*"),
            "em": ("*", "*"),
            "note": ("*", "*"),
            "tip": ("*", "*"),
            "s": ("~~", "~~"),
            "strike": ("~~", "~~"),
            "strikethrough": ("~~", "~~"),
            "u": ("<u>", "</u>"),
            "underline": ("<u>", "</u>"),
            "sup": ("<sup>", "</sup>"),
            "sub": ("<sub>", "</sub>"),
            "code": ("`", "`"),
        }
        if name in styled:
            left, right = styled[name]
            return f"{left}{self.inline(first)}{right}"

        if name == "link":
            label = self.inline(first) or second
            return f"[{label}]({second})" if second else label
        if name == "5etools":
            label = self.inline(first) or second
            if second:
                target = second if second.startswith("http") else f"{SITE}/{second}"
                return f"[{label}]({target})"
            return label
        if name in ("color", "style", "font", "comic", "adventure", "book"):
            return self.inline(first)

        if name == "hit":
            return self.signed(first)
        if name == "hitYourSpellAttack":
            return "your spell attack modifier"
        if name in ("d20", "bonus"):
            return self.signed(first)
        if name == "dc":
            return f"DC {first}"
        if name == "chance":
            return f"{first} percent"
        if name == "recharge":
            return f"(Recharge {first}–6)" if first else "(Recharge 6)"
        if name == "atk":
            return ATTACK_KINDS.get(first, f"*{first} Attack:*")
        if name in (
            "dice",
            "damage",
            "scaledice",
            "scaledamage",
            "hazard",
            "unit",
            "sense",
        ):
            return first or second
        if name == "area":
            return self.inline(first)
        if name == "filter":
            return self.inline(first)

        # Reference tags (spell/item/creature/...) carry an optional display name
        # after the source; class and subclass put it one slot later.
        display = first or second
        if name in DISPLAY_NAME_TAGS and len(args) > 2 and args[2]:
            display = args[2]
        if name == "class" and len(args) > 2 and args[2]:
            display = args[2]
        if name == "subclass" and len(args) > 4 and args[4]:
            display = args[4]
        return self.inline(display)

    @staticmethod
    def signed(value: str) -> str:
        try:
            number = int(value)
        except (TypeError, ValueError):
            return str(value)
        return f"+{number}" if number >= 0 else str(number)


def slug(text: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9]+", "-", text).strip("-").lower()
    return cleaned or "book"


def chapter_filename(index: int, label: str) -> str:
    return f"{index + 1:02d}-{slug(label)[:60]}.md"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Convert a 5e.tools book or adventure link into a Markdown file.",
    )
    parser.add_argument(
        "target", help="5e.tools URL (book.html#PHB, adventure.html#CoS) or bare id"
    )
    parser.add_argument("-o", "--output", help="output .md path (default: <id>.md)")
    parser.add_argument(
        "--split",
        metavar="DIR",
        help="write one Markdown file per top-level chapter into DIR instead of one file",
    )
    parser.add_argument(
        "--images",
        action="store_true",
        help="download images next to the output and link them locally",
    )
    parser.add_argument("--no-images", action="store_true", help="omit images entirely")
    parser.add_argument(
        "--no-statblocks",
        action="store_true",
        help="keep creature references as one-line pointers instead of inlining the stat block",
    )
    parser.add_argument(
        "--append-items",
        action="store_true",
        help="append the magic items of this source that the book itself does not describe"
        " (the DMG and similar books only reference them)",
    )
    parser.add_argument("--cache-dir", help="cache downloaded JSON (default: none)")
    parser.add_argument(
        "--debug", action="store_true", help="report unhandled entry types"
    )
    args = parser.parse_args(argv)

    cache = Path(args.cache_dir).expanduser() if args.cache_dir else None
    fetcher = Fetcher(cache)
    converter = Converter(
        fetcher,
        statblocks=not args.no_statblocks,
        drop_images=args.no_images,
        debug=args.debug,
    )

    kind, ident, title = converter.resolve(args.target)
    converter.book_source = ident
    rel = f"{kind}/{'book' if kind == 'book' else 'adventure'}-{ident.lower()}.json"
    print(f"[*] {kind} {ident}: {title}", file=sys.stderr)
    data = fetcher.json(rel)

    root = data.get("bookData") if kind == "book" else data.get("adventureData")
    if root is None:
        root = data.get("data") or []
    if isinstance(root, dict):
        root = [root]
    body = []
    for block in root:
        if isinstance(block, dict):
            body.extend(block.get("data") or block.get("entries") or [])
        elif isinstance(block, list):
            body.extend(block)
        else:
            body.append(block)
    if not body:
        raise SystemExit(f"No content found in {rel}")

    source_url = f"{SITE}/{kind}.html#{ident}"

    if args.split:
        out_dir = Path(args.split).expanduser()
        out_dir.mkdir(parents=True, exist_ok=True)
        images_dir = (out_dir / "images") if args.images else None
        converter.images_dir = images_dir
        documents = converter.chapter_documents(title, body, source_url)
        for index, (label, text) in enumerate(documents):
            path = out_dir / chapter_filename(index, label)
            path.write_text(text, encoding="utf-8")
            print(f"[+] {path}", file=sys.stderr)
        print(f"[+] {len(documents)} chapter files in {out_dir}", file=sys.stderr)
        return 0

    output = Path(args.output).expanduser() if args.output else Path(f"{ident}.md")
    if args.images:
        converter.images_dir = output.parent / "images"
        if output.parent and not output.parent.exists():
            output.parent.mkdir(parents=True, exist_ok=True)

    if args.append_items:
        body = list(body) + converter.item_appendix(ident)

    text = converter.document(title, body, source_url)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(text, encoding="utf-8")
    words = len(text.split())
    print(f"[+] {output} ({words:,} words)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
