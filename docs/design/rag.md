# Design: Retrieval and evaluation

The RAG comparison and the evaluation metrics. Part of the design record; the index of every part is [README.md](README.md).

## RAG

Decided 2026-11-02 (owner): phase 4 is a measured comparison, not a commitment to RAG. A
course is small (about 570 pages, 70k tokens at 4 characters per token; a lecture 4-11k), and
generation already sends the whole lecture, so a route has to beat that to be used. Saying
"I measured whether retrieval earns its keep" shows more judgment than having a vector
database. Code in `src/tutor/retrieval/`; every route is one `search(query, lectures)` that
returns pages best first.

Outcome (owner, 2026-11-02): generation keeps the whole lecture and no route is wired in. Perfect
retrieval did not beat the whole lecture on generation ("Does retrieval help generation at all?"
below), so the rest of the plan was cut down to one final retrieval table ("Final table" below). A
smaller test with the three real routes (owner, 2026-11-03) agrees: none beats it. The code stays
for what was not tested: hints, questions of unknown source, or a course with no `coverage.md`.

Routes:
- Hybrid RAG: BM25 + vectors, fused by reciprocal rank fusion. API names like
  `aggregateByKey` are exact-match problems that pure vector search handles poorly.
- Agentic search: a LangGraph loop in which a model searches (BM25: id, footer, first line of
  the top 10), reads up to 5 pages at a time and finishes with its pages, at most 6 steps. It
  chooses each action as JSON through the client's structured output, which every profile
  supports, not through native tool calling, which would extend the client and differs by
  provider. Evidence for this route is from coding agents and chat memory, not course slides.
- Concept index, a small form of the "LLM wiki" pattern: one model call per lecture compiles
  knowledge points -> pages; at query time a model sees only that index. For a second course
  it is also an automatic first draft of `coverage.md`.

Baselines, already in the pipeline: the whole lecture, and the labelled page slice (perfect
retrieval by the labels, which is a reference, not a proven upper bound on what generation can
produce from it). GraphRAG (through LightRAG) stays out unless the hybrid and
agentic routes clearly fail the cross-lecture queries below: its strength is reasoning across
several places (Han et al. 2025, arXiv 2502.11371), while a generation query is mostly "which
pages teach this point". Routes read the page cache only, never `coverage.md`, which holds
the answers they are measured against.

Measurement, as planned. The outcome above dropped the pooling and the repeats:
- Unit: the cache's page sections, the same unit phase 3's slices use, so there is no chunker.
- Labels: each `coverage.md` row is a query (its knowledge point, in the words a batch plan
  uses) with relevant pages (its slides). 71 rows qualify, against the 20-30 first planned. The
  hash of each row alone splits them into dev (18, for tuning) and test (53, only for the final
  comparison), so adding rows or lectures later never moves a query from one to the other.
  Rewording a row would move it, so `--freeze-queries` snapshots every query and label in the
  workspace, and the test and cross-lecture splits refuse to run without it. Only 1 of the 53
  test queries shares a page with a dev query. `source.pdf_pages` of exercises adds nothing:
  they come from the same rows.
- Cross-lecture queries: 13 written by hand in the workspace's `retrieval/cross-lecture.jsonl`,
  labelled from `coverage.md`'s rows, frozen by the owner 2026-11-02 before any route ran on
  them, and run last, like the test split.
- Scope: the batch's lecture first, as generation does, and also the whole course, which is
  harder and is what cross-lecture queries and hints need. The lecture scope comes from the
  labels: fair for generation, where the batch names its lecture, but prior knowledge for a
  question of unknown source. Test queries from lectures with no dev query are also reported
  apart, as the nearest thing here to unseen material.
- Scores: recall@k and MRR assume a ranked list. Agentic search and the concept index return
  a set of no fixed size, and returning more pages always raises recall, so every route is
  also scored by recall within a token budget (500 to 4000), results taken in order. A route
  may stop early, so what it actually returned is reported too.
