# Hints

## Useful functions

The examples use different data from the exercise, so they show the tool, not the answer.

### `sc.textFile(path)` — one element per line, from a file or a whole directory
```python
lines = sc.textFile("input_dir")      # every file in the directory
lines.take(2)
# ['station,date,temperature', 's1,2024-01-01,12.5']
```
Each file gives at least one partition, so a directory of several files is already split.

### Parsing a line safely (plain Python)
```python
def parse_price(line):
    fields = [f.strip() for f in line.split(";")]
    if len(fields) != 2:
        return None
    try:
        return fields[0], float(fields[1])
    except ValueError:              # float("abc") raises ValueError
        return None

parse_price(" pen ; 1.5 ")   # ('pen', 1.5)
parse_price("pen;cheap")     # None
parse_price("")              # None
```
Return `None` for a bad line, then drop the `None`s with `filter`.

### `filter(f)` — keep the elements where `f` is true
```python
sc.parallelize([("pen", 1.5), None, ("cup", 3.0)]).filter(lambda x: x is not None).collect()
# [('pen', 1.5), ('cup', 3.0)]
```

### `reduceByKey(f)` — combine all values of a key
```python
sc.parallelize([("pen", 1.5), ("cup", 3.0), ("pen", 2.0)]).reduceByKey(lambda a, b: a + b).collect()
# [('pen', 3.5), ('cup', 3.0)]      total per key; the order of pairs may differ
```

### `saveAsTextFile(path)` — writes `str()` of every element, one per line
```python
pairs = sc.parallelize([("pen", 3.5)])
pairs.saveAsTextFile("out1")                              # part-* file contains: ('pen', 3.5)
pairs.map(lambda kv: f"{kv[0]};{kv[1]:.2f}").saveAsTextFile("out2")   # contains: pen;3.50
```
Turn each element into the exact text you want *before* saving. The output directory
must not exist yet, and the result is several `part-*` files, one per partition.

### Number formatting
```python
f"{3.14159:.1f}"   # '3.1'
f"{7.0:.1f}"       # '7.0'
f"{-3:.1f}"        # '-3.0'
```

## Level 1
Three stages: turn every line into either a reading or nothing, combine the readings of
each station, turn each result into one line of text.

## Level 2
Write a `parse_line` function that returns `(station, temperature)` or `None`, so all the
skip rules live in one place. After the maximum per station, `map` to the output string.

## Level 3
`sc.textFile(inputPath).map(parse_line).filter(lambda r: r is not None)`, then
`reduceByKey(max)`, then `map(lambda kv: f"{kv[0]}\t{kv[1]:.1f}")`, then
`saveAsTextFile(outputPath)`, then `sc.stop()`.
