# Mod brief: carry bounties on the wyvern

Community request: bounty targets can be tied up and loaded onto a horse, but
many bounties are across the map. Players want to load the bounty onto the
wyvern (Blackstar) and fly instead.

This document is written to be pasted into a fresh Claude Code session as the
starting brief. It is a *research-then-build* task: whether the mod is possible
at all is unresolved, and phase 1 is designed to answer that before any effort
goes into building.

---

## What is already known

**This is a game-files mod, not a save edit.** What a mount can carry is game
data or game code, not character state. It belongs with the mod-editor side of
the toolchain (the same machinery that patches Blackstar timers), not the save
editor.

**The game has a "docking" concept**, which is the mechanic for attaching one
object to another — exactly what "bounty tied to horse" is:

- `docking_child_data` appears as an item-info field. Upstream issue
  NattKh#64 is titled "mecha spear docking_child_data does not match the
  effect/gimmick dropdown", so the field is real, per-item, and already
  surfaced in a mod-editor dropdown.
- `crimson/game_mods/gui/tabs/buffs_v319.py` contains `_patch_docking_108`,
  called right after item-info parsing — so the existing tooling already
  rewrites docking data for at least one case.

**Relevant vanilla tables** (extractable from the install, see below):
`iteminfo`, `characterinfo`, `equipslotinfo`, `fieldinfo`, `buffinfo`.

**Hard prerequisite**: `docs/ITEMINFO_PARSER_HANDOFF.md` in this repo. The
bundled item-info parser cannot read a single game-1.15 record, so docking data
cannot currently be inspected on a current install. That investigation must
land first, and it is a real reverse-engineering task, not a quick fix.

---

## The question that decides everything

Is the horse-only restriction expressed as **data** or as **code**?

- **Data** (a docking table naming valid parent objects / mount types, or a
  per-mount capability flag) → the mod is a table edit, well within what the
  existing PAZ-patching pipeline does.
- **Code** (hardcoded in the executable, or driven by animation/rig
  constraints that only exist for the horse) → no data mod can achieve it, and
  the honest answer to the community is "not possible with current tooling".

Do not start building until phase 2 answers this. A negative answer is a
successful outcome for this project — publish the finding rather than shipping
something that half-works.

---

## Phase 0 — prerequisite

> Read `docs/ITEMINFO_PARSER_HANDOFF.md`. Get `iteminfo` parsing working
> against a current-patch (1.15) install, using the byte-diff approach it
> describes: parse the old `vanilla_tables/iteminfo.pabgb` (5,486,564 bytes,
> pre-change format) and the newly extracted one (5,938,891 bytes), find the
> same item key in both, and walk `parse_full_item`'s field sequence until the
> two diverge. Report every new or resized field you find, not just the first.
> `tools/probe_iteminfo_format.py` is the harness; set `CRIMSON_GAME_PATH`.

Success criterion: the whole file parses into a plausible item count, and a
named weapon's record round-trips.

## Phase 1 — locate the docking data

> With `iteminfo` parsing, dump every field of an item known to be dockable and
> an item known not to be. Start from `docking_child_data` — find its shape
> (scalar? list? references to other keys?) and what values it takes across the
> item table. Then find the bounty/captive object and the two mounts:
> - the tied-up bounty target (likely a field object or gimmick, not an
>   inventory item — check `fieldinfo` if `iteminfo` has no candidate)
> - the horse and Blackstar/wyvern mount definitions (`characterinfo` is the
>   likeliest home)
> Report which table each lives in and what the docking-related fields contain
> for each. Do not modify anything yet.

## Phase 2 — decide data vs code

> Compare the horse's and the wyvern's definitions field by field. Look
> specifically for: a docking/attachment point list, a carry-capability flag, a
> permitted-child-type list, or a socket/bone reference that exists on the
> horse and is absent on the wyvern.
>
> Then state a verdict explicitly:
> - **Data-driven** — name the exact field(s) that would need to change and
>   what to change them to.
> - **Code-gated** — say so plainly, with the evidence (e.g. both mounts carry
>   identical docking data, so the difference must live elsewhere).
> - **Unresolved** — say what evidence is missing and what would settle it.
>
> Do not guess. A wrong "yes" here wastes days downstream.

## Phase 3 — build the patch (only if phase 2 says data-driven)

> Produce the smallest possible edit: modify only the field(s) phase 2
> identified, in one table, for the wyvern only. Use the existing mod pipeline
> in this repo (`crimson/game_mods`) to build the overlay — do not invent a new
> patching mechanism. Keep the vanilla bytes for a revert.
>
> Requirements:
> - The patch must be reversible (ship the restore, and verify restoring gives
>   back a byte-identical vanilla table).
> - No other item or mount may change. Diff the patched table against vanilla
>   and show the changed byte ranges.
> - Fail loudly on a game version whose table layout does not match what the
>   patch expects, rather than writing to unknown offsets.

## Phase 4 — test

> In-game verification, in this order, with a backed-up save:
> 1. Game loads and reaches the world with the patch applied.
> 2. The wyvern behaves normally when *not* carrying anything (no regression).
> 3. A bounty can be attached — and check what it looks like: an attachment
>    that works logically but renders in the wrong place, or clips, or detaches
>    on flight, is a partial result worth reporting honestly rather than
>    shipping.
> 4. Delivering the bounty completes the mission and pays out.
> 5. Save, exit, reload — the state survives.
>
> Anything short of all five is a "not ready" result. Write down which step
> failed and what it looked like.

## Phase 5 — release

> - Separate repository, since this is a mod rather than an editor feature.
> - README: what it does, game version tested, install/uninstall steps, and an
>   explicit statement of what was verified in-game versus assumed.
> - Credit the toolchain it was built with (NattKh's editor lineage) and the
>   parser work it depends on.
> - Nexus page: state the tested game version prominently — a table-offset mod
>   is version-sensitive, and a game patch can silently break it. Say what
>   happens if it breaks (revert instructions) before anyone needs them.
> - Ship the revert path in the download, not just in the instructions.

---

## Stop conditions

Stop and report rather than pushing on, if:

- Phase 0 turns into open-ended reverse engineering with no convergence — the
  parser problem is worth solving on its own merits, but it is a prerequisite
  here, not the deliverable.
- Phase 2 says code-gated. Publish the finding; it saves the next person the
  same search.
- Phase 4 produces a partial result (attaches but renders wrong, or breaks on
  reload). A cosmetic-but-broken mod on Nexus generates bug reports forever.

## Honest unknowns

- Whether the bounty object is an item, a field gimmick, or an NPC — this
  changes which table matters.
- Whether flight animation or rigging would even support a carried object; a
  data flag might permit attachment that then looks wrong in the air.
- Whether the wyvern has an attachment socket at all. If the model has no bone
  for it, no data edit will help.
- Whether Pearl Abyss's terms permit distributing game-data patches. Worth
  checking before publishing, given a Nexus page is public.