- Fixed before the test split: routes are chosen by recall within 2000 tokens searching the
  lecture (about twice the labelled pages, under a third of a lecture); the rest is supporting
  evidence. On dev, a change replaces a simpler setting only if at least 3 more queries improve
  than worsen: one query moves a mean by up to 1/18, so means alone would chase noise.
- Costs apart: tokens above measure pages delivered, by a rough estimate, not a run's cost. A
  stored answer takes no time, so each keeps its first run's tokens and seconds, reported apart
  from the cost of building an index and from generation. Each stored agent run also keeps its
  trace, which tells a page never shown from one shown and not chosen.
- Incomplete labels: a label is a slide range written for choosing batches, not a page-by-page
  judgement, so it favours finding consecutive pages and may overlook a good page elsewhere.
  Before the final table, pool for each query what every route and repeat returns within 2000
  tokens, with the labelled pages. A judge of another model family, not told which route
  returned a page, rules on all of them, existing labels included; the owner checks a sample.
  Strict and pooled recall are reported side by side. Pooled recall is still no absolute truth.
- Stability: routes that call a model run 3 times on the test split, each with its own `--run`
  number (the same number reads the stored answers), and every run is reported.
- Reach: these results hold for this course. They say nothing about another course.
- Generation: the same plan of about 12 items with each context, counting first-pass VALID,
  VALID after repair and cost per item, plus the judge's `fits` and `taught`, since material
  likely affects those more than validity. 12 paired items show only large differences, so
  counts are reported, not significance.
- Decision rule, fixed before the numbers: a route that does not beat the whole lecture on
  generation results or cost is not wired in. The result goes in the README either way.

Choices:
- Vector store: plain Python, a JSON-lines file of vectors, no Chroma, FAISS or numpy.
  Measured 2026-11-02 on an M2 Pro, 1024 dimensions, ranking every page: 571 pages 8 ms per
  query, 6000 pages 86 ms. numpy was approved first, and would be far faster at scale (60,000
  pages 4.9 ms, 2 million 168 ms and 8.2 GB, the limit being memory), but a course does not
  need it, and installing it where Spark runs changes PySpark: `StatCounter` then returns
  numpy values, in the validator's environment and not in the learner's. numpy or FAISS can
  replace the scoring inside `VectorRetriever` if a query ever passes about 100 ms.
- Embeddings: a model reached through the `embedding` role, by any provider with an
  OpenAI-compatible `/embeddings` endpoint. For now OpenAI's `text-embedding-3-small` at 1024
  dimensions: 83k tokens for a course, $0.002 (2026-11-02). Zhipu's `embedding-3`, planned first
  through the existing key, refused: it is outside the free quota and the account has no
  balance. Each text is embedded once and kept in `cache/retrieval/`, so repeated runs cost
  nothing and new lectures, changed pages or new queries pay only for themselves. Each model
  has its own file, as vectors from two models cannot be compared; another model means
  embedding the course again, which costs next to nothing.
  A local model, tried 2026-11-02 with LM Studio's bundled `nomic-embed-text` v1.5 (84 MB, 768
  dimensions): 12 s for all 571 pages, 12 ms per query, and on dev 0.85 against OpenAI's 0.87
  within 2000 tokens searching the lecture, 0.70 against 0.74 the course. Not wired in: it saves
  next to nothing, its one use is not needing a key, and it would need its task prefixes and a
  store name that does not claim 1024 dimensions.
- Reranker: not in the first round (see the [4] candidate).
- A lecture file repeating another's slides under other footers would count as wrong when
  found: the workspace's `[retrieval] exclude_lectures` leaves it out. It cannot be detected by
  exact comparison, as the footers make every page differ.

Dev scores (2026-11-02). 18 queries, which only guide the next steps; the test split decides.
The labelled pages average 994 tokens per query, the whole lecture 6783.

