# Template library

Templates are reference text written for the model. Installation and overview: [README_EN.md](../README_EN.md).

## Template library

Templates are **reference text written for the model**, used to pin down the commands you have accumulated in this environment and to cut down on guessing and back-and-forth confirmation. They are text only: injected as reference, **never executed**.

**Templates enjoy no privilege whatsoever.** A command derived from a template is treated exactly like any other command and goes through the same dangerous-pattern check and human confirmation. Templates are notes you maintain yourself, not a trusted source — doubly so for templates from a cloned repository.

### Where templates live

```
<project>/.woc/templates/*.md      # project level
~/.config/woc/templates/*.md       # user level; same id overrides the project one
```

Command-generating entry points (`woc`, `wocc`) load and inject them automatically; chat mode (`woc -c`) does not.

### Template format

```markdown
---
id: top-ip-from-log
desc: Top N client IPs by request count
tags: [nginx, log, statistics, awk]
when: user asks "which IP gets the most requests"
---

# Scenario
Analyse a web access log and find the busiest client.

# Reference command (annotated by the author, not verified)
awk '{print $1}' /var/log/nginx/access.log | sort | uniq -c | sort -rn | head -n 10

# Conventions
- In combined format the IP is always column 1.

# Do not do this
- Do not use `cut -d' ' -f1`: the request line contains spaces and the column shifts in some variants.
```

`id` is optional and defaults to the file name. `desc` / `tags` / `when` drive retrieval, so fill in all three. The body is injected **verbatim**, so sections like "Do not do this" are read as written.

### Management commands

```bash
woc --templates                                   # list templates, sources, and the two-layer policy
woc --templates --query "which IP hits us most"   # preview which bodies a query would select
woc --check-templates                             # self-check: portability traps, multi-line truncation
```

### Two-layer injection: constant index, budgeted bodies

Templates are injected in two layers with **separate budgets that do not squeeze each other**:

| Layer | Content | Policy |
|---|---|---|
| Index | `id \| desc \| when \| tags` | **Always complete**, so the model knows what exists |
| Body | The full template body | Query relevance plus a character budget (`BODY_BUDGET`, default 4000) |

The key rule: **retrieval only decides whose body is sent, never trims the index**. If the index were filtered by query, the model would not know the other templates exist. When the budget fits everything the policy is `full`, identical to injecting all bodies; otherwise it switches to `ranked`.

Relevance scoring (`select_bodies`):

- `tags` hits weigh 10, an `id` appearing verbatim in the query weighs 20;
- other terms are down-weighted by **inverse document frequency**: a word in one template weighs 1.0, a word in nearly every template approaches 0 (the current template set is the corpus);
- the same word in `when` counts 2.5, in `desc` 1.5, in the body 1.0;
- ASCII tags match on **word boundaries** (`log` does not hit `catalog`), while Chinese tags stay substring-based (with a 2-character sliding window, so "看看日志" still hits the tag `日志`);
- when the budget does not fit, templates scoring 0 get no body (pure noise); the index is still complete.

**So `tags` and `when` are not decoration** — how well you write them decides whether a template is selected at all.

### Installing globally

Project templates only apply inside this repository. To reuse them everywhere:

```bash
woc --install-templates                      # install every template of this project to the user level
woc --install-templates git-recent-changes    # only the listed ones
python3 compile.py --global-templates [ID...]  # equivalent entry point via the installer
python3 compile.py --list-templates            # see what is there first
```

Templates with identical content are skipped (shown as "up to date"), so running it repeatedly is safe. The user-level directory follows `XDG_CONFIG_HOME`, defaulting to `~/.config/woc/templates`.

### Injection order and caching

The injected message order is fixed:

```
system: general rules → multi-turn hint → <templates>(index + bodies) → <environment> → user: your question
```

The first four segments are byte-identical for the whole session, which is what keeps hitting the server-side prefix cache (`prompt_cache_hit_tokens`). **Never put anything that changes every run (time, git status, live load) into a template** — that would invalidate the cache permanently.

### Environment variables

```bash
export WOC_TEMPLATES=0            # disable template injection entirely
export WOC_TEMPLATES_USER=0       # disable user-level templates only
export WOC_TEMPLATES_PROJECT=0    # disable project-level templates only
export XDG_CONFIG_HOME=...        # relocate the user-level template directory
```
