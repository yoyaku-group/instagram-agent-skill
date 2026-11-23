#!/usr/bin/env python3
"""
detect.py - a five-check panel that scores how machine-written a draft looks.

What this is:  five local heuristics modelled on the signals public AI
detectors actually measure - sentence-length variation, concreteness, stock
vocabulary, typographic fingerprint, and voice. Every score is computed on
your machine from the text alone. Nothing is uploaded.

What this is NOT:  GPTZero, Originality, Copyleaks, Winston or Turnitin.
It does not call their APIs and it cannot promise their verdict. It catches
the things they all key on, which is why fixing them tends to move their
numbers too - but the only honest claim is the one on this line.

Each check returns a HUMAN score from 0 to 100. Higher is better.

Language
  French and English are scored natively. The language is detected from the
  text and every check then uses that language's contractions, person markers,
  slop lexicon and structural patterns. A French draft is never scored against
  English heuristics - doing that pinned any French text at 36/100 regardless
  of how well it was written (measured 2026-11-23).

Usage
  python3 detect.py draft.txt
  pbpaste | python3 detect.py -
  python3 detect.py draft.txt --json
  python3 detect.py before.txt after.txt      # compare two drafts
  python3 detect.py draft.txt --lang fr       # force a language
"""

import argparse
import json
import os
import re
import statistics
import sys
import unicodedata

HERE = os.path.dirname(os.path.abspath(__file__))
LEX = os.path.join(HERE, "slop.json")

SENT_RE = re.compile(r"[^.!?\n]+[.!?]*")
# Unicode letters only: digits and underscores are not words, and `[A-Za-z]`
# splits "véritable" into "ritable", silently corrupting every density the
# panel computes on French text.
WORD_RE = re.compile(r"[^\W\d_]+", re.UNICODE)
NUMBERS = re.compile(r"\b\d[\d,.]*%?\b|\$\d")

# Concrete markers beyond digits and capitalised nouns: a domain, a URL, a
# filesystem path, a SKU-style identifier (ADR001), a snake_case field name.
# These are the most concrete things a text can carry, in any language.
CONCRETE = re.compile(
    r"\b(?:[a-z0-9][a-z0-9-]*\.)+[a-z]{2,}\b"   # domain.tld
    r"|https?://\S+"                             # url
    r"|(?<![\w/])/[\w./-]{3,}"                   # path
    r"|\b[A-Z]{2,}\d{2,}\b"                      # ADR001
    r"|\b[a-z]+(?:_[a-z]+)+\b"                   # snake_case_field
)

# Capitalised word not opening a sentence. Accent-aware both sides: French
# proper nouns and accented capitals were invisible to the ASCII version.
PROPER = re.compile(
    r"(?<![.!?]\s)(?<!^)\b[A-ZÀ-ÖØ-Þ][a-zß-öø-ÿ]{2,}\b", re.MULTILINE
)

# Language-specific voice signals.
CONTRACTIONS = {
    "en": re.compile(r"\b\w+'(?:s|t|re|ve|ll|d|m)\b", re.IGNORECASE),
    # French elision is grammar, not informality: it is deliberately NOT used
    # as a voice signal (see check_voice).
    "fr": re.compile(r"\b(?:c|j|l|m|n|s|t|d|qu)['’]", re.IGNORECASE),
}
PRONOUNS = {
    "en": re.compile(r"\b(i|me|my|mine|we|us|our|you|your)\b", re.IGNORECASE),
    # First and second person plus the collective "on". Third person is
    # excluded: it is as impersonal in French as in English.
    "fr": re.compile(
        r"\b(?:je|me|moi|mon|ma|mes|nous|notre|nos|vous|votre|vos|"
        r"on|tu|te|toi|ton|ta|tes)\b",
        re.IGNORECASE,
    ),
}

# Per-language calibration of the voice check.
#
# Measurement note (2026-11-23, this machine): the person and contraction
# components were falsified as authorship discriminants. Assistant prose
# carried MORE first/second person than human prose in both languages
# (EN 2.86 vs 0.34 per 100 words, FR 3.12 vs 1.44) and FAR more contractions
# (EN 1.92 vs 0.36). Upstream anchored person at human=8.0 on the premise that
# humans write "I/we"; on real French that pinned every human sample at 35.0
# (p10 = p25 = p50). Person density tracks chattiness, not causation, so it is
# not scored in French at all and is retained for English only in a weakened
# role.
#
# What survives as a real signal is the structural tells: the lexicon's own
# machine idioms. Their false positives are the all-caps dictation pattern
# (three shouted words in a row), which is a genuine slop marker in a caption
# and merely a dictation habit in a note - so `tells` carries the weight while
# the panel no longer pretends the other components prove authorship.
VOICE_WEIGHTS = {
    "en": {"contractions": 0.00, "person": 0.10, "tells": 0.90},
    "fr": {"contractions": 0.00, "person": 0.00, "tells": 1.00},
}
VOICE_ANCHORS = {
    # Inverted from upstream on this measurement: high person density is the
    # machine side here, so `machine` sits above `human`.
    "en": {"contractions": (3.0, 0.0), "person": (0.3, 3.0)},
    "fr": {"contractions": (6.0, 0.0), "person": (1.4, 3.1)},
}

