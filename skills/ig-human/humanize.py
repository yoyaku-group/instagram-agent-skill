#!/usr/bin/env python3
"""
humanize.py - strip the machine fingerprint out of a draft.

Three passes, in this order:

  1. INVISIBLE   delete or normalise the characters a human keyboard never
                 produces: zero-width joiners, word joiners, soft hyphens,
                 BOMs, Unicode tag characters, non-breaking and narrow spaces.
                 These survive copy-paste and are the most mechanical tell in
                 any generated text.
  2. TYPOGRAPHIC em dash -> comma, en dash -> hyphen, curly quotes -> straight,
                 ellipsis -> three dots, bullet -> hyphen.
  3. LEXICAL     replace the slop lexicon in slop.json with plain words,
                 preserving capitalisation and leaving URLs untouched.

Structural tells (rule-of-three, "not just X, it's Y", hashtag walls) are
REPORTED, never auto-rewritten - rewriting a sentence's shape needs judgement,
so that is the model's job, not a regex's.

Language
  French and English are handled natively: the punctuation cleanup respects
  French spacing (a space before ; : ! ? is correct French, not an artefact)
  and the lexical pass uses the French block of the lexicon. Before 2026-11-23
  the cleanup turned "Objet :" into "Objet:", ate a line-final comma and
  stripped the French entries that did not exist yet.

Usage
  python3 humanize.py draft.txt
  python3 humanize.py draft.txt --report
  pbpaste | python3 humanize.py - --report
  python3 humanize.py draft.txt --json
  python3 humanize.py draft.txt -o clean.txt
  python3 humanize.py draft.txt --lang fr      # force a language
"""

import argparse
import json
import os
import re
import sys
import unicodedata

HERE = os.path.dirname(os.path.abspath(__file__))
LEX = os.path.join(HERE, "slop.json")

URL_RE = re.compile(r"https?://\S+|www\.\S+|\S+@\S+\.\S+")
SENT_RE = re.compile(r"[^.!?\n]+[.!?]*")
WORD_RE = re.compile(r"[^\W\d_]+", re.UNICODE)

# Apostrophe variants are one character to a reader and three to a regex.
APOSTROPHES = "'’‘ʼ`"

_STOPWORDS = {
    "fr": set("""le la les des du une et est que qui pour dans sur avec pas
        nous vous ils elles ce cette ces au aux par plus mais ou donc car ne
        se son sa ses leur leurs on tout tous être avoir fait comme aussi
        chaque aucun""".split()),
    "en": set("""the of and to in is it that for with you we they this are
        was be as at but not have from your our on by an or if when
        what which there""".split()),
}


def load_lexicon(path=LEX):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def detect_language(text):
    """Return 'fr' or 'en'. Ties and empty input fall back to 'en'."""
    toks = [w.lower() for w in WORD_RE.findall(text)]
    if not toks:
        return "en"
    counts = {lang: sum(1 for t in toks if t in stops) for lang, stops in _STOPWORDS.items()}
    accented = sum(1 for t in toks if any(c in "àâäéèêëîïôöùûüÿçœæ" for c in t))
    counts["fr"] += accented * 2
    return "fr" if counts["fr"] > counts["en"] else "en"


def lexicon_for(lex, lang):
    """Shared entries plus this language's block. Never mix EN slop into FR."""
    block = lex.get(lang) or {}
    return (lex.get("words", []) + block.get("words", []),
            lex.get("phrases", []) + block.get("phrases", []),
            lex.get("structures", []) + block.get("structures", []))


def entry_pattern(find):
    """Boundary-correct, apostrophe-tolerant pattern for a lexicon entry.

    `\\b` is wrong on both ends for French: it never matches next to an
    elision ("n'hésitez"), and it silently fails when the entry itself opens
    on an apostrophe. Unicode-aware lookarounds do the same job and do not
    depend on the entry's first character being a letter.
    """
    esc = re.escape(find).replace(r"\ ", r"\s+")
    for ap in APOSTROPHES:
        esc = esc.replace(re.escape(ap), "[%s]" % re.escape(APOSTROPHES))
    if not esc:
        return None
    return r"(?<![^\W\d_])" + esc + r"(?![^\W\d_])"


def _cp(spec):
    """'U+200B' -> '\\u200b';  'U+E0000-U+E007F' -> (start, end)."""
    if "-" in spec:
        a, b = spec.split("-")
        return (int(a[2:], 16), int(b[2:], 16))
    return int(spec[2:], 16)


def protect_urls(text):
    """Swap URLs for placeholders so no pass rewrites inside a link."""
    found = []

    def stash(m):
        found.append(m.group(0))
        return f"\x00URL{len(found) - 1}\x00"

    return URL_RE.sub(stash, text), found


def restore_urls(text, found):
    for i, url in enumerate(found):
        text = text.replace(f"\x00URL{i}\x00", url)
    return text


