"""Existing top-down simplifiers and explicit replay queries; no learned model."""

import bisect
import math
import struct
from decimal import Decimal, localcontext

import numpy as np


def validate(times, positions):
    t = np.asarray(times, dtype=np.float64)
    p = np.asarray(positions, dtype=np.float64)
    if t.ndim != 1 or len(t) < 2 or p.shape != (len(t), 3):
        raise ValueError("At least two complete time/x/y/z observations are required")
    if not np.isfinite(t).all() or not np.isfinite(p).all():
        raise ValueError("Time and position must be finite")
    if (t < 0).any() or (np.diff(t) <= 0).any() or (t != np.floor(t)).any():
        raise ValueError(
            "Elapsed seconds must be nonnegative, integer and strictly increasing"
        )
    if np.max(t) > 2**53 - 1 or np.max(np.abs(p)) > 1e12:
        raise ValueError(
            "Input exceeds the exact integer-time or finite-computation bound"
        )
    return t.astype(np.int64), p


def configurations(plan):
    choices = [("identity", "identity", None)]
    for method, key in [
        ("stride", "uniform_stride"),
        ("geometric", "geometric_tolerance_m"),
        ("synchronous", "synchronous_tolerance_m"),
    ]:
        choices.extend(
            (f"{method}-{value}", method, value)
            for value in plan["configurations"][key]
        )
    return choices


def simplify(times, positions, method, value=None):
    t, p = validate(times, positions)
    n = len(t)
    if method == "identity":
        return list(range(n)), []
    if method == "stride":
        if value is None or value < 1 or int(value) != value:
            raise ValueError("Stride must be a positive integer")
        return sorted(set(range(0, n, int(value))) | {n - 1}), []
    if (
        method not in ["geometric", "synchronous"]
        or value is None
        or not math.isfinite(value)
        or value < 0
    ):
        raise ValueError("Unknown simplifier or invalid tolerance")
    retained, stack, witnesses = {0, n - 1}, [(0, n - 1)], []
    while stack:
        a, b = stack.pop()
        if b - a <= 1:
            continue
        if method == "synchronous":
            fraction = (t[a + 1 : b] - t[a]) / float(t[b] - t[a])
            chord = p[a] + fraction[:, None] * (p[b] - p[a])
        else:
            direction = p[b] - p[a]
            length2 = float(np.sum(direction * direction))
            if length2 == 0:
                chord = np.broadcast_to(p[a], p[a + 1 : b].shape)
            else:
                fraction = np.clip(
                    np.sum((p[a + 1 : b] - p[a]) * direction, axis=1) / length2, 0, 1
                )
                chord = p[a] + fraction[:, None] * direction
        distance2 = np.sum((p[a + 1 : b] - chord) ** 2, axis=1)
        k = int(np.argmax(distance2)) + a + 1
        maximum = float(distance2[k - a - 1])
        split = maximum > float(value) ** 2
        witnesses.append(
            {
                "start": a,
                "end": b,
                "max_index": k,
                "max_distance_squared_m2": maximum,
                "split": split,
            }
        )
        if split:
            retained.add(k)
            stack.extend([(k, b), (a, k)])
    return sorted(retained), witnesses


def encode(times, positions, indices):
    t, p = validate(times, positions)
    if (
        len(indices) < 2
        or indices != sorted(set(indices))
        or indices[0] != 0
        or indices[-1] != len(t) - 1
    ):
        raise ValueError(
            "Retained indices must be ordered, unique and include both endpoints"
        )
    body = bytearray(struct.pack("<Q", len(indices)))
    for index in indices:
        body.extend(struct.pack("<qddd", int(t[index]), *map(float, p[index])))
    return bytes(body)


def unpack(payload):
    if len(payload) < 8:
        raise ValueError("Missing point-count header")
    count = struct.unpack_from("<Q", payload)[0]
    if count < 2 or len(payload) != 8 + 32 * count:
        raise ValueError("Encoded count and byte length disagree")
    records = list(struct.iter_unpack("<qddd", payload[8:]))
    return validate([r[0] for r in records], [r[1:] for r in records])


