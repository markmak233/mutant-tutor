# Hints

## Useful functions

The examples use different data from the exercise, so they show the tool, not the answer.
`rdd.collect()` is only used here to display results. After a `...ByKey` step the order
of the pairs may differ from what is shown.

### `mapValues(f)` — change each value, keep the key
```python
rdd = sc.parallelize([("a", 1), ("b", 2), ("a", 3)])
rdd.mapValues(lambda v: v * 10).collect()
# [("a", 10), ("b", 20), ("a", 30)]
```
Values can become tuples too: `rdd.mapValues(lambda v: (v, "x"))` gives `("a", (1, "x"))`, ...

### `reduceByKey(f)` — combine all values of a key, two at a time
```python
rdd = sc.parallelize([("a", 1), ("b", 2), ("a", 3)])
rdd.reduceByKey(lambda x, y: max(x, y)).collect()
# [("a", 3), ("b", 2)]
```
Spark first combines inside each partition, then combines the partial results, in any
order. So `f` must give the same answer however the values are grouped (like `+` or `max`).

### `aggregateByKey(zero, seq_op, comb_op)` — like reduceByKey, but the result type can differ
```python
words = sc.parallelize([("a", "hi"), ("b", "yo"), ("a", "hey")])
words.aggregateByKey(
    0,                                    # starting value per key, per partition
    lambda acc, word: acc + len(word),    # add one value into a partition's result
    lambda acc1, acc2: acc1 + acc2,       # merge results from two partitions
).collect()
# [("a", 5), ("b", 2)]   total characters per key
```

### Not allowed here: `groupByKey()`
```python
rdd.groupByKey().mapValues(list).collect()
# [("a", [1, 3]), ("b", [2])]
```
It sends every single value across the network before anything is combined.

## Level 1
An average cannot be built from two smaller averages. What *can* be combined from
two partial results, in any order?

## Level 2
Carry a `(sum, count)` pair per station. Two pairs combine by adding them component-wise,
which works no matter how the readings are split across partitions.

## Level 3
`mapValues` each temperature to `(t, 1)`, combine pairs with `reduceByKey`, then
`mapValues` the final pair to `sum / count`.