def pass_invisible(text, lex):
    """Delete or space-normalise invisible characters. Returns (text, hits)."""
    hits = []
    for entry in lex["invisible"]:
        cp = _cp(entry["cp"])
        if isinstance(cp, tuple):
            pattern = "[" + re.escape(chr(cp[0])) + "-" + re.escape(chr(cp[1])) + "]"
        else:
            pattern = re.escape(chr(cp))
        n = len(re.findall(pattern, text))
        if n:
            hits.append({"name": entry["cp"] + " " + entry["name"], "count": n,
                         "action": entry["action"]})
            text = re.sub(pattern, "" if entry["action"] == "delete" else " ", text)
    # Any remaining Cf (format) character is invisible by definition.
    stray = [c for c in text if unicodedata.category(c) == "Cf"]
    if stray:
        hits.append({"name": "other invisible format chars", "count": len(stray),
                     "action": "delete"})
        text = "".join(c for c in text if unicodedata.category(c) != "Cf")
    return text, hits


def pass_typographic(text, lex, lang="en"):
    hits = []
    for entry in lex["typographic"]:
        ch = entry["from"]
        n = text.count(ch)
        if not n:
            continue
        hits.append({"name": f"{ch} {entry['name']}", "count": n, "to": entry["to"].strip() or "(space)"})
        if ch == "—":
            # " word — word " and "word—word" both collapse to a comma + space.
            text = re.sub(r"\s*—\s*", ", ", text)
        elif ch == "–":
            text = re.sub(r"\s*–\s*(?=\d)", "-", text)      # 5–10  -> 5-10
            text = re.sub(r"\s+–\s+", ", ", text)            # used as em dash
            text = text.replace("–", "-")
        else:
            text = text.replace(ch, entry["to"])
    # A comma inserted before existing punctuation reads wrong.
    text = re.sub(r",\s*([,.;])", r"\1", text)
    # French sets a space BEFORE ; : ! ? — never strip it there. English does
    # not, so the strip is confined to English. (This rule used to run on both
    # and rewrote "Objet :" as "Objet:".)
    if lang != "fr":
        text = re.sub(r"\s+([:!?])", r"\1", text)
    # A line-final comma is ordinary punctuation ("Merci,\nBenjamin"), not an
    # artefact. Only trailing whitespace is cleaned.
    text = re.sub(r"[ \t]+$", "", text, flags=re.MULTILINE)
    return text, hits


def _match_case(src, repl):
    if not repl:
        return repl
    if src.isupper() and len(src) > 1:
        return repl.upper()
    if src[0].isupper():
        return repl[0].upper() + repl[1:]
    return repl