def reconstruct(full_times, retained_times, retained_positions):
    t, p = validate(retained_times, retained_positions)
    raw_full = np.asarray(full_times, dtype=np.float64)
    if (
        not np.isfinite(raw_full).all()
        or (raw_full != np.floor(raw_full)).any()
        or (raw_full < 0).any()
        or (raw_full > 2**53 - 1).any()
    ):
        raise ValueError(
            "Reconstruction grid must have exact nonnegative integer times"
        )
    full = raw_full.astype(np.int64)
    if (
        full.ndim != 1
        or len(full) < 2
        or (np.diff(full) <= 0).any()
        or full[0] != t[0]
        or full[-1] != t[-1]
    ):
        raise ValueError(
            "Reconstruction requires an ordered grid bounded by retained endpoints"
        )
    return np.column_stack([np.interp(full, t, p[:, axis]) for axis in range(3)])


def decimal_position(time, retained_times, retained_positions):
    index = min(
        bisect.bisect_right(retained_times, int(time)) - 1, len(retained_times) - 2
    )
    start, end = int(retained_times[index]), int(retained_times[index + 1])
    fraction = Decimal(int(time) - start) / Decimal(end - start)
    return [
        Decimal.from_float(float(a))
        + fraction * (Decimal.from_float(float(b)) - Decimal.from_float(float(a)))
        for a, b in zip(retained_positions[index], retained_positions[index + 1])
    ]


def zone_membership(full_times, retained_times, retained_positions, decoded, radius):
    squared = np.sum(decoded[:, :2] ** 2, axis=1)
    mask = squared <= radius**2
    near = np.flatnonzero(np.abs(squared - radius**2) <= 2 * radius * 1e-8 + 1e-16)
    with localcontext() as context:
        context.prec = 80
        for index in near:
            xyz = decimal_position(
                full_times[index], retained_times, retained_positions
            )
            mask[index] = xyz[0] ** 2 + xyz[1] ** 2 <= Decimal(radius) ** 2
    return mask, len(near)


def first_transition(times, membership):
    changes = np.flatnonzero(membership != membership[0])
    return int(times[changes[0]] - times[0]) if len(changes) else None


def horizontal_length(points):
    delta = np.diff(points[:, :2], axis=0)
    return float(np.sum(np.sqrt(np.sum(delta * delta, axis=1))))


