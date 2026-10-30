# Share of each weather condition per station (MRJob on Hadoop, order inversion)

## Concept

A share needs a total and a count at the same time, but a reducer sees its keys one  
after another and must not keep a whole station's counts in memory. Order inversion  
makes the total arrive first, so every share can be computed as its count streams by.  
How it works (general MapReduce knowledge; a course exercise names its slides here):  
the mapper emits, besides each (station, condition) count, a contribution to the  
station's total under a special key that sorts before all of that station's pair keys.  
Two things must hold for it to work: the special key and the pair keys of one station  
must reach the same reducer (a partitioner on the station alone), and the sort order must  
put the special key first. The reducer then remembers only the current station's total  
and divides each pair's count by it. A combiner can add up counts and totals early,  
since sums can be combined in any order.

## Task

Write an MRJob job that outputs, for every station, the (station, condition) pairs whose  
share is at least tau:  
share(station, condition) = count(station, condition) / count(station)

## Input

Every file in the input directory, no header line. Each line is  
station,date,condition            e.g.  s1,2024-01-05,rain

Skip lines that do not have exactly 3 fields, or have an empty station or condition.  
Fields may have spaces around them: " s1 , 2024-01-05 , rain " counts for station "s1".  
Station names contain only letters and digits, and condition names only lowercase letters  
a-z (like the course projects, the input's character set is part of the specification).

## Parameter

tau, passed as --jobconf myjob.settings.tau=0.25 and read with  
from mrjob.compat import jobconf_from_env  
tau = float(jobconf_from_env("myjob.settings.tau"))

## Output

One line per pair with share >= tau (a share equal to tau is included): key  
"station,condition" and the share as a float, unrounded. MRJob writes them as JSON:  
"s1,rain"   0.5

Within each output file, lines must be sorted by key (Hadoop's sort does this for you).

## Example

```text
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
```

s1 has 8 observations: rain 4/8, sun 2/8, fog and hail 1/8 each, below tau.

## Constraints

As in the course projects:
- Order inversion: compute a station's total and its pair counts in one MRStep, and do not  
keep a station's counts in a list or dict in the reducer.
- Use a combiner (or in-mapper combining).
- It must work with several reducers: the tests run it on Hadoop with 1, 2 and 4 reducers,  
so every key of a station must reach the same reducer.

## Slides

None: a synthetic example, not from a course.

## Run

Quickly, as map | sort | reduce with one reducer (`-r inline` would split a station over  
two reducers, since it ignores the partitioner):
```text
export myjob_settings_tau=0.25
cat sample_input/* | python student.py --mapper | LC_ALL=C sort | python student.py --reducer
pytest test_student.py      (Hadoop, about 25 s)
```

Stuck? hints.md has useful functions and three levels of hints.

## How to run

With the repository's virtual environment active:

```bash
cd "examples/station-condition-share/exercise"
pytest test_student.py      # the tests
python student.py           # the program, with the arguments Run shows, if any
```

Stuck? `hints.md` in this folder has three levels of hints, each more specific; or, from the repository's folder: `python -m tutor.hint "examples/station-condition-share/exercise"`
