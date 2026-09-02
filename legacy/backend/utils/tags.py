def parse_tags(tags_str: str | None) -> list[str]:
    if not tags_str:
        return []
    return [tag.strip() for tag in tags_str.split(",") if tag.strip()]


def top_tags_from_entries(entries, n: int = 5) -> list[tuple[str, int]]:
    counts: dict[str, int] = {}
    for entry in entries:
        if not entry.tags:
            continue
        for tag in parse_tags(entry.tags):
            counts[tag] = counts.get(tag, 0) + 1
    return sorted(counts.items(), key=lambda x: x[1], reverse=True)[:n]
