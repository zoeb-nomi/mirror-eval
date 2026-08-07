# Rubric and judge protocol

The primary output is the **claim-failure taxonomy** in [`taxonomy.yaml`](taxonomy.yaml).
The 0–3 scores below are a secondary rollup, kept because a lift chart needs a scalar.
When the two disagree, the taxonomy is the one to act on: counts name fixes, scores don't.

---

## Scoring anchors (secondary)

Scored against [`canon.yaml`](canon.yaml). The judge sees the canon, the question, the
answer, and the URLs the engine actually cited — nothing else.

### IDENTITY — is the current role and positioning right?

| | |
|---|---|
| **0** | Wrong era or wrong company. A past job described in the present tense. |
| **1** | Vague or hedged. No clear current identity asserted. |
| **2** | Current company **or** wedge correct, not both. |
| **3** | Both correct. |

### ACCURACY — are specific claims consistent with canon?

| | |
|---|---|
| **0** | Asserts a stale or fabricated claim as fact. |
| **1** | Generic. Specifics unverifiable or slightly off. |
| **2** | Correct but thin; no canon numbers. |
| **3** | Canon-consistent specifics; numbers match exactly. |

The asymmetry is deliberate: a **wrong specific scores below a vague generality**, because
a reader cannot distinguish a fabricated number from a real one. Hallucinated precision is
the more dangerous failure.

### ENDORSEMENT — would-hire signal

| | |
|---|---|
| **0** | Declines to assess, or negative. |
| **1** | Heavily hedged — "not enough information." |
| **2** | Cautiously positive with real reservations. |
| **3** | Confident recommend, citing concrete evidence. |

`null` where the prompt asked for no judgement.

### SOURCES — quality of the surfaces actually cited

| | |
|---|---|
| **0** | None, or only `poisoned_sources`. |
| **1** | Mixed; majority poisoned or irrelevant. |
| **2** | Mixed; majority clean. |
| **3** | Clean sources only. |

`null` in `knowledge` mode.

---

## Judge protocol

**Two families, not two passes.** Claude and Gemini judge every answer independently.
Running the same judge three times measures its *precision*; a consistently wrong judge
scores beautifully. Two families measure something closer to accuracy — and matter
specifically here, because Claude is one of the four systems under test. A Claude-only
judge would be grading a contestant from its own family, and self-preference bias in LLM
judges is documented (Zheng et al., 2023). `report.py` prints cross-family agreement
**per engine**, so if the Claude judge agrees with the Gemini judge markedly less on
Claude's own answers, it shows up as a number instead of a caveat.

**Blind human validation sits above both.** Two models agreeing is evidence of a shared
prior, not of truth. `judge_check.py --export` writes a worksheet containing:

- every answer where the two judges disagreed on tags — highest information per label
- every answer carrying a high-impact tag (`fabricated_metric`,
  `hallucinated_verification`, `conflated_identity`)
- a control group of answers **both judges called clean**, balanced across engines —
  without these you can only find false positives, never false negatives
- a hard cap on total items, with anything dropped printed explicitly

You label it without seeing the verdicts. `--score` then reports exact-set agreement and
mean Jaccard, per judge and per engine.

This is the same protocol as `src/judge_check.py` in
[crosssource](https://github.com/zoeb-nomi/crosssource), where blind labelling caught a
real harness bug that no amount of model agreement would have surfaced. **An unvalidated
judge makes every other table in the repo uncitable.**

## Resolution

`analyze.py` reports the within-prompt standard deviation — pure sampling noise — and
converts it into the smallest difference the design can resolve at roughly two standard
errors. Any before/after delta below that figure gets described as directional, not
significant, in the report itself rather than in a footnote a reader might miss.

## Known limitation: API is not the product

The harness probes provider APIs with native web search. ChatGPT-the-app has memory, a
different system prompt, and its own retrieval stack; so do the others. A manual
product-mode spot-check — the two highest-signal prompts through each consumer UI on the
same day — is recorded in `results/<wave>/product-mode.md`, and the API-versus-product gap
is reported as a number. Naming and measuring your construct-validity gap is the
difference between an eval and a demo.
