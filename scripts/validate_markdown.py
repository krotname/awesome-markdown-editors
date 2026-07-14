from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from urllib.parse import unquote, urlsplit


def _markdown_files(root: Path) -> list[Path]:
    return sorted(
        path
        for path in root.rglob("*.md")
        if ".git" not in path.relative_to(root).parts
    )


def _without_html_comments(text: str) -> str:
    def preserve_lines(match: re.Match[str]) -> str:
        return "\n" * match.group(0).count("\n")

    return re.sub(r"<!--.*?-->", preserve_lines, text, flags=re.DOTALL)


def _github_slug(text: str) -> str:
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"[*_~]", "", text)
    text = re.sub(r"[^\w\s-]", "", text, flags=re.UNICODE)
    return re.sub(r"\s", "-", text.strip().lower())


def _anchors(text: str) -> set[str]:
    result: set[str] = set()
    counts: dict[str, int] = {}
    for line in text.splitlines():
        heading = re.match(r"^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$", line)
        if heading:
            base = _github_slug(heading.group(1))
            suffix = counts.get(base, 0)
            result.add(base if suffix == 0 else f"{base}-{suffix}")
            counts[base] = suffix + 1
        for explicit in re.finditer(
            r"<a\s+(?:name|id)=[\"']([^\"']+)[\"']", line, flags=re.IGNORECASE
        ):
            result.add(explicit.group(1))
    return result


def _destinations(line: str):
    position = 0
    while True:
        marker = line.find("](", position)
        if marker < 0:
            return
        depth = 1
        escaped = False
        closing = None
        for index in range(marker + 2, len(line)):
            character = line[index]
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == "(":
                depth += 1
            elif character == ")":
                depth -= 1
                if depth == 0:
                    closing = index
                    break
        if closing is None:
            yield marker, None
            return
        yield marker, line[marker + 2 : closing].strip()
        position = closing + 1


def _href(destination: str) -> str:
    if destination.startswith("<") and destination.endswith(">"):
        return destination[1:-1]
    return destination.split(maxsplit=1)[0] if destination else ""


def _paragraph_parenthesis_issues(text: str):
    visible = _without_html_comments(text)
    paragraph: list[str] = []
    start_line = 1
    in_fence = False

    def inspect():
        if not paragraph:
            return None
        block = "\n".join(paragraph)
        block = re.sub(r"\x60+.*?\x60+", "", block, flags=re.DOTALL)
        block = block.replace(r"\(", "").replace(r"\)", "")
        delta = block.count("(") - block.count(")")
        unmatched_strong = len(re.findall(r"(?<!\\)\*\*", block)) % 2
        if delta or unmatched_strong:
            return start_line, delta, unmatched_strong
        return None

    for line_number, line in enumerate(visible.splitlines() + [""], 1):
        if re.match(r"^\s*(?:\x60{3}|~~~)", line):
            issue = inspect()
            if issue:
                yield issue
            paragraph.clear()
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        if line.strip():
            if not paragraph:
                start_line = line_number
            paragraph.append(line)
            continue
        issue = inspect()
        if issue:
            yield issue
        paragraph.clear()


def validate(root: Path) -> list[str]:
    root = root.resolve()
    errors: list[str] = []
    texts: dict[Path, str] = {}

    for path in _markdown_files(root):
        relative = path.relative_to(root)
        try:
            texts[path.resolve()] = path.read_bytes().decode("utf-8", "strict")
        except UnicodeDecodeError as error:
            errors.append(f"{relative}: invalid UTF-8 at byte {error.start}")

    anchors = {path: _anchors(text) for path, text in texts.items()}

    for path, text in texts.items():
        relative = path.relative_to(root)
        for line_number, line in enumerate(text.splitlines(), 1):
            for _, destination in _destinations(line):
                if destination is None:
                    errors.append(f"{relative}:{line_number}: unterminated inline link")
                    continue
                if not destination:
                    errors.append(f"{relative}:{line_number}: empty inline link")
                    continue
                if destination.startswith("[") or "](" in destination:
                    errors.append(f"{relative}:{line_number}: nested or malformed link")
                    continue
                href = _href(destination)
                parsed = urlsplit(href)
                if parsed.scheme or href.startswith(("//", "/", "mailto:")):
                    continue
                target_text, _, fragment = href.partition("#")
                target = (
                    (path.parent / unquote(target_text)).resolve()
                    if target_text
                    else path
                )
                try:
                    target.relative_to(root)
                except ValueError:
                    errors.append(f"{relative}:{line_number}: link escapes repository: {href}")
                    continue
                if target_text and not target.exists():
                    errors.append(f"{relative}:{line_number}: missing linked file: {href}")
                    continue
                if fragment and target.suffix.lower() == ".md":
                    decoded_fragment = unquote(fragment)
                    if decoded_fragment not in anchors.get(target, set()):
                        errors.append(
                            f"{relative}:{line_number}: missing Markdown anchor: {href}"
                        )

        for line_number, delta, unmatched_strong in _paragraph_parenthesis_issues(text):
            if delta:
                errors.append(
                    f"{relative}:{line_number}: unbalanced parentheses in paragraph "
                    f"(delta {delta:+d})"
                )
            if unmatched_strong:
                errors.append(
                    f"{relative}:{line_number}: unbalanced strong-emphasis markers"
                )

    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", nargs="?", default=".")
    args = parser.parse_args()
    errors = validate(Path(args.root))
    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 1
    print("Markdown validation: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
