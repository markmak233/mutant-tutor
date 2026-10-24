"""Plain-Python specification of StationMax, for differential testing.

Takes the text lines of all input files and returns the expected output lines; order of
the returned lines does not matter.
"""


def expected_output(lines: list[str]) -> list[str]:
    maxima: dict[str, float] = {}
    for line in lines:
        fields = [field.strip() for field in line.split(",")]
        if len(fields) != 3 or not fields[0]:
            continue
        try:
            temperature = float(fields[2])
        except ValueError:
            continue
        station = fields[0]
        maxima[station] = max(maxima.get(station, temperature), temperature)
    return [f"{station}\t{value:.1f}" for station, value in maxima.items()]
