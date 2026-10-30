# Maximum temperature per station from CSV files (PySpark RDD, file input/output)

## Concept

A standalone Spark program takes its input and output paths from the command line, reads  
text with sc.textFile and writes with saveAsTextFile; lines it cannot parse are filtered  
out, so one bad record does not fail the job.

## Task

Write a Spark program that writes the highest temperature of each station in inputPath  
to outputPath.

## Input

Every file in inputPath. Each line is
```text
station,date,temperature          e.g.  s1,2024-01-05,12.5
```

Skip these lines without crashing:
- the header line "station,date,temperature" (it may start every file)
- blank lines
- lines that do not have exactly 3 fields, or have an empty station
- lines whose temperature is not a number  
Fields may have spaces around them: " s1 , 2024-01-05 , 12.5 " is a reading for station "s1".

## Output

Save with saveAsTextFile to outputPath, one line per station:
```text
station<TAB>max_temperature       e.g.  s1  12.5
```

Format the temperature with one decimal place, exactly like Python's f"{t:.1f}"  
(12.5, -3.0, 3.1 for 3.14159). Line order does not matter. A station appears only if it  
has at least one valid reading.

## Example

```text
input file
    station,date,temperature
    s1,2024-01-01,12.5
    s2,2024-01-01,-3.0
     s1 , 2024-01-02 , 20.0

    s2,2024-01-02,oops
    s3,2024-01-02
    s2,2024-01-03,-1.5
output lines
    s1  20.0
    s2  -1.5
```

## Constraints

- RDD API only (sc.textFile, saveAsTextFile); no DataFrames.
- Do not use groupByKey.

## Slides

None: a synthetic example, not from a course.

## Run

```text
rm -rf sample_output && python student.py sample_input sample_output
cat sample_output/part-*
pytest test_student.py
```

sample_input/ holds the example above; Spark refuses to write into an existing output dir.  
Stuck? hints.md has useful functions and three levels of hints.

## How to run

With the repository's virtual environment active:

```bash
cd "examples/station-max-file/exercise"
pytest test_student.py      # the tests
python student.py           # the program, with the arguments Run shows, if any
```

Stuck? From the repository's folder: `python -m tutor.hint "examples/station-max-file/exercise"`