def summarize_run(times, positions, indices, payload, guards, zones):
    t, p = validate(times, positions)
    rt, rp = unpack(payload)
    decoded = reconstruct(t, rt, rp)
    if not np.array_equal(rt, t[indices]) or not np.array_equal(rp, p[indices]):
        raise ValueError("Encoded records do not match retained source observations")
    errors = decoded - p
    distance2 = np.sum(errors * errors, axis=1)
    distances = np.sqrt(distance2)
    mse = float(np.mean(distance2))
    maximum = float(np.max(distances))
    position_pass = maximum <= guards["maximum_synchronous_error_m"]
    if abs(maximum - guards["maximum_synchronous_error_m"]) <= 1e-8:
        with localcontext() as context:
            context.prec = 80
            limit = Decimal(guards["maximum_synchronous_error_m"]) ** 2
            position_pass = all(
                sum(
                    (a - Decimal.from_float(float(b))) ** 2
                    for a, b in zip(decimal_position(time, rt, rp), point)
                )
                <= limit
                for time, point in zip(t, p)
            )
    original_length, decoded_length = horizontal_length(p), horizontal_length(decoded)
    signed_length_error = decoded_length - original_length
    relative = signed_length_error / original_length if original_length > 0 else None
    route_pass = (
        abs(relative) <= guards["maximum_route_relative_error"]
        if relative is not None
        else decoded_length <= 1e-6
    )
    route_boundary_check = False
    if (
        relative is not None
        and abs(abs(relative) - guards["maximum_route_relative_error"]) <= 1e-10
    ):
        with localcontext() as context:
            context.prec = 80
            source_decimal = [
                [Decimal.from_float(float(v)) for v in point] for point in p
            ]
            decoded_decimal = [decimal_position(time, rt, rp) for time in t]

            def length(rows):
                return sum(
                    (
                        ((b[0] - a[0]) ** 2 + (b[1] - a[1]) ** 2).sqrt()
                        for a, b in zip(rows, rows[1:])
                    ),
                    Decimal(0),
                )

            raw, compressed = length(source_decimal), length(decoded_decimal)
            route_pass = (
                abs(compressed - raw)
                <= Decimal(str(guards["maximum_route_relative_error"])) * raw
            )
            route_boundary_check = True
    row = {
        "input_points": len(t),
        "retained_points": len(indices),
        "input_bytes": 8 + 32 * len(t),
        "encoded_bytes": len(payload),
        "byte_ratio": len(payload) / (8 + 32 * len(t)),
        "point_mae_m": float(np.mean(distances)),
        "point_mse_m2": mse,
        "point_rmse_m": math.sqrt(mse),
        "max_synchronous_error_m": maximum,
        **{
            f"bias_{axis}_m": float(np.mean(errors[:, number]))
            for number, axis in enumerate("xyz")
        },
        "route_reference_m": original_length,
        "route_decoded_m": decoded_length,
        "route_signed_error_m": signed_length_error,
        "route_absolute_error_m": abs(signed_length_error),
        "route_relative_error": relative,
        "route_absolute_relative_error": (
            abs(relative) if relative is not None else None
        ),
        "position_pass": bool(position_pass),
        "route_pass": bool(route_pass),
        "route_boundary_decimal_check": route_boundary_check,
    }
    all_pass = [position_pass, route_pass]
    for radius in zones:
        reference_mask, ref_near = zone_membership(t, t, p, p, radius)
        decoded_mask, dec_near = zone_membership(t, rt, rp, decoded, radius)
        reference_seconds = int(np.sum(np.diff(t) * reference_mask[:-1]))
        decoded_seconds = int(np.sum(np.diff(t) * decoded_mask[:-1]))
        reference_first = first_transition(t, reference_mask)
        decoded_first = first_transition(t, decoded_mask)
        state_match = (reference_first is None) == (decoded_first is None)
        transition_error = (
            decoded_first - reference_first
            if reference_first is not None and decoded_first is not None
            else None
        )
        occupancy_pass = (
            abs(decoded_seconds - reference_seconds)
            <= guards["maximum_zone_occupancy_error_seconds"]
        )
        transition_pass = state_match and (
            transition_error is None
            or abs(transition_error) <= guards["maximum_first_transition_error_seconds"]
        )
        prefix = f"zone{radius}_"
        row.update(
            {
                prefix + "reference_occupancy_s": reference_seconds,
                prefix + "decoded_occupancy_s": decoded_seconds,
                prefix
                + "occupancy_signed_error_s": decoded_seconds
                - reference_seconds,
                prefix
                + "occupancy_absolute_error_s": abs(
                    decoded_seconds - reference_seconds
                ),
                prefix + "reference_first_transition_s": reference_first,
                prefix + "decoded_first_transition_s": decoded_first,
                prefix + "first_transition_signed_error_s": transition_error,
                prefix + "transition_state_match": state_match,
                prefix
                + "first_transition_absolute_error_s": (
                    abs(transition_error) if transition_error is not None else None
                ),
                prefix + "occupancy_pass": occupancy_pass,
                prefix + "transition_pass": transition_pass,
                prefix + "decimal_boundary_points": ref_near + dec_near,
            }
        )
        all_pass.extend([occupancy_pass, transition_pass])
    row["admissible"] = bool(all(all_pass))
    return row