# Sentence-length variation anchors, measured per language on this machine
# (2026-11-23): human p50 EN 0.70 / FR 0.63, machine p25 0.29 / 0.41.
# English keeps upstream's calibration; French gets its own because the
# English anchor scored a typical human French paragraph as machine.
BURSTINESS_ANCHORS = {
    "en": (0.70, 0.22),
    "fr": (0.63, 0.35),
}

# Concrete-marker density. This check asks one question: is the draft grounded
# in anything checkable? It is deliberately NOT a density contest, because the
# measurement says density does not separate authors - it separates registers.
#
# Measured 2026-11-23 on this machine, French, bucketed by length:
#   human p50 by length: 8.3 / 6.4 / 10.1 / 9.0 / 8.2 / 6.5 / 7.0 / 7.4
#   machine p50 by length: 3.9 / 5.7 / 9.1 / 10.5 / 9.8 / 9.7 / 10.6 / 10.1
# Machine prose overtakes human prose past ~110 words, because assistant text
# is dense with paths, snake_case fields and identifiers while a composed
# human note is not. Anchoring on the median therefore scored a careful memo
# as machine and a debugging transcript as human.
#
# So the anchor is the human LOWER QUARTILE (grounded -> 100) against the
# machine TENTH PERCENTILE, which is 0.0 in both languages: the most abstract
# machine text carries no concrete marker whatsoever. A draft is penalised for
# being abstract, not for being less dense than a chat log.
SPECIFICITY_ANCHORS = {
    "en": (8.4, 0.0),
    "fr": (5.3, 0.0),
}

# Language detection. Function words are the cheapest reliable signal; the
# accented-character count breaks ties and catches short drafts.
_STOPWORDS = {
    "fr": set("""le la les des du une et est que qui pour dans sur avec pas
        nous vous ils elles ce cette ces au aux par plus mais ou donc car ne
        se son sa ses leur leurs on tout tous être avoir fait comme aussi
        chaque aucun cette cest nest cest""".split()),
    "en": set("""the of and to in is it that for with you we they this are
        was be as at but not have from your our their on by an or if when
        what which there""".split()),
}


def clamp(n):
    return max(0.0, min(100.0, n))


def scale(value, human, machine):
    """Map value onto 0-100 where `human` -> 100 and `machine` -> 0."""
    if human == machine:
        return 50.0
    return clamp((value - machine) / (human - machine) * 100)


def sentences(text):
    return [s.strip() for s in SENT_RE.findall(text) if len(s.split()) > 2]


def words(text):
    return WORD_RE.findall(text)


def detect_language(text):
    """Return 'fr' or 'en'. Ties and empty input fall back to 'en'."""
    toks = [w.lower() for w in WORD_RE.findall(text)]
    if not toks:
        return "en"
    counts = {lang: sum(1 for t in toks if t in stops) for lang, stops in _STOPWORDS.items()}
    accented = sum(1 for t in toks if any(c in "àâäéèêëîïôöùûüÿçœæ" for c in t))
    counts["fr"] += accented * 2
    if counts["fr"] > counts["en"]:
        return "fr"
    return "en"


def check_burstiness(text, lang="en"):
    """Humans vary sentence length hard. Models write even."""
    lens = [len(s.split()) for s in sentences(text)]
    if len(lens) < 4:
        return 50.0, "too short to judge"
    mean = statistics.mean(lens)
    cv = statistics.pstdev(lens) / mean if mean else 0
    human, machine = BURSTINESS_ANCHORS[lang]
    score = scale(cv, human=human, machine=machine)
    return score, (f"variation {cv:.2f} across {len(lens)} sentences "
                   f"(want {human - (human - machine) * 0.4:.2f}+)")


