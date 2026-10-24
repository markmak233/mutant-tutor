"""Mutant: the combiner emits 1 per key instead of the partial count, losing counts
whenever a map task sees a key more than once."""

from mrjob.compat import jobconf_from_env
from mrjob.job import MRJob


class ConditionShare(MRJob):
    # Partitioning: SORT_VALUES switches on Hadoop's KeyFieldBasedPartitioner with -k1,1,
    # and the separator makes "field 1" the part of the key before the comma, the station.
    # So "s1,*" and "s1,rain" always reach the same reducer.
    SORT_VALUES = True
    JOBCONF = {"mapreduce.map.output.key.field.separator": ","}

    # Step 1 (mapper): for every valid line, emit one count for the station's total and one
    # for the (station, condition) pair.
    #   "s1,2024-01-01,rain"  ->  ("s1,*", 1), ("s1,rain", 1)
    def mapper(self, _, line):
        fields = [field.strip() for field in line.split(",")]
        if len(fields) != 3 or not fields[0] or not fields[2]:
            return
        station, condition = fields[0], fields[2]
        yield f"{station},*", 1
        yield f"{station},{condition}", 1

    # Step 2 (combiner): add up the counts of a key within one map task. Summing is safe
    # whether Hadoop runs the combiner zero, one or many times.
    #   ("s1,rain", [1, 1, 1])  ->  ("s1,rain", 3)
    def combiner(self, key, counts):
        yield key, 1

    # Step 3 (reducer_init): once per reducer task, read tau and start with no station.
    def reducer_init(self):
        self.tau = float(jobconf_from_env("myjob.settings.tau"))
        self.station, self.total = None, 0

    # Step 4 (reducer): keys arrive sorted, and "*" sorts before every condition name, so a
    # station's total comes first (order inversion):
    #   "s1,*" 8  ->  remember total = 8 for s1, output nothing
    #   "s1,fog" 1  ->  1 / 8 = 0.125 < tau, output nothing
    #   "s1,rain" 4  ->  4 / 8 = 0.5 >= tau, output ("s1,rain", 0.5)
    # Only the current station's total is kept, never a list of a station's counts.
    def reducer(self, key, counts):
        station, condition = key.split(",", 1)
        count = sum(counts)
        if condition == "*":
            self.station, self.total = station, count
            return
        share = count / self.total
        if share >= self.tau:
            yield key, share


if __name__ == "__main__":
    ConditionShare.run()
