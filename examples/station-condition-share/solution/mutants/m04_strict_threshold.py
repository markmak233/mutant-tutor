"""Mutant: share > tau, so a share exactly equal to tau is dropped."""

from mrjob.compat import jobconf_from_env
from mrjob.job import MRJob


class ConditionShare(MRJob):
    # SORT_VALUES switches on Hadoop's KeyFieldBasedPartitioner with -k1,1; the separator
    # makes field 1 the station, so a station's "*" total and its pairs share a reducer.
    SORT_VALUES = True
    JOBCONF = {"mapreduce.map.output.key.field.separator": ","}

    def mapper(self, _, line):
        fields = [field.strip() for field in line.split(",")]
        if len(fields) != 3 or not fields[0] or not fields[2]:
            return
        station, condition = fields[0], fields[2]
        # "*" sorts before every condition name, so the total reaches the reducer first.
        yield f"{station},*", 1
        yield f"{station},{condition}", 1

    def combiner(self, key, counts):
        yield key, sum(counts)

    def reducer_init(self):
        self.tau = float(jobconf_from_env("myjob.settings.tau"))
        self.station, self.total = None, 0

    def reducer(self, key, counts):
        station, condition = key.split(",", 1)
        count = sum(counts)
        if condition == "*":
            self.station, self.total = station, count
            return
        share = count / self.total
        if share > self.tau:
            yield key, share


if __name__ == "__main__":
    ConditionShare.run()
