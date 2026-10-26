# Hints

## Useful functions

The examples use different data from the exercise, so they show the tool, not the answer.

### `jobconf_from_env(name)` — read a parameter passed with `--jobconf`
```python
from mrjob.compat import jobconf_from_env
from mrjob.job import MRJob

class LongWords(MRJob):
    def mapper_init(self):                        # runs once per map task, before mapper
        self.min_len = int(jobconf_from_env("myjob.settings.minlen"))

    def mapper(self, _, line):
        for word in line.split():
            if len(word) >= self.min_len:
                yield word, 1

    def combiner(self, word, counts):
        yield word, sum(counts)

    def reducer(self, word, counts):
        yield word, sum(counts)

if __name__ == "__main__":
    LongWords.run()
```
```
$ python long_words.py -r inline words.txt --jobconf myjob.settings.minlen=5
"river"	2
"forest"	1
```
The value arrives as a string: convert it (`int`, `float`). `*_init` methods
(`mapper_init`, `reducer_init`) are the place to read it once.

### Combiner — a reducer that runs on one map task's output
It has the reducer's signature, and what it yields must look like mapper output, because
Hadoop may run it zero, one or several times before the real reducer. Summing counts is
safe; anything that changes the meaning of a value (an average, a share) is not.

### Sort order of keys — `"*"` comes first
```python
sorted(["2024-01,feb", "2024-01,*", "2024-01,apr", "2024-01,~"])
# ['2024-01,*', '2024-01,apr', '2024-01,feb', '2024-01,~']
ord("*"), ord("0"), ord("a"), ord("~")    # (42, 48, 97, 126)
```
Within one reducer, keys arrive in this text order, so a key ending in `*` arrives before
every other key with the same prefix, and one ending in `~` after them.

### Remembering something across keys in a reducer
```python
class Running(MRJob):
    def reducer_init(self):              # once per reducer task
        self.previous = None

    def reducer(self, key, values):
        ...                              # self.previous still holds what the last key set
        self.previous = key
```
Instance variables survive from one key to the next within the same reducer task.

### Choosing the reducer by part of the key (Hadoop only)
With several reducers, Hadoop picks the reducer from a hash of the whole key, so
`"2024-01,apr"` and `"2024-01,*"` can end up in different reducers. To partition by the
first comma-separated field only:
```python
class PerMonth(MRJob):
    SORT_VALUES = True     # turns on Hadoop's KeyFieldBasedPartitioner, by field 1 (-k1,1)
    JOBCONF = {"mapreduce.map.output.key.field.separator": ","}    # fields split at ","
```
Now every key starting with `2024-01,` goes to the same reducer, still sorted. The inline
runner ignores all of this, which is why the tests use Hadoop.

## Level 1
Give each station two kinds of keys: one that carries the station's total number of
observations, and one per condition. If the total reaches the reducer first, the reducer
can divide each condition count by it as the keys go by, without storing anything else.

## Level 2
The mapper emits `("s1,*", 1)` and `("s1,rain", 1)` for every valid line; the combiner sums.
In the reducer, a key whose condition is `*` sets the current station's total (keep it in
`self`); any other key divides its count by that total. Then make all keys of a station go
to the same reducer: partition by the part before the comma.

## Level 3
`SORT_VALUES = True` and `JOBCONF = {"mapreduce.map.output.key.field.separator": ","}`.
`reducer_init` reads tau with `float(jobconf_from_env("myjob.settings.tau"))` and sets the
total to 0. In `reducer`: `station, condition = key.split(",", 1)`, `count = sum(counts)`;
if `condition == "*"` store `count` as the total, else compute `count / total` and yield
`key, share` when it is `>= tau`.
