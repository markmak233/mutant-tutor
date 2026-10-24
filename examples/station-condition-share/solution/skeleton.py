"""Pristine skeleton of station-condition-share (the code part of exercise/student.py)."""

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
