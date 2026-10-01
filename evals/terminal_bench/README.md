# Running Terminal-Bench against aven

```bash
pip install terminal-bench
export ANTHROPIC_API_KEY=sk-ant-...

tb run \
  -d terminal-bench-core==0.1.1 \
  --agent-import-path evals.terminal_bench.aven_agent:AvenAgent \
  -t count-dataset-tokens \
  --n-concurrent 1
```

Needs Docker — each task is a container. On a Mac with colima rather than
Docker Desktop you also need the compose plugin, which colima does not bring:

```bash
brew install docker-compose
mkdir -p ~/.docker/cli-plugins
ln -sf "$(brew --prefix)/bin/docker-compose" ~/.docker/cli-plugins/docker-compose
```

Check the pipeline with `-a oracle` first. That runs the task's own reference
solution instead of a model, so it costs nothing and tells you whether a
failure is yours or the harness's.

## What a score from this does and does not mean

It measures whether aven gets in the way of a model that is trying to work:
whether the tool descriptions are clear, whether the loop stalls, whether
compaction loses something it needed. Those are real questions and this answers
them.

It measures nothing about what aven is for. The adapter passes `--yes`, because
nobody is in the container to approve anything and staged work would otherwise
sit there untouched — so staging, approval, the sandbox and undo are all
switched off for the duration. An agent that ran `rm -rf` and then wrote a
confident summary would score well here.

Terminal-Bench also rewards an agent for roaming the whole container, while
aven refuses every path outside the roots it was given. That is a deliberate
disadvantage on this benchmark and the right behaviour everywhere else.

So: useful as a check that the basics hold up, misleading as evidence that the
design works. The claims this project actually makes need their own
adversarial evals, which do not exist yet.

## Cost, which is the number that measures the harness

```bash
python evals/terminal_bench/score.py          # the newest run
python evals/terminal_bench/score.py runs/2026-09-30__22-59-56
```

Accuracy is mostly a statement about the model: given a working shell, it
either knows how to fix the pipeline or it does not. Cost is mostly a statement
about the harness, because the harness is what decides how many tokens it takes
to get there — how long the tool descriptions are, whether the prefix stays
byte-identical so it can be read from cache at a tenth of the price, whether a
40,000-character page sits in the conversation ten turns after anybody needed
it. Every economy aven claims shows up here and nowhere else.

Three numbers to read:

- **cost per solved task**, not per task. An agent that gives up cheaply has an
  excellent cost per task.
- **cache hit rate**. On a multi-turn task nearly all input should be a cache
  read. A low rate means something upstream is changing bytes between turns,
  and it is a 10x price difference on the largest number in the run.
- **output tokens**, the one thing that can be neither cached nor offloaded.

The prices are a table in the script, not something the run records, so they
are printed with the results and can be checked against the invoice.

## Running a slice, defensibly

```bash
python evals/terminal_bench/sample.py 30            # which tasks, and why
FLAGS=$(python evals/terminal_bench/sample.py 30 --args)
sh -c "tb run -d terminal-bench-core==0.1.1 \
  --agent-import-path evals.terminal_bench.aven_agent:AvenAgent \
  --model anthropic/claude-haiku-4-5-20251001 $FLAGS --n-concurrent 3"
```

(`sh -c` because zsh does not word-split an unquoted `$FLAGS`, so the thirty
`-t` flags arrive as one pattern and `tb` matches no tasks at all.)

A sampled score is worth nothing if the sample could have been chosen after
seeing the results. `sample.py` is what makes that checkable: the mix of
difficulties matches the whole set, the tasks within a difficulty are taken at
even spacing through the sorted names, and there is no seed — a seed is one
more thing somebody can try several of. The same `n` always gives the same
tasks.

A sampled score estimates what the full set would say, with the error of thirty
draws rather than eighty. It is not comparable to a published 80-task number.