def pass_lexical(text, lex, lang="en"):
    """Replace slop words and phrases. Longest first so phrases win."""
    hits = []
    words_l, phrases_l, _ = lexicon_for(lex, lang)
    entries = sorted(words_l + phrases_l, key=lambda e: len(e["find"]), reverse=True)
    for entry in entries:
        rx = entry_pattern(entry["find"])
        if not rx:
            continue
        pattern = re.compile(rx, re.IGNORECASE)
        found = pattern.findall(text)
        if not found:
            continue
        hits.append({"find": entry["find"], "replace": entry["replace"] or "(deleted)",
                     "count": len(found), "family": entry["family"]})
        text = pattern.sub(lambda m: _match_case(m.group(0), entry["replace"]), text)
    # Clean up after deletions. Deleting a whole clause leaves orphaned
    # punctuation behind ("system. ." or a line that now opens on a comma),
    # and that reads worse than the slop did.
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"(?m)^[ \t]*(?:[,.;:]+[ \t]*)+", "", text)
    text = re.sub(r"(?m)^[ \t](?=\S)", "", text)       # one space left by a deletion.
                                                      # Deeper indents are deliberate.
    text = re.sub(r"\s+([,.])", r"\1", text)
    if lang != "fr":
        text = re.sub(r"\s+([:!?])", r"\1", text)
    text = re.sub(r",\s*([,.;:!?])", r"\1", text)      # an em dash became a comma,
                                                      # then the clause after it went
    text = text.replace("...", "\x00ELL\x00")          # protect real ellipses
    text = re.sub(r"\.\s*\.+", ".", text)
    text = re.sub(r"([!?])\s*\.", r"\1", text)
    text = text.replace("\x00ELL\x00", "...")
    text = re.sub(r"(?m)^[ \t]+$", "", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    # An em dash that became a comma, followed by a sentence connective, leaves
    # a splice ("is important, also, it's proof"). Promote it to a full stop.
    text = re.sub(r",\s*(also|so|still|basically|in the end)\s*,\s*",
                  lambda m: ". " + m.group(1)[0].upper() + m.group(1)[1:] + ", ", text)
    return text, hits


def scan_structures(text, lex, lang="en"):
    flags = []
    _, _, structures = lexicon_for(lex, lang)
    for s in structures:
        try:
            pattern = re.compile(s["regex"], re.MULTILINE)
        except re.error:
            continue
        found = pattern.findall(text)
        if found:
            flags.append({"name": s["name"], "count": len(found), "fix": s["fix"]})
    # Sentence-length uniformity is structural too.
    lens = [len(s.split()) for s in SENT_RE.findall(text) if len(s.split()) > 2]
    if len(lens) >= 4:
        mean = sum(lens) / len(lens)
        var = sum((n - mean) ** 2 for n in lens) / len(lens)
        cv = (var ** 0.5) / mean if mean else 0
        if cv < 0.35:
            flags.append({
                "name": f"Uniform sentence length (variation {cv:.2f})",
                "count": len(lens),
                "fix": "Break one sentence in half. Let another run long. Machines write even.",
            })
    return flags


def restore_capitals(original, text):
    """Deleting an opener leaves the next word lower case.

    Only fix it for writers who capitalise their sentences in the first place:
    a deliberately lower-case voice is a style, not an artefact, and shouting
    over it would be exactly the kind of thing this script exists to stop.
    Accented capitals count - "À toutes les équipes" opens many French notes.
    """
    starts = re.findall(r"(?:^|[.!?]\s+|\n)\s*([^\W\d_])", original)
    if not starts or sum(1 for c in starts if c.isupper()) * 2 < len(starts):
        return text
    return re.sub(r"(?:^|(?<=[.!?] )|(?<=[.!?]\n)|(?<=\n))\s*([^\W\d_])",
                  lambda m: m.group(0)[:-len(m.group(1))] + m.group(1).upper(), text)


def humanize(text, lex, lang=None):
    lang = lang or detect_language(text)
    raw_for_case = text
    text, urls = protect_urls(text)
    text, inv = pass_invisible(text, lex)
    text, typo = pass_typographic(text, lex, lang)
    text, lexi = pass_lexical(text, lex, lang)
    text = restore_capitals(raw_for_case, text)
    text = restore_urls(text, urls)
    return text.strip() + "\n", {
        "language": lang,
        "invisible": inv,
        "typographic": typo,
        "lexical": lexi,
        "structures": scan_structures(text, lex, lang),
    }


def render_report(report, out=sys.stderr):
    def head(title):
        print(f"\n{title}\n" + "-" * len(title), file=out)

    total = sum(h["count"] for h in report["invisible"]) \
        + sum(h["count"] for h in report["typographic"]) \
        + sum(h["count"] for h in report["lexical"])

    head(f"HUMANIZE REPORT  [{report.get('language', 'en')}]")
    print(f"{total} machine artefacts removed, "
          f"{len(report['structures'])} structural tells flagged for rewrite", file=out)

    if report["invisible"]:
        head("1. INVISIBLE CHARACTERS")
        for h in report["invisible"]:
            print(f"  {h['count']:>3}x  {h['name']}  -> {h['action']}", file=out)
    if report["typographic"]:
        head("2. TYPOGRAPHY")
        for h in report["typographic"]:
            print(f"  {h['count']:>3}x  {h['name']}  -> {h['to']}", file=out)
    if report["lexical"]:
        head("3. SLOP LEXICON")
        for h in report["lexical"]:
            print(f"  {h['count']:>3}x  {h['find']}  -> {h['replace']}   [{h['family']}]", file=out)
    if report["structures"]:
        head("4. STRUCTURAL TELLS  (not auto-fixed - rewrite these yourself)")
        for h in report["structures"]:
            print(f"  {h['count']:>3}x  {h['name']}\n        {h['fix']}", file=out)
    if not (report["invisible"] or report["typographic"]
            or report["lexical"] or report["structures"]):
        head("CLEAN")
        print("  Nothing to strip.", file=out)
    print("", file=out)


def main():
    ap = argparse.ArgumentParser(description="Strip the machine fingerprint out of a draft.")
    ap.add_argument("input", nargs="?", default="-", help="file, or - for stdin")
    ap.add_argument("-o", "--out", help="write cleaned text here instead of stdout")
    ap.add_argument("--report", action="store_true", help="print what changed, to stderr")
    ap.add_argument("--json", action="store_true", help="emit {text, report} as JSON")
    ap.add_argument("--lang", choices=["en", "fr"], help="force a language instead of detecting")
    ap.add_argument("--lexicon", default=LEX, help="path to slop.json")
    args = ap.parse_args()

    raw = sys.stdin.read() if args.input == "-" else open(args.input, encoding="utf-8").read()
    lex = load_lexicon(args.lexicon)
    clean, report = humanize(raw, lex, args.lang)

    if args.json:
        print(json.dumps({"text": clean, "report": report}, indent=2, ensure_ascii=False))
        return
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(clean)
        print(f"wrote {args.out}", file=sys.stderr)
    else:
        sys.stdout.write(clean)
    if args.report:
        render_report(report)


if __name__ == "__main__":
    main()
