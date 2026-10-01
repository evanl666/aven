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
