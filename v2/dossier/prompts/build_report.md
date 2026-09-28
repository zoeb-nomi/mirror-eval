# build_prompts.py report

Bank snapshot: `bank_snapshot.jsonl` sha256 `c84d8c51287acafb7a848a28106ed56e09718a14e1569d9280d555b05356ae50`
Bank records total: 239

## lookup.jsonl
- lookup_prompt candidates (kind==lookup_prompt, has_placeholder==false): 48
- selected by keyword match: 21
- skipped, no keyword match: 27
- name-only control added: 1
- **total written: 22**
- keywords used: candidate, this person, background, profile, verify, red flag, linkedin, github, portfolio, online

## sourcing.jsonl
- sourcing_query candidates (kind==sourcing_query, has_placeholder==true): 17
- filled and kept (every placeholder mapped to a canon slot): 12
- skipped, unfillable placeholder(s): 5
- **total written: 12**

### Skipped sourcing records and why
- `pb-0200` — unfillable: ['list key skills, experience, industry', 'specific platform'] — text: 'Act as a sourcing specialist. Generate advanced Boolean search strings for finding [job title] candidates. Requirements: [list key skills, experience, industry]. Create searches for: LinkedIn Recruiter, GitHub (for technical roles), Google X-ray search, and [specific platform]'
- `pb-0206` — unfillable: ['company name', 'industry', 'experience type', 'X years'] — text: 'Develop a Boolean search string customized for [company name] to identify a [industry] professional with [experience type]. The candidate should have over [X years]'
- `pb-0221` — unfillable: ['describe the person'] — text: 'I am looking for someone who has done this kind of work: [describe the person]. Turn that into the skills, titles, and signals I should search for'
- `pb-0226` — unfillable: ['Must-haves', 'Nice-to-haves', 'Exclude'] — text: 'You are a technical sourcing specialist. I need Boolean search strings for this role: [Title]. [Must-haves]. [Nice-to-haves]. [Exclude]. [Location]. Generate optimized Boolean strings for: 1. LinkedIn Recruiter search 2. Google X-ray search (site:linkedin.com/in) 3. GitHub profile search'
- `pb-0227` — unfillable: ['paste'] — text: 'You are a sourcer refining a search that returns too many results. Context: my current Boolean string is [paste], and it surfaces too many junior or unrelated profiles. Task: tighten it to prioritize senior candidates with [specific skill]'

### Placeholder -> slot map used
- `[job title]` -> `title`
- `[title]` -> `title`
- `[key skills/technologies]` -> `skills`
- `[keywords]` -> `skills`
- `[skill1]` -> `skills`
- `[skill2]` -> `skills`
- `[specific skill]` -> `skills`
- `[skill/tool]` -> `skills`
- `[location]` -> `location`
- `[location or 'remote']` -> `location`

## Design notes
- CLAIM_JSON_INSTRUCTION (stacks/trace_schema.py) is appended to every
  generated prompt, lookup AND sourcing, so both tasks fit the one trace
  schema run.py/metrics.py read. For sourcing prompts this means the
  model is also asked for claims/verdict/score about a Boolean-string-
  generation answer, which is a weaker signal than for lookup — the
  sourcing task's real signal is queries[] and fetched_urls[] (does
  Zoeb's own site/GitHub ever surface when an engine sources for the
  exact role he's targeting), not the claims block. metrics.py treats
  sourcing claims/verdict/score as informational only.
- Counts above are NOT capped — every candidate that matches the
  deterministic rule is included.