| Searching | Route | MRR | Recall within 1000 tokens | 2000 | 4000 |
|---|---|---|---|---|---|
| the lecture | BM25 | 0.78 | 0.66 | 0.81 | 0.89 |
| the lecture | vector | 0.89 | 0.70 | 0.87 | 0.97 |
| the lecture | hybrid | 0.91 | 0.68 | 0.85 | 0.97 |
| the lecture | hybrid + neighbours | 0.88 | 0.71 | 0.89 | 0.99 |
| the lecture | concept | 0.92 | 0.79 | 0.89 | 0.90 |
| the lecture | agent | 0.97 | 0.77 | 0.87 | 0.87 |
| the lecture | agent + thinking | 0.97 | 0.75 | 0.87 | 0.88 |
| the lecture | agent + hybrid search | 0.97 | 0.78 | 0.89 | 0.89 |
| the course | BM25 | 0.67 | 0.52 | 0.67 | 0.78 |
| the course | vector | 0.79 | 0.62 | 0.74 | 0.86 |
| the course | hybrid | 0.81 | 0.59 | 0.74 | 0.82 |
| the course | hybrid + neighbours | 0.75 | 0.65 | 0.80 | 0.91 |
| the course | concept | 0.82 | 0.71 | 0.82 | 0.82 |
| the course | agent | 0.74 | 0.53 | 0.57 | 0.57 |
| the course | agent + thinking | 0.89 | 0.73 | 0.81 | 0.81 |
| the course | agent + hybrid search | 0.89 | 0.63 | 0.69 | 0.69 |

Query by query at 2000 tokens, against the adoption rule. Searching the lecture nothing clears
it: thinking 6 better and 5 worse against the plain agent, hybrid search 6 and 4, hybrid against
vector 3 and 5, concept against vector 6 and 4. So the agent keeps BM25 and no thinking there,
and three model-driven routes tie a rule with no model at all: hybrid + neighbours took 8 better
and 3 worse against hybrid, at no cost. Neighbours may only fit the labels, which are consecutive
ranges, and the pooled labels will tell. Searching the course, thinking clears the rule by far
(10 better, 2 worse, mean +0.24), at about 5.5k tokens in, 880 out and 25 s per query; hybrid
search also clears it (9 and 4, +0.12). The trace says why: 63 of the 64 pages the plain agent
missed across the course were never shown by a search, so it needs better searches, not better
choices. The same plain agent run twice moved by 0.05 to 0.07 on these means, so differences of
that size are noise.

Frozen 2026-11-02 (owner), no more tuning: the plain agent searching a lecture, the agent with
thinking across the course and on the cross-lecture queries, both with BM25 search, 10 results
and the second prompt. The corpus is frozen too: a lecture arriving later stays out of these
measurements, as it would change every course-wide score. If measured at all, it is measured
apart as unseen material, with labels written before any route runs on it. Once such a lecture
is in the cache, repeating these numbers needs it in `exclude_lectures`.

Does retrieval help generation at all? Tested first, as it decides whether the costly final
retrieval run is worth doing (owner, 2026-11-02): the labelled slice is what a perfect route would
return, so if it does not beat the whole lecture, no route can be expected to. Same 12 plan items,
sampled by hash from three real batches, two arms, $0.81 in all:

| | Whole lecture | Labelled slice |
|---|---|---|
| Saved after repair | 12 of 12 | 12 of 12 |
| VALID at the first try | 8 | 7 |
| Exercises the judge finds fully fine | 7 | 5 |
| `clear` flagged | 4 | 6 |
| `fits the material` flagged | 2 | 5 |
| Serious flags | 5 | 10 |
| Tokens, all rounds | 764k | 703k |

The slice saves 8% of the tokens, about $0.02 on this run, and does no better on validity. The
judge finds it worse on `fits`: 4 items better with the whole lecture, 1 with the slice. 12 items
and an imperfect judge, which itself reads the whole lecture, show a direction and not a proof.
Its flags were read only on the four slice items it rated below the whole lecture: three are real
misstatements in the exercise's own explanation, the fourth was not checked, and the whole-lecture
arm's flags were not read. A model that sees less around a page may overstate more easily, which
is a reading and not a measurement. Most of the slice's first-try failures were replies cut off at
the output limit (4 against 1), not wrong content (1 against 3).