def check_specificity(text, lang="en"):
    """Numbers, names, domains and concrete identifiers. Slop is abstract."""
    w = words(text)
    if len(w) < 25:
        return 50.0, "too short to judge"
    per100 = 100 / len(w)
    numbers = NUMBERS.findall(text)
    concrete = set(CONCRETE.findall(text))
    proper = set(PROPER.findall(text))
    hits = len(numbers) + len(concrete) + len(proper)
    density = hits * per100
    human, machine = SPECIFICITY_ANCHORS[lang]
    score = scale(density, human=human, machine=machine)
    if not hits:
        # The unambiguous machine signature: a paragraph with nothing
        # checkable in it at all. Worth saying outright.
        return 0.0, "no concrete marker at all - nothing checkable in the draft"
    detail = (f"{hits} concrete markers "
              f"({len(numbers)} numeric, {len(concrete)} identifier, "
              f"{len(proper)} name), {density:.1f} per 100 words "
              f"(want {human - (human - machine) * 0.4:.2f}+)")
    return score, detail


def _lexicon_for(lex, lang):
    """Shared entries plus this language's block. Never mix EN slop into FR."""
    block = lex.get(lang) or {}
    return (lex.get("words", []) + block.get("words", []),
            lex.get("phrases", []) + block.get("phrases", []),
            lex.get("structures", []) + block.get("structures", []))


def check_slop(text, lex, lang):
    """Stock vocabulary density against the lexicon."""
    w = words(text)
    if not w:
        return 50.0, "empty"
    words_l, phrases_l, _ = _lexicon_for(lex, lang)
    hits, found = 0, []
    for entry in words_l + phrases_l:
        pattern = re.compile(r"\b" + re.escape(entry["find"]).replace(r"\ ", r"\s+") + r"\b",
                             re.IGNORECASE)
        n = len(pattern.findall(text))
        if n:
            hits += n
            found.append(entry["find"])
    density = hits * 100 / len(w)
    score = scale(density, human=0.0, machine=4.0)
    detail = f"{hits} stock terms, {density:.1f} per 100 words"
    if found:
        detail += " (" + ", ".join(sorted(found)[:4]) + (", ..." if len(found) > 4 else "") + ")"
    return score, detail


def check_fingerprint(text, lang="en"):
    """Characters a phone keyboard does not produce."""
    invisible = sum(1 for c in text if unicodedata.category(c) == "Cf")
    em = text.count("—")
    curly = sum(text.count(c) for c in "‘’“”")
    ellip = text.count("…")
    nbsp = sum(text.count(c) for c in "   ")
    if lang == "fr":
        # A hard space before ; : ! ? » is correct French typography, not a
        # machine fingerprint. Counting it penalised well-set French.
        correct = len(re.findall(r"[   ]\s*(?=[;:!?»])|(?<=[«])[   ]", text))
        nbsp = max(0, nbsp - correct)
    total = invisible * 4 + em * 2 + curly + ellip + nbsp
    per1k = total * 1000 / max(len(text), 1)
    score = scale(per1k, human=0.0, machine=12.0)
    detail = (f"{invisible} invisible, {em} em dash, {curly} curly quote, "
              f"{ellip} ellipsis, {nbsp} hard space")
    return score, detail


def check_voice(text, lex, lang="en"):
    """Contractions, person, and the shapes models default to."""
    w = words(text)
    if len(w) < 25:
        return 50.0, "too short to judge"
    per100 = 100 / len(w)
    contractions = len(CONTRACTIONS[lang].findall(text)) * per100
    person = len(PRONOUNS[lang].findall(text)) * per100
    tells = 0
    names = []
    _, _, structures = _lexicon_for(lex, lang)
    for s in structures:
        # `caps-shout` is excluded from scoring, on measurement: it fired on
        # 702 human samples against 23 machine ones on this machine's corpora
        # (2026-11-23). Three capitalised words in a row is a dictation habit
        # and a caption emphasis, not evidence of machine authorship; scoring
        # it inverted the check. It stays in the lexicon for humanize.py.
        if s["id"] == "caps-shout":
            continue
        try:
            n = len(re.compile(s["regex"], re.MULTILINE).findall(text))
        except re.error:
            continue
        if n:
            tells += n
            names.append(s["id"])
    bullets = [len(b.split()) for b in re.findall(r"(?m)^\s*[-*•]\s+(.+)$", text)]
    uniform = (len(bullets) >= 3 and statistics.pstdev(bullets) < 1.6)
    c_anchor = VOICE_ANCHORS[lang]["contractions"]
    p_anchor = VOICE_ANCHORS[lang]["person"]
    wgt = VOICE_WEIGHTS[lang]
    # `scale` handles human < machine correctly (it is a plain linear map), and
    # the person anchor IS inverted here: machine prose is the chattier side.
    score = (scale(contractions, human=c_anchor[0], machine=c_anchor[1]) * wgt["contractions"]
             + scale(person, human=p_anchor[0], machine=p_anchor[1]) * wgt["person"]
             + clamp(100 - tells * 22) * wgt["tells"])
    if uniform:
        score -= 12
        names.append("uniform-bullets")
    detail = (f"{contractions:.1f} contractions, {person:.1f} personal pronouns "
              f"per 100 words, {tells} structural tell(s)")
    if lang == "fr":
        detail = (f"{person:.1f} personal pronouns per 100 words "
                  f"(elision not scored: it is grammar in French), "
                  f"{tells} structural tell(s)")
    if names:
        detail += " [" + ", ".join(names[:4]) + "]"
    return clamp(score), detail


