"""The markup of the bot's messages: bold, italic, a quote and a link over plain text.

The templates of the bot use four marks:

    **жирный**    _курсив_    > цитата (a whole line)    [текст](https://…)

``parse`` takes them out: the text a patient reads has no marks in it, and the marks become
spans beside it — in characters of that text. The Telegram layer turns the spans into
entities; with no entities the same text goes as it is. ``esc`` is for everything that did
not come from the bot's own templates (a patient's name, a complaint, an answer of the
administrators or of DeepSeek): its asterisks and underscores stay what they were.

Rules that need no marks in a template (``render``): a sentence with 103 or 112 stands on a
line of its own behind ⚠️ and is bold; a service note is a quote; an example of what to
type — «например «завтра вечером»» — is italic.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

Kind = Literal["bold", "italic", "quote", "link"]
AMBULANCE = "⚠️"
PLACE = "📍"

_ESCAPED = "\\*_[]>"
_LINK = re.compile(r"\[((?:[^\]\\\n]|\\.)*)\]\((https?://[^\s()]+)\)")
_WORD = re.compile(r"\w")
# «… на приёме. При угрозе жизни звоните 103 или 112.» — where one sentence ends
_SENTENCE = re.compile(r"(?<=[.!?…])\s+(?=[«\"(]?[А-ЯЁA-Z⚠])")
_CALL = re.compile(r"(?<![\d.,/№-])\b(?:103|112)\b(?![\d/-])")
_EXAMPLE = re.compile(r"«([^«»\n]+)»")


@dataclass(frozen=True)
class Span:
    kind: Kind
    start: int  # in characters of the plain text
    end: int
    url: str | None = None


def esc(text: str) -> str:
    """A text that is not a template of the bot: every mark in it is a plain character."""
    escaped = re.sub(r"([\\*_\[\]])", r"\\\1", text)
    return re.sub(r"(?m)^>", r"\\>", escaped)


def bold(text: str) -> str:
    return f"**{text}**" if text else text


def italic(text: str) -> str:
    return f"_{text}_" if text else text


def quote(text: str) -> str:
    return "\n".join(f"> {line}" for line in text.split("\n"))


def link(text: str, url: str) -> str:
    return f"[{text}]({url})"


def _unescape(text: str) -> str:
    return re.sub(r"\\(.)", r"\1", text)


def _closing_bold(body: str, at: int) -> int:
    """Where the «**» that closes the one before ``at`` stands, or -1."""
    i = at
    while i < len(body) - 1:
        if body[i] == "\\":
            i += 2
            continue
        if body[i] == "*" and body[i + 1] == "*" and i > at:
            return i
        i += 1
    return -1


def _closing_italic(body: str, at: int) -> int:
    """Where the «_» that closes the one before ``at`` stands, or -1: right after a word
    and before a space, a punctuation mark or the end."""
    i = at
    while i < len(body):
        if body[i] == "\\":
            i += 2
            continue
        if body[i] == "_" and i > at and not body[i - 1].isspace():
            after = body[i + 1 : i + 2]
            if not after or not _WORD.match(after):
                return i
        i += 1
    return -1


def _inline(body: str, offset: int) -> tuple[str, list[Span]]:
    """One line without its «> »: the text and the spans of bold, italic and links."""
    out: list[str] = []
    spans: list[Span] = []
    length = 0  # of the plain text so far
    bold_at: int | None = None
    bold_end = italic_end = -1
    italic_from: int | None = None
    i = 0
    while i < len(body):
        ch = body[i]
        if ch == "\\" and i + 1 < len(body) and body[i + 1] in _ESCAPED:
            out.append(body[i + 1])
            length += 1
            i += 2
            continue
        if ch == "*" and body.startswith("**", i):
            if bold_at is not None and i == bold_end:
                if length > bold_at:
                    spans.append(Span("bold", offset + bold_at, offset + length))
                bold_at, i = None, i + 2
                continue
            closing = _closing_bold(body, i + 2) if bold_at is None else -1
            if closing > i + 2:
                bold_at, bold_end, i = length, closing, i + 2
                continue
        if ch == "_":
            if italic_from is not None and i == italic_end:
                if length > italic_from:
                    spans.append(Span("italic", offset + italic_from, offset + length))
                italic_from, i = None, i + 1
                continue
            opens = italic_from is None and (i == 0 or not _WORD.match(body[i - 1]))
            opens = opens and i + 1 < len(body) and not body[i + 1].isspace()
            closing = _closing_italic(body, i + 1) if opens else -1
            if closing > i + 1 and (bold_at is None or closing < bold_end):
                italic_from, italic_end, i = length, closing, i + 1
                continue
        if ch == "[" and (found := _LINK.match(body, i)):
            text = _unescape(found[1])
            if text:
                spans.append(Span("link", offset + length, offset + length + len(text), found[2]))
            out.append(text)
            length += len(text)
            i = found.end()
            continue
        out.append(ch)
        length += 1
        i += 1
    return "".join(out), spans


def parse(marked: str) -> tuple[str, tuple[Span, ...]]:
    """The plain text and the spans of a marked one. A mark that does not close is text."""
    out: list[str] = []
    spans: list[Span] = []
    pos = 0
    quoted_from: int | None = None
    lines = marked.split("\n")
    for n, line in enumerate(lines):
        quoted = line.startswith("> ")
        if quoted and quoted_from is None:
            quoted_from = pos
        if not quoted and quoted_from is not None:
            spans.append(Span("quote", quoted_from, pos - 1))  # without the line break
            quoted_from = None
        text, inner = _inline(line[2:] if quoted else line, pos)
        out.append(text)
        spans += inner
        pos += len(text)
        if n < len(lines) - 1:
            out.append("\n")
            pos += 1
    if quoted_from is not None:
        spans.append(Span("quote", quoted_from, pos))
    spans = [s for s in spans if s.end > s.start]
    return "".join(out), tuple(sorted(spans, key=lambda s: (s.start, -s.end)))


def plain(marked: str) -> str:
    return parse(marked)[0]


def ambulance(text: str) -> str:
    """Every sentence with 103 or 112 on a line of its own, behind ⚠️. Done once: a line
    that has the sign already, and a line with an address, stay as they are."""
    lines = []
    for line in text.split("\n"):
        head = "> " if line.startswith("> ") else ""
        body = line[len(head) :]
        if not _CALL.search(body) or body.startswith((AMBULANCE, PLACE, "•")):
            lines.append(line)
            continue
        group: list[str] = []
        for sentence in _SENTENCE.split(body):
            if _CALL.search(sentence):
                if group:
                    lines.append(head + " ".join(group))
                    group = []
                lines.append(f"{head}{AMBULANCE} {sentence}")
            else:
                group.append(sentence)
        if group:
            lines.append(head + " ".join(group))
    return "\n".join(lines)


def _has_bold(marked: str) -> bool:
    return "**" in marked.replace("\\*", "")


def _sentences(line: str) -> list[str]:
    return re.split(r"(?<=[.!?…])\s+", line)


def lead(marked: str, notes: Iterable[str] = ()) -> str:
    """The bold line of a screen — what the patient is to answer or to read first.

    The question: the first line with «?», its sentences that ask («С чем обращаетесь? Что
    произошло?», not «Опишите своими словами…» after them). No question — the last line
    that ends with a colon («Выберите, пожалуйста, время:»); no such line — the last one
    that is not a service note («Начнём с ФИО.»). A text with a bold of its own stays.
    """
    if _has_bold(marked):
        return marked
    skipped = (*notes, "")
    lines = marked.split("\n")

    def plain_line(line: str) -> bool:
        return line not in skipped and not line.startswith(("> ", AMBULANCE, PLACE, "•"))

    for n, line in enumerate(lines):
        if "?" not in line or not plain_line(line):
            continue
        parts = _sentences(line)
        first = next(i for i, part in enumerate(parts) if "?" in part)
        last = first
        while last + 1 < len(parts) and parts[last + 1].rstrip('»")').endswith("?"):
            last += 1
        asked = " ".join(parts[first : last + 1])
        lines[n] = line.replace(asked, bold(asked), 1)
        return "\n".join(lines)
    candidates = [n for n, line in enumerate(lines) if plain_line(line)]
    if not candidates:
        return marked
    colon = [n for n in candidates if lines[n].rstrip().endswith(":")]
    n = colon[-1] if colon else candidates[-1]
    lines[n] = bold(lines[n])
    return "\n".join(lines)


def first_sentence(marked: str) -> str:
    """A route: its first sentence is the bold line, what follows goes to the next line. A
    route that begins with the line about the ambulance has that line for it."""
    if _has_bold(marked) or marked.startswith(AMBULANCE):
        return marked
    head, sep, tail = marked.partition("\n")
    parts = _sentences(head)
    rest = " ".join(parts[1:])
    lines = [bold(parts[0]), *([rest] if rest else [])]
    return "\n".join(lines) + sep + tail


def _covered(spans: Iterable[Span], kind: Kind, start: int, end: int) -> bool:
    return any(s.kind == kind and s.start <= start and end <= s.end for s in spans)


def rules(text: str, spans: tuple[Span, ...], quoted: Iterable[str] = ()) -> tuple[Span, ...]:
    """The spans no template has to ask for: an ambulance line is bold, a service note is
    a quote, an example of what to type is italic."""
    found = list(spans)
    notes = tuple(quoted)
    pos = 0
    for line in text.split("\n"):
        end = pos + len(line)
        if line.startswith(AMBULANCE):
            start = (
                pos
                + len(AMBULANCE)
                + (1 if line[len(AMBULANCE) : len(AMBULANCE) + 1] == " " else 0)
            )
            if end > start and not _covered(found, "bold", start, end):
                found.append(Span("bold", start, end))
        elif line and line in notes and not _covered(found, "quote", pos, end):
            found.append(Span("quote", pos, end))
        elif "например" in line.lower():
            after = line.lower().index("например")
            for example in _EXAMPLE.finditer(line, after):
                start, stop = pos + example.start(1), pos + example.end(1)
                if not _covered(found, "italic", start, stop):
                    found.append(Span("italic", start, stop))
        pos = end + 1
    return tuple(sorted(found, key=lambda s: (s.start, -s.end)))


def render(marked: str, quoted: Iterable[str] = ()) -> tuple[str, tuple[Span, ...]]:
    """What ``Ctx.say`` makes of a text: the ambulance lines, the marks, the rules."""
    text, spans = parse(ambulance(marked))
    return text, rules(text, spans, quoted)