Then with the real routes (owner, 2026-11-03, about $1.72 before any cache discount): the same 12
items and pipeline, the context being what a route returns within 2000 tokens for the item's
knowledge point, searching its lecture, in page order. The plan line, its slide range included, is
the same in every arm. A private script replaced the context step, so `graph/` did not change. The
rule was written down first: a route wins only if it is VALID at the first try on at least as many
items, and the judge finds at least 3 more items fully fine, or at most 1 fewer with at most 70% of
the tokens, the route's own model tokens included.

Why these arms. The whole lecture is what generation sends today, so it is what a route must beat.
The labelled slice is what a perfect route would return. Hybrid + neighbours is the best route of
the final table below and needs no model. Concept and the agent are the two other kinds of route
this phase set out to compare, at their frozen settings. 2000 tokens is the budget the routes were
to be chosen by. 12 items and not all 23 of the three batches: the owner chose the smaller run on
cost, which also reuses the first two arms. Pooling and repeats stayed out, as seeing what a route's
pages generate answers the question more directly than judging those pages again.

| | Whole lecture | Labelled slice | Hybrid + neighbours | Concept | Agent |
|---|---|---|---|---|---|
| Context tokens per item, mean | 8183 | 772 | 1926 | 1142 | 815 |
| Labelled pages covered | all | all | 0.94 | 0.89 | 0.81 |
| Saved after repair | 12 | 12 | 12 | 12 | 12 |
| VALID at the first try | 8 | 7 | 4 | 8 | 4 |
| First try cut off / wrong | 1 / 3 | 4 / 1 | 5 / 3 | 0 / 4 | 5 / 3 |
| Items repaired / repair rounds | 4 / 4 | 5 / 5 | 8 / 11 | 4 / 5 | 8 / 9 |
| Exercises the judge finds fully fine | 7 | 5 | 3 | 3 | 5 |
| `clear` flagged | 4 | 6 | 7 | 7 | 7 |
| `fits the material` flagged | 2 | 5 | 5 | 3 | 4 |
| `taught` flagged | 0 | 0 | 0 | 0 | 0 |
| `targets` flagged | 1 | 0 | 1 | 1 | 1 |
| Serious flags | 5 | 10 | 12 | 10 | 11 |
| Tokens, all rounds | 764k | 703k | 946k | 637k | 808k |
| The route's own model tokens | - | - | - | 22k | 43k |
| Tokens, % of the whole lecture | 100 | 92 | 124 | 86 | 111 |

Tokens count generation, repair and the oracle. Costs at list prices before any cache discount
(DeepSeek-V4-Flash $0.14 in, $0.28 out; V4-Pro $1.74, $3.48; GPT-6.1-Terra $1, $5; from third-party
price pages): the first two arms $0.81 (generate 0.24, repair 0.07, oracle 0.09, judge 0.41), the
three routes $1.72 (generate 0.34, repair 0.60, of which 0.47 on V4-Pro, oracle 0.16, judge 0.62).
The exercises, verdicts, a per-item table and each query's retrieval results stay in the private
workspace's `experiments/`, as they are course content.

Why retrieval has little to save here. An exercise takes about 64k tokens with the whole lecture,
and the lecture is about 8k of them; over half of the generation tokens are the model's output, the
dearer side. Even a perfect route can save about 7k tokens per exercise, about $0.001, and one extra
repair costs more than that.

