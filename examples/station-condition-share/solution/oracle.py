"""Plain-Python specification of ConditionShare, for differential testing.

Takes the text lines of all input files and tau, returns the expected output lines without
JSON quotes ("s1,rain<TAB>0.5"), sorted by key: the order each reducer's output must keep.
"""


def expected_output(lines: list[str], tau: float) -> list[str]:
    totals: dict[str, int] = {}
    counts: dict[tuple[str, str], int] = {}
    for line in lines:
        fields = [field.strip() for field in line.split(",")]
        if len(fields) != 3 or not fields[0] or not fields[2]:
            continue
        station, condition = fields[0], fields[2]
        totals[station] = totals.get(station, 0) + 1
        counts[station, condition] = counts.get((station, condition), 0) + 1
    rows = []
    for (station, condition), count in counts.items():
        share = count / totals[station]
        if share >= tau:
            rows.append((f"{station},{condition}", share))
    return [f"{key}\t{share}" for key, share in sorted(rows)]
