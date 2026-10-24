"""Plain-Python specification of StationMonthly, for differential testing.

Takes the data lines of all input files (no headers) and min_readings, returns the expected
output lines in the required order.
"""


def expected_output(lines: list[str], min_readings: int) -> list[str]:
    sums: dict[tuple[str, str], float] = {}
    counts: dict[tuple[str, str], int] = {}
    for line in lines:
        fields = [field.strip() for field in line.split(",")]
        if len(fields) != 3 or not fields[0] or not fields[1]:
            continue
        try:
            temperature = float(fields[2])
        except ValueError:
            continue
        key = (fields[0], fields[1][:7])
        sums[key] = sums.get(key, 0.0) + temperature
        counts[key] = counts.get(key, 0) + 1
    rows = [
        (station, month, sums[station, month] / n)
        for (station, month), n in counts.items()
        if n >= min_readings
    ]
    rows.sort(key=lambda r: (r[0], -r[2], r[1]))
    return [f"{station},{month},{average}" for station, month, average in rows]