CHECKS = ["BURSTINESS", "SPECIFICITY", "SLOP DENSITY", "FINGERPRINT", "VOICE"]


def run(text, lex, lang=None):
    lang = lang or detect_language(text)
    results = {}
    results["BURSTINESS"] = check_burstiness(text, lang)
    results["SPECIFICITY"] = check_specificity(text, lang)
    results["SLOP DENSITY"] = check_slop(text, lex, lang)
    results["FINGERPRINT"] = check_fingerprint(text, lang)
    results["VOICE"] = check_voice(text, lex, lang)
    scores = [results[c][0] for c in CHECKS]
    # The weakest check drags the verdict: a detector only needs one signal.
    overall = statistics.mean(scores) * 0.6 + min(scores) * 0.4
    verdict = "PASS" if overall >= 70 and min(scores) >= 55 else (
        "REVIEW" if overall >= 50 else "FLAGGED")
    return results, overall, verdict, lang


def bar(score, width=24):
    filled = round(score / 100 * width)
    return "#" * filled + "." * (width - filled)


def render(results, overall, verdict, label=None, lang=None, out=sys.stdout):
    title = "AI DETECTION PANEL" + (f"  -  {label}" if label else "")
    if lang:
        title += f"  [{lang}]"
    print("\n" + title, file=out)
    print("=" * max(len(title), 62), file=out)
    for name in CHECKS:
        score, detail = results[name]
        print(f"  {name:<13} {bar(score)} {score:5.1f}", file=out)
        print(f"  {'':<13} {detail}", file=out)
    print("-" * 62, file=out)
    print(f"  {'HUMAN SCORE':<13} {bar(overall)} {overall:5.1f}   {verdict}", file=out)
    if verdict != "PASS":
        weakest = min(CHECKS, key=lambda c: results[c][0])
        print(f"\n  Weakest signal: {weakest}. Fix that first.", file=out)
    print("", file=out)


def main():
    ap = argparse.ArgumentParser(description="Score how machine-written a draft looks.")
    ap.add_argument("input", nargs="?", default="-", help="file, or - for stdin")
    ap.add_argument("compare", nargs="?", help="second file, to show before/after")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--lang", choices=["en", "fr"], help="force a language instead of detecting")
    ap.add_argument("--lexicon", default=LEX)
    args = ap.parse_args()

    lex = json.load(open(args.lexicon, encoding="utf-8"))
    read = lambda p: sys.stdin.read() if p == "-" else open(p, encoding="utf-8").read()

    targets = [(args.input, read(args.input))]
    if args.compare:
        targets.append((args.compare, read(args.compare)))

    payload = []
    for name, text in targets:
        results, overall, verdict, lang = run(text, lex, args.lang)
        payload.append({
            "source": name,
            "language": lang,
            "checks": {k: {"score": round(v[0], 1), "detail": v[1]} for k, v in results.items()},
            "human_score": round(overall, 1),
            "verdict": verdict,
        })

    if args.json:
        print(json.dumps(payload if args.compare else payload[0], indent=2, ensure_ascii=False))
        return

    for (name, text), p in zip(targets, payload):
        results, overall, verdict, lang = run(text, lex, args.lang)
        render(results, overall, verdict,
               label=os.path.basename(name) if args.compare else None, lang=lang)
    if args.compare:
        a, b = payload
        delta = b["human_score"] - a["human_score"]
        print(f"  {a['human_score']:.1f} {a['verdict']}  ->  "
              f"{b['human_score']:.1f} {b['verdict']}   ({delta:+.1f})\n")

    sys.exit(0 if payload[-1]["verdict"] == "PASS" else 1)


if __name__ == "__main__":
    main()
