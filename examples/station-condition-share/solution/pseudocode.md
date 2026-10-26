# station-condition-share in exam pseudocode

Order inversion (Lin & Dyer style). The special key `(s, *)` carries the
marginal; the partitioner sends all keys of station `s` to one reducer, and the sort order
puts `(s, *)` before `(s, c)`.

```
class Mapper
    method Map(offset o, line l)
        (s, d, c) ← Parse(l)                      ▷ skip malformed lines
        Emit(pair (s, *), count 1)
        Emit(pair (s, c), count 1)

class Combiner
    method Combine(pair p, counts [n1, n2, ...])
        Emit(pair p, count Sum([n1, n2, ...]))

class Partitioner
    method GetPartition(pair (s, x), count n, reducers R)
        return Hash(s) mod R                      ▷ by station only

class Reducer
    method Initialize
        τ ← jobconf "myjob.settings.tau"
        total ← 0
    method Reduce(pair (s, x), counts [n1, n2, ...])
        n ← Sum([n1, n2, ...])
        if x = * then
            total ← n                             ▷ arrives first for each station
        else
            share ← n / total
            if share ≥ τ then
                Emit(pair (s, x), share)
```
