# LOCAL-PATCHES

What the YOYAKU fork changes against upstream
`Jakeschincariol/instagram-agent-skill` (MIT), and why. The engine lives in
`~/repos/instagram-agent-skill/skills/ig-human/`; this file is meant to be read
whenever someone wonders whether a local behaviour is upstream's or ours.

Upstream was pinned at `d03c56bb`. Everything below is a deliberate divergence,
and each one names the measurement that forced it.

## 1. The engine scores French natively (2026-11-23)

Upstream is English-only by construction. On French copy it did not degrade
gracefully - it failed silently and returned a constant 36/100 for any French
draft, whatever its quality. Four independent causes:

| fault | effect on French |
| --- | --- |
| `WORD_RE = [A-Za-z']+` | split `véritable` into `ritable`, corrupting every per-100-word density |
| English-only `CONTRACTIONS` / `PRONOUNS` | pinned VOICE at 30.0 |
| `PROPER` = ASCII capital + ASCII lowercase | accented French proper nouns never counted as names |
| `slop.json` had no `fr` block | zero French slop detection across 96 words / 58 phrases / 16 structures |

Now: `WORD_RE` is `[^\W\d_]+`, `PROPER` accepts `[A-ZÀ-ÖØ-Þ][a-zß-öø-ÿ]`,
there is a per-language `fr` block, and `detect_language()` routes every check
to the right language. English slop never fires on French copy.

## 2. Anchors come from measurement, not taste

Distributions were taken over this machine's own corpora on 2026-11-23, per
language and per author. Three upstream premises did **not** survive.

**SPECIFICITY is not a density contest.** Machine French overtakes human French
past ~110 words (p50 9.7 vs 9.0) because assistant prose is dense with paths,
snake_case fields and identifiers, while composed human French is not. Human
density by length bucket: 8.3 / 6.4 / 10.1 / 9.0 / 8.2 / 6.5 / 7.0 / 7.4.
Machine, same buckets: 3.9 / 5.7 / 9.1 / 10.5 / 9.8 / 9.7 / 10.6 / 10.1.
The anchor is therefore the human lower quartile against the machine tenth
percentile (0.0 in both languages): abstract text is penalised, dense chat logs
are not rewarded.

**VOICE's person and contraction components are inverted here.** Assistant prose
carried *more* first/second person than human prose (EN 2.86 vs 0.34, FR 3.12
vs 1.44 per 100 words) and far more contractions (EN 1.92 vs 0.36). Person
density tracks chattiness, not causation. It is not scored in French at all and
carries a 0.10 weight in English.

**`caps-shout` is not a tell.** It fired on 702 human samples against 23 machine
ones: three capitalised words in a row is a dictation habit and a caption
emphasis. It is excluded from scoring in `check_voice` but kept in the lexicon,
so `humanize.py` still normalises it.

Discrimination, same corpora, before and after:

| lang | author | PASS% before | after |
| --- | --- | --- | --- |
| en | human | 0.6% | 84.6% |
| en | machine | 3.5% | 14.1% |
| fr | human | n/a (pinned) | 68.5% |
| fr | machine | n/a (pinned) | 40.8% |

Before the recalibration the panel called human English *more* machine-like than
machine English. That was the defect that mattered most, and no amount of French
work would have revealed it.

## 3. `humanize.py` respects French typography

Upstream rewrote `Objet :` as `Objet:` and ate line-final commas
(`Merci,` → `Merci`). Both are correct French. The punctuation passes are now
language-aware: only `[,.;]` is collapsed, never `:!?`, and the strip before
`:!?` is confined to English. A hard space before `; : ! ? »` is correct French
typography, so `check_fingerprint` does not count it as a fingerprint.

## 4. The French lexicon is deliberately narrow

`slop.json` `fr` carries 39 words, 47 phrases and 11 structures. Ordinary French
business vocabulary is **excluded on purpose** - `accompagner`, `expertise`,
`performant` are how French business actually writes, and flagging them would
make the gate unusable. What is included is genuine filler: `véritable`,
`incontournable`, `robuste`, `il est important de noter que`,
`n'hésitez pas à`, `en constante évolution`, `un vrai game changer`.

Three French structure regexes were also broken on first write:
`(?:seulement|juste|qu')une?` demanded `seulementune`, so only the `qu'` branch
could ever match. They are now apostrophe-tolerant (`['’]`) and each alternative
carries its own spacing.

## 5. No "no-AI / no-pricing block"

The earlier `SKILL.md` claimed this file added one. It does not, and never did.
If pricing or AI-attribution rules are wanted in the lexicon, they belong in the
`phrases` array with a measurement showing they fire, not in a doc asserting
they exist.

## Testing a change

```bash
cd ~/repos/instagram-agent-skill/skills/ig-human
python3 detect.py <human-draft> <machine-draft>   # correct direction = human scores higher
python3 detect.py --lang fr <french-draft>
python3 -c "import json; json.load(open('slop.json'))"   # lexicon parses
```

A change to an anchor is only justified by a distribution, measured per
language and per author, and the discrimination table above must still hold.
