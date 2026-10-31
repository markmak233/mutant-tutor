# Share of each weather condition per station (MRJob on Hadoop, order inversion)

> learn mode · implementation · medium

**Contents:** [Concept](#concept) · [Task](#task) · [Input](#input) · [Output](#output) · [Example](#example) · [Constraints](#constraints) · [Slides](#slides) · [How to run](#how-to-run)

---

## Concept

### Why it is needed

A share needs a total and a count at the same time, but a reducer sees its keys one after
another and must not keep a whole station's counts in memory. Order inversion makes the
total arrive first, so every share can be computed as its count streams by.

### Order inversion (general MapReduce knowledge; a course exercise names its slides here)

The mapper emits, besides each pair's count, a contribution to the total under a special
key that sorts before all the pair keys of the same group:

```text
map(record):
    emit (group, *)     -> 1        the total's contribution
    emit (group, item)  -> 1        the pair's count
reduce((group, *), counts):         total = sum(counts)
reduce((group, item), counts):      emit (group, item) -> sum(counts) / total
```

Two things must hold for it to work:

| Condition | Why |
|---|---|
| a partitioner on the group alone | a group's special key and pair keys reach one reducer |
| the special key sorts first | the total must be known before the first pair arrives |

The reducer then remembers only the current group's total. A combiner can add up counts
and totals early, since sums can be combined in any order.

---

## Task

Write an MRJob job that outputs, for every station, the (station, condition) pairs whose
share is at least $\tau$:

$$
\text{share}(s, c) = \frac{\text{count}(s, c)}{\text{count}(s)}
$$

## Input

| Argument | Meaning |
|---|---|
| input directory | every file in it, no header line |
| `tau` | the threshold $\tau$, passed as `--jobconf myjob.settings.tau=0.25` |

Read `tau` in the job with

```python
from mrjob.compat import jobconf_from_env
tau = float(jobconf_from_env("myjob.settings.tau"))
```

Each line is

```text
station,date,condition            e.g.  s1,2024-01-05,rain
```

Skip lines that do not have exactly 3 fields, or have an empty station or condition.
Fields may have spaces around them: ` s1 , 2024-01-05 , rain ` counts for station `s1`.
Station names contain only letters and digits, and condition names only lowercase letters
a-z.

## Output

One line per pair with share $\ge \tau$ (a share equal to $\tau$ is included): key
`"station,condition"` and the share as a float, unrounded. MRJob writes them as JSON:

```text
"s1,rain"   0.5
```

Within each output file, lines are sorted by key (Hadoop's sort does this for you).

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

| Station | Observations | Shares | Kept ($\tau = 0.25$) |
|---|---|---|---|
| `s1` | 8 | rain $\frac{4}{8}$, sun $\frac{2}{8}$, fog and hail $\frac{1}{8}$ each | rain, sun |
| `s2` | 3 | sun $\frac{2}{3}$, rain $\frac{1}{3}$ | both |

## Constraints

1. **Order inversion**: compute a station's total and its pair counts in one `MRStep`,
   and do not keep a station's counts in a list or dict in the reducer.
2. **A combiner** (or in-mapper combining).
3. **Several reducers**: the tests run it on Hadoop with 1, 2 and 4 reducers, so every key
   of a station must reach the same reducer.

## Slides

None: a synthetic example, not from a course.

> **Assumption:** the input's character set (letters and digits in station names,
> lowercase a-z in conditions) is part of the specification, as in course projects.

---

## How to run

With the repository's virtual environment active:

```bash
cd "examples/station-condition-share/exercise"
pytest test_student.py      # the tests
export myjob_settings_tau=0.25
cat sample_input/* | python student.py --mapper | LC_ALL=C sort | python student.py --reducer
```

Quickly, as map | sort | reduce with one reducer (`-r inline` would split a station over two reducers, since it ignores the partitioner); `pytest test_student.py` runs it on Hadoop, about 25 s.

> **Stuck?** `hints.md` in this folder has three levels of hints, each more specific; or ask
> for a hint from the repository's folder:
>
> ```bash
> python -m tutor.hint "examples/station-condition-share/exercise"
> ```