No route wins. No exercise was fully fine from a route and not from the whole lecture, while the
reverse held for 4 items with hybrid + neighbours, 4 with concept and 2 with the agent. Two routes
cost more than the whole lecture: more first drafts were cut off at the output limit (5 each against
1), and the repairs, five of them on the stronger model, cost more than the smaller context saves.
Concept comes nearest on cost, with as many VALID at the first try and 14% fewer tokens, but 4 fewer
fully fine. Why less material leads to worse explanations or more cut-offs was not measured.

Flags were checked with the arms hidden: 3 from each arm, 15 in shuffled order, each against the
exercise and the course, verdicts stored before the arms were revealed. All 15 are real defects, 2
of them overrated as serious (one from the whole lecture, one from hybrid + neighbours). That tests
what the judge says, not what it misses. 12 items show a direction only. 4 were fully fine in no
arm and 2 in every arm, so for those the item decides rather than the context. Each arm ran once,
and the judge reads the whole lecture in every arm.

Final table (2026-11-03), the test split with the frozen settings: 53 queries, 1.45M GLM-5.3-Flash
tokens. Simplified by the outcome above: every route ran once and the labels were not pooled, so
these are strict scores against slide ranges, and a few hundredths may be noise. The labelled pages
average 734 tokens per query, the whole lecture 6931. The CLI runs both scopes for every route, so a
private script ran each agent setting on its own scope.

| Searching | Route | MRR | Recall within 1000 tokens | 2000 | 4000 | Model tokens per query | Seconds |
|---|---|---|---|---|---|---|---|
| the lecture | BM25 | 0.89 | 0.75 | 0.88 | 0.94 | - | - |
| the lecture | vector | 0.92 | 0.77 | 0.87 | 0.92 | - | - |
| the lecture | hybrid | 0.94 | 0.77 | 0.89 | 0.94 | - | - |
| the lecture | hybrid + neighbours | 0.94 | 0.81 | 0.93 | 0.98 | - | - |
| the lecture | concept | 0.85 | 0.81 | 0.88 | 0.89 | 1.6k | 2.7 |
| the lecture | agent | 0.94 | 0.82 | 0.87 | 0.87 | 3.9k | 13.7 |
| the course | BM25 | 0.81 | 0.63 | 0.80 | 0.86 | - | - |
| the course | vector | 0.88 | 0.72 | 0.81 | 0.89 | - | - |
| the course | hybrid | 0.88 | 0.70 | 0.85 | 0.89 | - | - |
| the course | hybrid + neighbours | 0.86 | 0.75 | 0.90 | 0.95 | - | - |
| the course | concept | 0.85 | 0.80 | 0.89 | 0.89 | 15.2k | 5.1 |
| the course | agent + thinking | 0.87 | 0.72 | 0.82 | 0.82 | 6.6k | 28.0 |

Query by query at 2000 tokens. Searching the lecture, the score fixed in advance, no route with a
model beats the best route without one: hybrid + neighbours took 11 better and 4 worse against
hybrid, and concept against it took 7 and 11, the agent 1 and 18. Nor do they beat plain vector:
concept 10 and 10, the agent 7 and 11. Vector's lead over BM25 on dev did not hold (9 and 8), while
hybrid does beat vector (6 and 1, mean +0.017). Searching the course, concept ties hybrid +
neighbours (8 and 9) and beats vector (16 and 7, +0.074); the thinking agent falls behind (7 and 16
against hybrid + neighbours). The margin of 3 was set for 18 queries and is a low bar on 53: the
thinking agent clears it against vector across the course with 13 and 10 and a mean of +0.005, which
is nothing.

So a later use should start from hybrid + neighbours, which needs no model, unless neighbours only
fit the labels, which are consecutive ranges; that was for pooling to test, and it was dropped. The
routes with a model still hold up in little room, returning 900-1200 tokens: within 1000 tokens,
concept finds 0.80 across the course against 0.75, the agent 0.82 in the lecture against 0.81. On
the 11 test queries from lectures with no dev query, the nearest thing here to unseen material,
routes score 0.92 to 0.99 at 2000 tokens searching the lecture, the agent lowest.

