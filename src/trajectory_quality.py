"""Qualify one scene/aircraft track without filling gaps or conflicting frames."""


def qualify_track(rows, minimum_points, maximum_points):
    stats = {"out_of_order_transitions": 0, "duplicate_frame_rows": 0, "gaps_split": 0}
    if any(row["error"] for row in rows):
        return {
            "reason": "invalid_frame_or_coordinate",
            "runs": [],
            "excluded_lengths": [],
            "stats": stats,
        }
    frames = [row["frame"] for row in rows]
    stats["out_of_order_transitions"] = sum(b < a for a, b in zip(frames, frames[1:]))
    unique, conflict = {}, False
    for row in rows:
        previous = unique.get(row["frame"])
        if previous is None:
            unique[row["frame"]] = row
        else:
            stats["duplicate_frame_rows"] += 1
            conflict |= previous["xyz_km"] != row["xyz_km"]
    if conflict:
        return {
            "reason": "conflicting_duplicate_frame",
            "runs": [],
            "excluded_lengths": [],
            "stats": stats,
        }
    ordered = [unique[frame] for frame in sorted(unique)]
    chunks, start = [], 0
    for index in range(1, len(ordered)):
        if ordered[index]["frame"] - ordered[index - 1]["frame"] != 1:
            chunks.append(ordered[start:index])
            start = index
            stats["gaps_split"] += 1
    if ordered:
        chunks.append(ordered[start:])
    return {
        "reason": None,
        "stats": stats,
        "runs": [
            chunk for chunk in chunks if minimum_points <= len(chunk) <= maximum_points
        ],
        "excluded_lengths": [
            len(chunk)
            for chunk in chunks
            if not minimum_points <= len(chunk) <= maximum_points
        ],
    }
