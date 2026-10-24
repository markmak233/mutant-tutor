"""Tests for ConditionShare.

Each test writes input files into a temp dir and runs the job on the local Hadoop, where
partitioner, sort order and reducer count behave as on a cluster (the inline runner ignores
them). TUTOR_PARTITIONS sets both the number of input files and of reducers, so the
validator can rerun with 1, 2 and 4. Each Hadoop job takes about 7 s, so the tests are few
and each one carries a lot of data.

Output is compared per output file: every file's lines must keep the key order, MRJob's
JSON quotes are optional, and shares may differ by tiny float errors.
"""

from tutor.testkit import assert_output, run_mrjob, write_inputs

from student import ConditionShare


def run(tmp_path, lines, tau):
    input_dir = write_inputs(tmp_path / "input", lines, suffix=".csv")
    return run_mrjob(ConditionShare, input_dir, tmp_path / "output", runner="hadoop",
                     jobconf={"myjob.settings.tau": tau})


def check(result, expected):
    assert_output(result, expected, order="per_file", float_tol=1e-9, optional_quotes=True)


def shares(lines, tau):
    """The expected lines, sorted by key, for generated data."""
    totals, counts = {}, {}
    for line in lines:
        fields = [f.strip() for f in line.split(",")]
        if len(fields) == 3 and fields[0] and fields[2]:
            totals[fields[0]] = totals.get(fields[0], 0) + 1
            pair = (fields[0], fields[2])
            counts[pair] = counts.get(pair, 0) + 1
    rows = sorted((f"{s},{c}", n / totals[s]) for (s, c), n in counts.items())
    return [f"{key}\t{share}" for key, share in rows if share >= tau]


def test_example_from_question(tmp_path):
    """Includes shares exactly equal to tau (s1,sun = 2/8 = 0.25), which must be output."""
    lines = [
        "s1,2024-01-01,rain", "s1,2024-01-02,rain", "s1,2024-01-03,sun", "s1,2024-01-04,fog",
        " s1 , 2024-01-05 , rain ", "s1,2024-01-06,rain", "s1,2024-01-07,sun",
        "s1,2024-01-08,hail", "s2,2024-01-01,sun", "s2,2024-01-02,sun", "s2,2024-01-03,rain",
        "s3,2024-01-01,", "not an observation",
    ]
    check(run(tmp_path, lines, 0.25), [
        "s1,rain\t0.5", "s1,sun\t0.25",
        "s2,rain\t0.3333333333333333", "s2,sun\t0.6666666666666666",
    ])


def test_many_stations_over_several_reducers(tmp_path):
    """Nine stations with observations in every input file, so each station's total and
    pairs must be brought together across files and land in the same reducer; tau = 0
    outputs every pair, which checks every count."""
    conditions = ["cloud", "fog", "rain", "sun"]
    lines = []
    for i in range(180):
        lines.append(f"st{i % 9},2024-02-{i % 28 + 1:02d},{conditions[(i * i + i // 9) % 4]}")
        if i % 15 == 0:
            lines += [f"st{i % 9},2024-02-01", ",2024-02-01,rain", f"st{i % 9},x,"]
    check(run(tmp_path, lines, 0.0), shares(lines, 0.0))


def test_high_tau_keeps_only_dominant_conditions(tmp_path):
    """tau = 0.6: only conditions with at least 60% of a station's observations. Station
    "dry" always has sun (share 1.0); station "gone" has only bad lines and never appears."""
    lines = (
        [f"dry,2024-03-{d:02d},sun" for d in range(1, 11)]
        + [f"mix,2024-03-{d:02d},{'rain' if d % 5 else 'sun'}" for d in range(1, 21)]
        + [f"even,2024-03-{d:02d},{'rain' if d % 2 else 'sun'}" for d in range(1, 21)]
        + ["gone,2024-03-01,", "gone,2024-03-02", " , 2024-03-03 , sun"]
    )
    check(run(tmp_path, lines, 0.6), ["dry,sun\t1.0", "mix,rain\t0.8"])
