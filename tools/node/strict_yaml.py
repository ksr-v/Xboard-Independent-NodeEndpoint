"""Limited read-only parser for the audited Node block YAML, with scalar spans.

Unsupported YAML is rejected. This is not a general YAML implementation.
"""
import json
import re
from dataclasses import dataclass


class LayoutError(ValueError):
    pass


@dataclass
class Node:
    value: object
    start: int = -1
    end: int = -1

    def plain(self):
        if isinstance(self.value, dict):
            return {k: v.plain() for k, v in self.value.items()}
        if isinstance(self.value, list):
            return [v.plain() for v in self.value]
        return self.value

    def get(self, key, default=None):
        if not isinstance(self.value, dict):
            raise LayoutError("expected a block mapping")
        return self.value.get(key, default)


def scalar(text):
    text = text.strip()
    if not text:
        return None
    if text[0] in "&*!|>@`" or text == "<<":
        raise LayoutError("anchors, tags, aliases and block strings are unsupported")
    if text.startswith('"') or text.startswith('[') or text.startswith('{'):
        try:
            value = json.loads(text)
        except ValueError as exc:
            raise LayoutError("only JSON-compatible quoted/flow values are supported") from exc
        if isinstance(value, (dict, list)) and value:
            raise LayoutError("nonempty flow collections are unsupported")
        return value
    if text.startswith("'"):
        if len(text) < 2 or not text.endswith("'") or "\n" in text:
            raise LayoutError("invalid single-quoted scalar")
        return text[1:-1].replace("''", "'")
    if any(c in text for c in "[]{}") or re.search(r"(^|\s)[&*!]", text):
        raise LayoutError("unsupported plain scalar syntax")
    if text in ("true", "false"):
        return text == "true"
    if text in ("null", "~"):
        return None
    if re.fullmatch(r"0|[1-9][0-9]*|-[1-9][0-9]*", text):
        return int(text)
    if re.search(r":\s", text) or text.lower() in ("yes", "no", "on", "off"):
        raise LayoutError("ambiguous plain scalar; use an explicit quoted string")
    return text


def without_comment(text):
    quote = None
    escaped = False
    index = 0
    while index < len(text):
        char = text[index]
        if quote == '"' and escaped:
            escaped = False
        elif quote == '"' and char == "\\":
            escaped = True
        elif quote == "'" and char == "'" and index + 1 < len(text) and text[index + 1] == "'":
            index += 1
        elif char in ("'", '"'):
            if quote == char:
                quote = None
            elif quote is None:
                quote = char
        elif char == "#" and quote is None and (index == 0 or text[index - 1].isspace()):
            return text[:index].rstrip()
        index += 1
    if quote is not None:
        raise LayoutError("unterminated quoted scalar")
    return text.rstrip()


def parse(source):
    tokens = []
    offset = 0
    for physical in source.splitlines(keepends=True):
        line = physical.rstrip("\r\n")
        if "\t" in line[:len(line) - len(line.lstrip())]:
            raise LayoutError("tabs in indentation are unsupported")
        content = without_comment(line.lstrip(" "))
        indent = len(line) - len(line.lstrip(" "))
        start = offset + indent
        offset += len(physical)
        if not content:
            continue
        if content in ("---", "...") or content.startswith("%"):
            raise LayoutError("YAML directives and document boundaries are unsupported")
        if content.startswith("- ") and re.match(r"[A-Za-z_][A-Za-z0-9_-]*:(?:\s|$)", content[2:]):
            tokens.append((indent, "-", start))
            tokens.append((indent + 2, content[2:], start + 2))
        else:
            tokens.append((indent, content, start))
    if not tokens or tokens[0][0] != 0:
        raise LayoutError("expected a nonempty root mapping")
    cursor = 0

    def block(indent):
        nonlocal cursor
        sequence = tokens[cursor][1] == "-" or tokens[cursor][1].startswith("- ")
        value = [] if sequence else {}
        while cursor < len(tokens) and tokens[cursor][0] == indent:
            _, text, start = tokens[cursor]
            cursor += 1
            if sequence:
                if text != "-" and not text.startswith("- "):
                    raise LayoutError("mixed block mapping/list")
                raw = text[2:] if text.startswith("- ") else ""
                child = Node(scalar(raw), start + 2, start + len(text)) if raw else None
            else:
                match = re.fullmatch(r"([A-Za-z_][A-Za-z0-9_-]*):(?:\s+(.*))?", text)
                if not match:
                    raise LayoutError("only ordinary unquoted block keys are supported")
                key, raw = match.groups()
                if key in value or key == "<<":
                    raise LayoutError("duplicate or merged mapping key")
                child = Node(scalar(raw), start + match.start(2), start + len(text)) if raw is not None else None
            if child is None:
                if cursor < len(tokens) and tokens[cursor][0] > indent:
                    child = block(tokens[cursor][0])
                else:
                    child = Node(None)
            elif cursor < len(tokens) and tokens[cursor][0] > indent:
                raise LayoutError("unexpected nesting after scalar")
            if sequence:
                value.append(child)
            else:
                value[key] = child
        return Node(value)

    root = block(0)
    if cursor != len(tokens) or not isinstance(root.value, dict):
        raise LayoutError("unsupported indentation or root layout")
    return root