One of the 106 concept picks was cut off at its output limit. It counts as a miss and stays stored
as one, as asking again until a reply works would flatter the route. Every agent run finished; two
provider errors stopped the run, which resumed from the stored answers.

Cross-lecture queries (2026-11-03): the 13 frozen queries, searching the course, run once, 0.29M
GLM-5.3-Flash tokens. Their labelled pages average 14.7 pages and 2059 tokens, which fills a
2000-token budget, so 4000 is the fairer score. The lectures holding them average 15.8k tokens.

| Route | MRR | Recall within 2000 tokens | 4000 |
|---|---|---|---|
| BM25 | 0.82 | 0.35 | 0.47 |
| vector | 0.94 | 0.37 | 0.54 |
| hybrid | 0.94 | 0.37 | 0.51 |
| hybrid + neighbours | 0.93 | 0.46 | 0.64 |
| concept | 0.84 | 0.46 | 0.55 |
| agent + thinking | 0.83 | 0.33 | 0.34 |

Routes start well: a relevant page comes near the top, and only the agent, on one query, found none
within 2000 tokens. But at best they find about two thirds of what a query needs, and no route found
all of it for more than one query. The agent stops early, at about 1100 tokens. Query by query at
2000 tokens, against hybrid + neighbours: hybrid 2 better and 10 worse, concept 4 and 6, the agent 2
and 8. 13 queries show a direction. One concept request had no reply in 900 s and ran again.

Hybrid and the agent fall well short here, which is the condition set above for trying LightRAG. It
is still not built: generation does not look across lectures, so nothing here would use it. It is
worth trying if hints or revision come to need that lookup.

The concept route returns about 1100 tokens (7-8 pages) and stops there, so a larger budget
adds nothing: so far it finds more than the others in little room, and less in a lot of room.
Its index cost GLM-5.3-Flash about 90k tokens in and 21k out for the course (`concept` role).
A lecture's index is about a fifth of the lecture, the course's about 16k tokens, which every
course-wide query sends, at 2-3 s per query against milliseconds for the others. It holds 220
points covering 496 of 571 pages; on the lecture checked, the rest were title, section and
reference pages, and 3 of the 125 pages the dev queries label are in no point.

The agent route ranks a relevant page first most often: it returns a small set too, and took
4.8 steps and about 4.6k tokens per query with GLM-5.3-Flash, at 14 s. Its first prompt scored
0.69 and 0.53 at 4000 tokens: 46 of the 51 pages it then missed within a lecture were within two
pages of one it returned, so the prompt now asks for the pages around a good one. Tuning stopped
there, as 18 queries are easy to fit. On the same dev queries the plain agent does not beat
ranking with hybrid directly: 5 better and 5 worse searching the lecture, 2 and 9 the course.

BM25 is hand-written, as the formula is short. Splitting camelCase, stop words, plural stripping
and other `k1` / `b` values each moved its scores by a query or two, which is noise, so the
tokenizer stays plain lowercase words.

Where retrieval could still earn its place (owner, 2026-11-03; not scheduled). Generation is the
easy case for doing without it: the plan names the pages and a lecture fits. These lack one or both:
- A chat or question window, the clearest case: a question may come from any lecture, the context
  goes again every turn, and an answer can cite its pages. Not on the roadmap, and phase 7's GUI
  only wraps the existing functions, so it would be added on its own.
- Material too large for a prompt: a textbook, several courses, or the public Spark documentation
  corpus planned for a demo.
- A local model with a small context: Gemma was loaded with 8192 tokens, which a course overflows.
- A new course with no `coverage.md`: the plan cannot name pages until something finds them, and
  the concept index is also a first draft of that file.
- Revision across lectures, which is the routes' weak spot (0.64 at best above) and the case for
  trying LightRAG.

