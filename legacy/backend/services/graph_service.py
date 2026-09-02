from typing import Optional
from sqlalchemy.orm import Session
from .. import models
from ..auth import check_visibility
from ..utils.tags import parse_tags


def get_graph_data(
    db: Session,
    user: Optional[models.User] = None,
) -> dict:

    entries = db.query(models.Entry).all()

    entity_map: dict[int, list[int]] = {}
    for ee in db.query(models.EntryEntity).all():
        if ee.entry_id not in entity_map:
            entity_map[ee.entry_id] = []
        entity_map[ee.entry_id].append(ee.entity_id)

    nodes = []
    entry_ids_in_graph = set()

    for e in entries:
        if not check_visibility(e.visibility, user, e.user_id):
            continue
        label = (e.title or f"Entry {e.id}")[:30]
        snippet = (e.content or "")[:80]
        nodes.append({
            "id": e.id,
            "label": label,
            "title": snippet,
            "group": e.entry_type or "Journal",
        })
        entry_ids_in_graph.add(e.id)

    edges = []
    seen: set[tuple[int, int]] = set()

    entry_list = [e for e in entries if e.id in entry_ids_in_graph]

    for i, e1 in enumerate(entry_list):
        tags1 = set(parse_tags(e1.tags))
        ents1 = set(entity_map.get(e1.id, []))
        for j, e2 in enumerate(entry_list):
            if i >= j:
                continue
            key = (e1.id, e2.id)
            if key in seen:
                continue

            weight = 0
            shared_tags = tags1 & set(parse_tags(e2.tags))
            if shared_tags:
                weight += len(shared_tags)

            shared_ents = ents1 & set(entity_map.get(e2.id, []))
            if shared_ents:
                weight += len(shared_ents)

            if weight > 0:
                seen.add(key)
                edges.append({
                    "from": e1.id,
                    "to": e2.id,
                    "value": weight,
                    "title": ", ".join(shared_tags | set()),
                })

    return {"nodes": nodes, "edges": edges}
