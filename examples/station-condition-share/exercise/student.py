"""Share of each weather condition per station (MRJob on Hadoop, order inversion)

Concept:
Order inversion: give a station's total a key that sorts before its (station, condition)
keys and reaches the same reducer, so the reducer knows the total before the pair counts
arrive and computes each share without storing them.

Task:
Write an MRJob job that outputs, for every station, the (station, condition) pairs whose
share is at least tau:
    share(station, condition) = count(station, condition) / count(station)

Input:
Every file in the input directory, no header line. Each line is
    station,date,condition            e.g.  s1,2024-01-05,rain
Skip lines that do not have exactly 3 fields, or have an empty station or condition.
Fields may have spaces around them: " s1 , 2024-01-05 , rain " counts for station "s1".
Station names contain only letters and digits, and condition names only lowercase letters
a-z (like the course projects, the input's character set is part of the specification).
Parameter: tau, passed as --jobconf myjob.settings.tau=0.25 and read with
    from mrjob.compat import jobconf_from_env
    tau = float(jobconf_from_env("myjob.settings.tau"))

Output:
One line per pair with share >= tau (a share equal to tau is included): key
"station,condition" and the share as a float, unrounded. MRJob writes them as JSON:
    "s1,rain"   0.5
Within each output file, lines must be sorted by key (Hadoop's sort does this for you).

Example:
    input (tau = 0.25)
        s1,2024-01-01,rain
        s1,2024-01-02,rain
        s1,2024-01-03,sun
        s1,2024-01-04,fog
         s1 , 2024-01-05 , rain
        s1,2024-01-06,rain
        s1,2024-01-07,sun
        s1,2024-01-08,hail
        s2,2024-01-01,sun
        s2,2024-01-02,sun
        s2,2024-01-03,rain
        s3,2024-01-01,
        not an observation
    output lines
        "s1,rain"   0.5
        "s1,sun"    0.25
        "s2,rain"   0.3333333333333333
        "s2,sun"    0.6666666666666666
s1 has 8 observations: rain 4/8, sun 2/8, fog and hail 1/8 each, below tau.

Constraints:
As in the course projects:
- Order inversion: compute a station's total and its pair counts in one MRStep, and do not
  keep a station's counts in a list or dict in the reducer.
- Use a combiner (or in-mapper combining).
- It must work with several reducers: the tests run it on Hadoop with 1, 2 and 4 reducers,
  so every key of a station must reach the same reducer.

Slides:
None: a synthetic example, not from a course.

Run:
Quickly, as map | sort | reduce with one reducer (`-r inline` would split a station over
two reducers, since it ignores the partitioner):
    export myjob_settings_tau=0.25
    cat sample_input/* | python student.py --mapper | LC_ALL=C sort | python student.py --reducer
    pytest test_student.py      (Hadoop, about 25 s)
Stuck? hints.md has useful functions and three levels of hints.
"""

from mrjob.compat import jobconf_from_env
from mrjob.job import MRJob


class ConditionShare(MRJob):
    def mapper(self, _, line):
        raise NotImplementedError

    def combiner(self, key, counts):
        raise NotImplementedError

    def reducer_init(self):
        raise NotImplementedError

    def reducer(self, key, counts):
        raise NotImplementedError


if __name__ == "__main__":
    ConditionShare.run()