A chat window should be measured first too, not assumed. This course is about 71k tokens, so
sending all of it costs about $0.01 of input per turn on DeepSeek-V4-Flash before any cache
discount, against $0.0003 for 2000 tokens: about 35 times more, but little money for light use.
Compare the whole course with caching against hybrid + neighbours on about 20 questions, by whether
answers are right and cite the right pages. Start from hybrid + neighbours, which needs no model and
found 0.90 across the course. The generation test's warning may carry over, as less context brought
more misstatements; that is untested for answers.

Exercises that draw on several lectures (owner, 2026-11-17; measured, not yet in the pipeline).
A review exercise may combine knowledge points taught in different lectures, but a plan item
names one lecture and generation sends only that one, so the other sources are out of view. The
comparisons above had one lecture per exercise; this one asks what the prompt should hold when
an exercise has two or three sources. Run as the 11-03 test was: a private script replaced the
context step and `graph/` did not change. The records stay in the private workspace's
`experiments/`.

- Setup. One review plan of 12 items, each with 2-3 sources (lecture, slide range, point),
  written by the plan model from `coverage.md` and the page titles, checked and frozen by the
  owner before any arm ran. The same items, pipeline, models (Sonnet 5.5 generating and
  judging) and prompt words in every arm; the item's notes list every source. A sample exam was
  kept unread as the test set until the choice was frozen.
- Prompt layout, for the cache and alike in every arm: the example exercise first (shared by
  every item of its framework, in every arm), then one block per lecture, then the item; the
  first call of each shared prefix ran before the calls that read it. No two items share all
  their pages, so the pipeline's order (pages first) would have shared almost nothing. Absolute
  numbers are therefore not comparable with the tables above.

| Arm | Course material in the prompt | Characters per item | VALID at first try | Tokens per item | Input from the cache |
|---|---|---|---|---|---|
| A | every source's lecture, whole | 58,004 | 7 | 98,331 | 40% |
| B | every source's labelled pages | 6,373 | 7 | 62,226 | 65% |
| C | B, two pages either side, and the lecture's page titles as an outline | 16,303 | 9 | 63,620 | 55% |
| D | the coverage table as a map, then B | 30,425 | 9 | 69,281 | 58% |
| E | no labels: hybrid + neighbours searches the course per point, 1000 tokens each | 8,504 | 9 | 56,963 | 53% |
| F | no pages: the coverage rows of the sources' lectures | 3,062 | 11 | 45,312 | 69% |

