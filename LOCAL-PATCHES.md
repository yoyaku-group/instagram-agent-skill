# LOCAL PATCHES — instagram-agent-skill (YOYAKU vendored copy)

**Upstream:** https://github.com/Jakeschincariol/instagram-agent-skill
**Fork (origin):** https://github.com/yoyaku-group/instagram-agent-skill · patch branch: `yoyaku`
**Pinned commit:** `d03c56bb598be770c60b201f94237e5d1a4268a6` (2026-09-13, MIT, v1.0 + 1 fix)
**Vendored:** 2026-10-04 · **Patched:** 2026-10-04

This clone is the **engine** for the YOYAKU `ig-*` skill pack. All 13 wrappers live
as real directories in `~/.claude/skills/ig-*` (the canonical home), mirrored as
symlinks in `~/.commandcode/skills/` and `~/.agents/skills/` — never as real
copies there. The first six (`ig-reel|caption|human|audit|viral|dm`) were adapted
2026-10-04; the remaining seven (`ig-carousel|comment|plan|profile|reply|repurpose|story`)
were adapted 2026-11-22 on the same pattern: brand voice from the yoyaku-marketing
SSOT (`strategy/social-voice-*.md`), `/ig-human` + content-lint gates, one plain
ask that never names a comment word, nothing is posted. Registry SSOT for the
pack: `yoyaku-marketing/strategy/README.md` + this file. Remotes: **origin** = YOYAKU fork — branch `yoyaku` carries the pinned upstream plus this patch set; **upstream** = Jakeschincariol. Never push to upstream; refresh via the procedure below.

## Patch set (1)

**`skills/ig-human/slop.json`** — added entries to the `words` and `phrases` arrays:
- French slop: "n'hésitez pas à", "il est important de noter que", "force est de constater que", "à l'ère du numérique", "sans plus attendre", "ne manquez pas", "restez jusqu'à la fin", "abonnez-vous pour plus", "commentez ci-dessous", "l'algorithme adore", "en résumé", etc.
- YOYAKU no-AI/no-pricing block (rule 01 defence-in-depth): "propulsé par l'IA", "grâce à l'IA", "intelligence artificielle", "notre IA", "AI-powered", "powered by AI", "auto-generated".

No other file is patched. All six Python tools are upstream as-is (stdlib only — no network imports, audited 2026-10-04).

## Refresh procedure

```bash
cd ~/repos/instagram-agent-skill
git fetch upstream
git log --oneline yoyaku..upstream/main      # review what upstream changed
git diff yoyaku..upstream/main -- skills/    # scope the diff
# Rebase (or merge) branch `yoyaku` onto the new upstream pin, keep the patch
# set below on top, push origin yoyaku, then update the pinned SHA here and in
# the skill wrappers if a CLI flag changed (check add_argument in the script).
```