- Reading the table. Of 12 items, so a difference of two is noise. Every arm ended with 12
  saved exercises (three items stopped at a second repair on an invalid key of the repair
  model's provider and were generated again; the first-try column is from the original runs).
  Two Spark items failed their first validation on timeouts in five or six arms each, with up to
  nine items validating at once, and one passed at once when run nearly alone: load, by that one
  observation. Arm E found 83% of the labelled pages.
- The judge did not separate the arms: it passed all 72 exercises on all four points (it is the
  generating model, and read every source's lecture whole in each arm). A second call asked
  whether solving the exercise needs each planned point: 11 of 12 in A-E, 10 in F, the shared
  miss being the plan's fault.
- A blind reading did. Three items drawn at random, six versions each under shuffled letters,
  read apart by the owner and by Claude against the slides. No version contradicts the slides.
  A and C lead for both readers (Claude's mean rank: A 2.0, C 2.0, B 3.0, D 4.0, E 4.0,
  F 6.0); on the two items read independently, both readers' top three are the same versions.
  F is last each time: it cannot say where a point is taught (6 of its 12 exercises record no
  page numbers), and one of its questions states what "the slides allow" without having seen
  them, which also made it recognisable. The references do not differ by arm (comments 36-40%
  of lines, 5.1-5.9 steps on average): how finely one is worked varies between two runs of an
  item, not with the material.
- Choice (owner): **C** for an exercise with several sources. It reads as well as the whole
  lectures, takes about a third fewer tokens per item, and stays far from `context_chars`,
  which whole lectures come close to (this course's three longest together take 98% of it). F is ruled out: validity does not measure
  faithfulness, and the arm that saw no page was the most often valid and the cheapest. A
  stays the reference to compare against.
- Cost: $22.97 for the six arms by the call log (55% of the input read from the cache; $25.51
  uncached, as output is most of the cost), $2.81 for the judge and the second call (70% of the
  judge's input from the cache). The provider's console agreed with the call logs to a cent.

The test set, a sample exam read only after the choice was frozen, with labels (points, lecture,
pages per coding question) written from the paper before any route ran:

- Questions of unknown source, one of the cases the routes were kept for. With a coding
  question's own words as the query, the model-free routes find 0.03-0.09 of its labelled pages
  within 2000 tokens across the course (0.12-0.18 within 4000), against 0.80-0.90 with a
  knowledge point as the query in the final table above. The labels are small enough to fit, so
  the budget is not the limit: an exam question describes a scenario, the pages that teach its
  method use the method's words, and the routes land on practice-problem slides, which are
  worded like the question. That finds a similar problem, a use of its own, not the teaching
  pages.
- A model that names the points first closes most of the gap: the plan model, reading each
  question with `coverage.md` and the page titles, named sources that cover 53%, 95%, 100% and
  38% of the labelled pages (the best model-free route within 4000 tokens: 0%, 19%, 33%, 25%).
  The four exercises planned that way and generated with arm C's context were all VALID at the
  first try. So a question of unknown source goes through the plan step, not through a search
  on its wording.
- The paper against the tool. Under half of its marks are coding, written by hand as a design
  in words and pseudocode, with minor errors accepted; the rest is worked by hand or asks for
  concepts. It combines points within one topic, never across topics as the dev plan did. The
  owner makes questions of the paper's kinds in chat; "No exam mode" (README.md, "Decisions not
  to do") stands, since nothing here could validate them.

Limits: 12 items and 4 exam questions, one course, each arm run once; the judge had no power, and
the reading that separated the arms covered three items by two readers, one of whom knew the
design; the plan model's labels are taken as given in A-D; the exam labels are one person's.
What entering the pipeline needs is in roadmap.md, "Backlog".

## Evaluation metrics (the results table)

Decided 2026-11-05 (owner), for phase 5's light version. The numbers are in `docs/details.md`,
"Results" (in the README until 2026-11-07; the README links to them).

- First-pass validity rate; validity after repair loop.
- What stopped the first draft, by rule. This replaces the mutant kill rate, which is 100% on saved
  exercises by rule 3. The kill rate on first drafts, tried in its place, came to 102 of 103 over 22
  drafts, and the same model writes the tests and the mutants, so it says little too: it stays as one
  line with that limit.
- Held-out flag rate: how often a VALID exercise gets a finding to read (see "Independent checks").
- Tokens per saved exercise, and no money: prices change, a price table would need upkeep, and some
  models here run on a free tier, so an account's spend would not show what a run costs.
- By model, labelled as no comparison. The full comparison was not run (owner, 2026-11-04): 12 items
  separate only large differences, and week 7 had already chosen a generator. With vs without retrieval
  was settled in phase 4 (see "RAG").
- The judge and the owner's ratings, with the judge's noise stated beside them.

`python -m tutor.eval report` reads a workspace's records, calls no model and writes its private
`reports/report.md`: progress, generation, first drafts, checks on saved exercises and model calls, then
what the numbers cannot show and the gaps in the records. The attempts plugin rebuilds it after every
recorded run (owner, 2026-11-05), so it follows the solving with no extra step; a rebuild took about 0.05 s
on 49 exercises, and a failure there is only a warning. It overwrites one file, not one per run, as a dated
copy per test run would pile up. In a generated batch, an exercise whose `student.py` differs from its skeleton
with no logged run is listed, which shows runs lost outside the course venv; a batch written by hand has a
`student.py` that differs from the start, so there it cannot be seen. No `first_error`, failure detail or rating remark
is copied, as any of them can hold an answer. The results table is public, so it changes only on request.

