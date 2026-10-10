# D&D Skill — Scripts Reference

Full syntax for all Python helper scripts. Load this file once at `/dm:dnd load`, then it stays in context for the session.

> **Path note:** commands below use `${CLAUDE_SKILL_DIR}` for the skill directory. This file is read verbatim, so that token is **not** auto-expanded here — substitute the absolute skill-dir path (from `SKILL.md`) before running any command, or it will fail with a broken `/scripts/…` path.

---

## Dice Script — `scripts/dice.py`

**MANDATORY.** Every die roll in play — player checks, NPC attacks, saves, damage, ability score gen, anything — must be produced by invoking this script via Bash. **Never sample dice mentally or with inline `random` calls.** The script routes rolls through a local physical-dice server that may surface them on the player's phone for them to cast; rolling in your head bypasses that and breaks the ritual. If the server isn't running the script falls back to local random — so there is no scenario where the script should be skipped.

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/dice.py d20+5
python3 ${CLAUDE_SKILL_DIR}/scripts/dice.py 2d6+3
python3 ${CLAUDE_SKILL_DIR}/scripts/dice.py 4d6kh3        # ability score roll
python3 ${CLAUDE_SKILL_DIR}/scripts/dice.py d20 adv       # advantage
python3 ${CLAUDE_SKILL_DIR}/scripts/dice.py d20+3 dis     # disadvantage + modifier
python3 ${CLAUDE_SKILL_DIR}/scripts/dice.py d20 --silent  # returns integer only

# Always pass --label so the phone HUD shows what the roll is for:
python3 ${CLAUDE_SKILL_DIR}/scripts/dice.py d20+4 --label "Perception check"
python3 ${CLAUDE_SKILL_DIR}/scripts/dice.py d20+6 adv --label "Attack — Goblin Boss vs Piper"
python3 ${CLAUDE_SKILL_DIR}/scripts/dice.py 2d8+3 --label "Greataxe damage"

# Player rolls — pass --player <pc-name> to route to that player's phone tab:
python3 ${CLAUDE_SKILL_DIR}/scripts/dice.py d20+4 --label "Perception" --player piper
python3 ${CLAUDE_SKILL_DIR}/scripts/dice.py d20+6 adv --label "Attack" --player piper
# NPC / monster / DM-side rolls — omit --player (routes to the DM channel,
# which auto-rolls server-side if the DM has no tab open).
python3 ${CLAUDE_SKILL_DIR}/scripts/dice.py d20+5 --label "Goblin attack"
```

**Routing rule:** if the roll is **for a player character**, pass `--player <pc-name>` (lowercase, matches whatever name the player used in the URL). If the roll is for an NPC/monster/anything the DM resolves, omit `--player` so it doesn't ring the players' phones.

**Etiquette rule (important):** when invoking with `--player`, the player is not staring at their phone — they're listening to you narrate. **Always prompt them out loud before invoking**, so they pick up the phone. Pattern:

> *"Piper — make a Perception check. Cast it."*

Then run the command. The Bash call will block while the player picks up the phone, sees the prompt, and casts; the result returns to you afterward. Without the verbal prompt the player won't know to look, and the call will sit waiting for ~3 minutes before timing out into an auto-roll.

Flags nat 20 (CRITICAL HIT) and nat 1 (FUMBLE) automatically. If output contains `[auto]` the target's phone wasn't connected and the server rolled itself — no action needed, just narrate the result.

To force-skip the physical roller (e.g. high-volume NPC rolls you don't want to surface): `--auto` flag, or `DND_DICE_PHYSICAL=0 python3 ...`.

---

## Ability Scores Script — `scripts/ability-scores.py`
```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/ability-scores.py roll
python3 ${CLAUDE_SKILL_DIR}/scripts/ability-scores.py pointbuy
python3 ${CLAUDE_SKILL_DIR}/scripts/ability-scores.py pointbuy --check STR=15 DEX=10 CON=15 INT=8 WIS=11 CHA=12
python3 ${CLAUDE_SKILL_DIR}/scripts/ability-scores.py modifiers STR=15 DEX=10 CON=15 INT=8 WIS=11 CHA=12
```
Roll mode: generates 3 arrays (4d6kh3 × 6 each). Point buy mode: prints cost table; `--check` validates against the 27-point budget.

---

## XP Script — `scripts/xp.py`
Awards XP for combat and qualifying non-combat encounters. Reads character files from the campaign directory, updates XP, and pushes to the display sidebar. All tables (difficulty thresholds, CR→XP, monster multipliers, level advancement) are codified in the script — the DM only decides the difficulty tier or provides a monster list.

```bash
# Preview — no files modified:
python3 ${CLAUDE_SKILL_DIR}/scripts/xp.py calc --level 3 --players 2 --difficulty hard --type combat
python3 ${CLAUDE_SKILL_DIR}/scripts/xp.py calc --level 3 --players 2 --monsters "goblin:1/4:3,hobgoblin:1:1"

# Award after a combat encounter — difficulty-rated (use when full monster list is unavailable):
python3 ${CLAUDE_SKILL_DIR}/scripts/xp.py award \
  --campaign <name> --characters "Max of Thraxx,Ethros the 19th" --difficulty hard --type combat

# Award after a combat encounter — exact CR calculation (preferred for standard combats):
python3 ${CLAUDE_SKILL_DIR}/scripts/xp.py award \
  --campaign <name> --characters "Max of Thraxx,Ethros the 19th" \
  --monsters "goblin:1/4:3,hobgoblin:1:1" --note "Ambush in the alley"

# Award for a qualifying non-combat encounter:
python3 ${CLAUDE_SKILL_DIR}/scripts/xp.py award \
  --campaign <name> --characters "Max of Thraxx,Ethros the 19th" --difficulty medium --type noncombat \
  --note "guild informant interrogation"

# A PC whose player missed the session — same award, not counted in the split:
python3 ${CLAUDE_SKILL_DIR}/scripts/xp.py award \
  --campaign <name> --characters "Max of Thraxx,Ethros the 19th" --absent "Mara" \
  --difficulty hard --type combat
```

**Difficulty tiers:** `easy` `medium` `hard` `deadly`
**Encounter types:** `combat` `noncombat` (both use the same difficulty threshold table)
**Monster CR formats:** `1/4`, `0.25`, `1/2`, `0.5`, `1/8`, `0.125`, or integer (`1`, `5`, `10`)
**Monster count:** omit for 1 (e.g. `"dragon:10"`); explicit for groups (e.g. `"goblin:1/4:3"`)
**Monster multiplier** (applied automatically): ×1 (1), ×1.5 (2), ×2 (3–6), ×2.5 (7–10), ×3 (11–14), ×4 (15+)

`award` updates the character file XP field, flags LEVEL UP PENDING if a threshold is crossed, and pushes XP to the display via `push_stats.py`. The `--note` label prints to terminal only — not stored.

---

## Encounter Builder — `scripts/encounter.py`

Sizes a fight for the PCs who are actually present, with the 5e encounter budget done by the script.

```bash
# The fiction says who is there — how many of each make it hard for this party?
python3 ${CLAUDE_SKILL_DIR}/scripts/encounter.py build --campaign $CAMP --difficulty hard --with hobgoblin --with goblin

# Nothing decided yet — a few differently shaped groups to choose from
python3 ${CLAUDE_SKILL_DIR}/scripts/encounter.py build --campaign $CAMP --difficulty medium --type undead
python3 ${CLAUDE_SKILL_DIR}/scripts/encounter.py build --party 5,5,4,4 --difficulty deadly --type fiend,undead --options 5

# How hard is this group?
python3 ${CLAUDE_SKILL_DIR}/scripts/encounter.py rate --campaign $CAMP --monsters "goblin:4,hobgoblin"
```

- **Party:** `--campaign` reads the levels from the sheets and leaves out the PCs in `absent:`; or `--party 3,3,2`; or `--level 3 --players 4`.
- **`--with NAME`** (repeatable, Italian names understood): the script finds the counts. If those creatures cannot make that difficulty it prints the closest group and says so.
- **Without `--with`:** one creature, a pair, a pack, a leader with followers — each option of a single creature type. `--type` narrows the types, `--exclude dragon,lich` keeps names out, `--max-cr N` caps one creature (default: party level + 1), `--seed` repeats the draw.
- Every result prints the XP maths, the party's four thresholds, the `--monsters "…"` string for `xp.py award` and the combatants (AC, HP, DEX) ready for `combat.py init` — add the PCs to that list.
- Names come out in the campaign's language (`language:` Session Flag, or `--lang it`).

**When to use it:** before any fight you are preparing or improvising — choose the creatures for the story, let the script choose the numbers. Pick the difficulty from the scene and the `difficulty` table dial, not from habit: most fights medium, a hard one where the stakes are, deadly rarely and telegraphed. The budget ignores terrain, surprise and a party that is already hurt: shift one step down when the PCs are low on resources. Read the stat block with `lookup.py monster` before running a creature you have not used.

---

## Combat Script — `scripts/combat.py`
```bash
# Roll initiative and print tracker
python3 ${CLAUDE_SKILL_DIR}/scripts/combat.py init '<JSON>'
# JSON: [{"name":"Flerb","dex_mod":0,"hp":12,"ac":16,"type":"pc"}, ...]

# The players roll their own on the display; monsters are rolled hidden; the tracker starts by itself
python3 ${CLAUDE_SKILL_DIR}/scripts/combat.py init '<JSON>' --phones [--timeout 60]

# Reprint tracker from saved state
python3 ${CLAUDE_SKILL_DIR}/scripts/combat.py tracker '<JSON>' <round_num>

# Resolve a single attack
python3 ${CLAUDE_SKILL_DIR}/scripts/combat.py attack --atk 4 --ac 15 --dmg 2d6+2
```
`init` outputs `STATE_JSON:` line — store in `state.md` under `## Active Combat` between turns.

**`--phones`** opens an initiative roll on the display and waits. A browser bound to a character (PIN login or `?char=`) shows that player a big **Roll d20 +N** button and buzzes; the table screen lists the PCs with a small button beside each one still to roll, for players without a phone. Every roll is made by the server and appears to all as it lands; the monsters' rolls stay hidden until the end. When the last PC is in — or `--timeout` seconds have passed, and the missing are rolled for — the order is shown on every screen, written in the story as one line (`⚔ Lio 19 · Goblin 1 15 · …`), and the script pushes `--turn-order` itself (music, "your turn" on the first player's phone). Ties: higher modifier first, then the PC.
- `"init_mod": N` on a combatant replaces `dex_mod` as the initiative bonus (Alert, Jack of All Trades…).
- If the display is not running the script says so and rolls for everyone as without `--phones`.
- `python3 ${CLAUDE_SKILL_DIR}/display/initiative.py cancel` closes a roll nobody will finish; `status` shows it.

---

## Character Script — `scripts/character.py`
```bash
# Full stat block from raw scores
python3 ${CLAUDE_SKILL_DIR}/scripts/character.py calc --class fighter --level 1 \
    STR=15 DEX=10 CON=15 INT=9 WIS=11 CHA=14 \
    --proficient STR CON Athletics Intimidation Perception Survival

# Level-up HP and bonus calculation
python3 ${CLAUDE_SKILL_DIR}/scripts/character.py levelup --class fighter --from 1 --hp-roll 7 --con-mod 2

# XP tracking
python3 ${CLAUDE_SKILL_DIR}/scripts/character.py xp --level 1 --gained 150
```

---

## Inventory Script — `scripts/inventory.py`

Coins and items on the character sheet (`## Equipment & Inventory`), **applied by the script, not remembered**: it makes change, refuses a purchase the purse cannot cover, and exits non-zero without touching the file when a name or an item does not match. It also pushes the change to the display. Never edit the `**Currency:**` line or an item list by hand.

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/inventory.py show  "Aldric"
python3 ${CLAUDE_SKILL_DIR}/scripts/inventory.py gain  "Aldric" 12gp 5sp            # found, earned, rewarded
python3 ${CLAUDE_SKILL_DIR}/scripts/inventory.py pay   "Aldric" 3sp                 # toll, bribe, a round of ale
python3 ${CLAUDE_SKILL_DIR}/scripts/inventory.py add   "Aldric" "Chiave di ferro"   # picked up
python3 ${CLAUDE_SKILL_DIR}/scripts/inventory.py add   "Aldric" "Pozione di guarigione" --qty 2
python3 ${CLAUDE_SKILL_DIR}/scripts/inventory.py add   "Aldric" "Spada corta" --section weapons   # weapons | armour | gear
python3 ${CLAUDE_SKILL_DIR}/scripts/inventory.py remove "Aldric" "Pozione di guarigione"          # drunk, lost, broken
python3 ${CLAUDE_SKILL_DIR}/scripts/inventory.py buy   "Aldric" "Lanterna schermabile" --price 5gp   # pays and adds, or neither
python3 ${CLAUDE_SKILL_DIR}/scripts/inventory.py sell  "Aldric" "Spada corta" --price 5gp
python3 ${CLAUDE_SKILL_DIR}/scripts/inventory.py give  "Aldric" "Mira" --item "Chiave di ferro"
python3 ${CLAUDE_SKILL_DIR}/scripts/inventory.py give  "Aldric" "Mira" --coins 10gp
python3 ${CLAUDE_SKILL_DIR}/scripts/inventory.py split 137gp 4sp --among "Aldric,Mira,Lio"       # loot in equal shares
```

- Coins: `pp gp ep sp cp` (Italian `mp mo me ma mr` are read too). `--price` is the price of **one**; `--qty N` buys or sells N.
- Several of an item are written `Name ×N`; adding one more of an item already on the sheet raises the count.
- `remove` / `sell` / `give --item` match the exact name first, then a unique part of it (`"pozione"`). An ambiguous or missing match changes nothing and lists the candidates — pick one and run it again.
- `split` reports any copper left over; hand it to someone with `gain`.
- `--campaign NAME` (before the subcommand) works on a campaign other than the active one.

---

## Treasure — `scripts/loot.py`

Random treasure by challenge rating, handed out through `inventory.py` so it lands on the sheets.

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/loot.py roll --cr 1/4 --monsters 4          # what four goblins carried
python3 ${CLAUDE_SKILL_DIR}/scripts/loot.py roll --cr 3 --hoard                 # a lair, a vault, a real chest
python3 ${CLAUDE_SKILL_DIR}/scripts/loot.py roll --level 5 --hoard              # by party level, with no CR to go by
python3 ${CLAUDE_SKILL_DIR}/scripts/loot.py set --coins 340gp 20sp --item "Chiave d'argento" --item "Granato (100 mo) x2"
python3 ${CLAUDE_SKILL_DIR}/scripts/loot.py show
python3 ${CLAUDE_SKILL_DIR}/scripts/loot.py take --among "Aldric,Mira,Lio" --item "2:Mira" --item "3:Lio"
python3 ${CLAUDE_SKILL_DIR}/scripts/loot.py take --to "Aldric"                  # everything left, to one character
python3 ${CLAUDE_SKILL_DIR}/scripts/loot.py drop                                # the party leaves it there
```

- `roll` prints the pile numbered and keeps it in `<campaign>/loot.json`; nothing is on a sheet until `take`. `set` does the same for a treasure the adventure prescribes.
- `take`: `--among` splits the coins in equal shares (the odd copper goes to the first name); `--item N:NAME` gives numbered item N (`N:NAME:2` = two of a stack); `--items-to NAME` gives every item left; `--to NAME` gives everything. What nobody took stays for a later `take`. The same options on `roll` / `set` hand it out at once.
- Individual treasure is coins only; a hoard adds gems or art objects (written with their value, to be sold with `inventory.py sell`) and magic items drawn from the SRD by rarity — a spell scroll comes with its spell. Four tiers: CR 0–4, 5–10, 11–16, 17+.
- Names are written in the campaign's language: the `language:` Session Flag in `state.md` (e.g. `- language: it`), or `--lang it`. `--seed` repeats a roll. `--campaign NAME` (before the subcommand) works on a campaign other than the active one.
- A wrong name or number changes nothing and says why.

**How to use it:** roll, narrate what the characters find (look a magic item up with `lookup.py` before describing it — unidentified items are described, not named), let the players say who takes what, then `take`. Do not roll for every kill: individual treasure for creatures that would carry coins, a hoard at the end of a lair or an adventure node. If a rolled item does not fit the place or would unbalance the party, `drop` and `set` what does.

---

## Stats Display Script — `display/push_stats.py`
Pushes character and combat stats to the sidebar. Players merged by name; partial updates work.

```bash
# Full stats push (on /dm:dnd load — use --replace-players to clear stale characters):
python3 ${CLAUDE_SKILL_DIR}/display/push_stats.py --replace-players --json '{
  "players": [{
    "name": "Flerb", "race": "Tiefling", "class": "Fighter", "level": 1, "background": "Soldier",
    "hp": {"current": 12, "max": 12, "temp": 0},
    "xp": {"current": 220, "next": 300},
    "ac": 16, "initiative": "+0", "speed": 30,
    "hit_dice": {"remaining": 1, "max": 1, "die": "d10"},
    "second_wind": true,
    "ability_scores": {
      "str": {"score": 15, "mod": "+2"}, "dex": {"score": 10, "mod": "+0"},
      "con": {"score": 15, "mod": "+2"}, "int": {"score": 9, "mod": "-1"},
      "wis": {"score": 11, "mod": "+0"}, "cha": {"score": 14, "mod": "+2"}
    },
    "sheet": {
      "attacks": [
        {"name": "Longsword", "bonus": "+4", "damage": "1d8+2", "type": "Slashing", "notes": "Versatile (1d10)"},
        {"name": "Handaxe",   "bonus": "+4", "damage": "1d6+2", "type": "Slashing", "notes": "Thrown 20/60 ft"}
      ],
      "spells": null,
      "features": [
        {"name": "Second Wind",  "text": "Bonus action: regain 1d10+level HP. Recharges on short/long rest."},
        {"name": "Action Surge", "text": "Once per rest: take an additional action on your turn."}
      ],
      "inventory": ["Longsword", "Handaxe ×2", "Chain Mail", "Shield", "Explorer'\''s Pack", "15 gp"]
    }
  }]
}'

# sheet sub-keys: attacks, spells ({slots, save_dc, attack_bonus, cantrips, prepared} or null),
# features ([{name, text}]), inventory ([strings])
# sheet is optional — omit if you only need the stats sidebar without the full sheet modal

# Partial updates (use whenever values change mid-session):
python3 ${CLAUDE_SKILL_DIR}/display/push_stats.py --player Flerb --hp 7 12
python3 ${CLAUDE_SKILL_DIR}/display/push_stats.py --player Flerb --xp 220 300
python3 ${CLAUDE_SKILL_DIR}/display/push_stats.py --player Flerb --second-wind false

# Temp HP (Symbiotic Entity, Aid, etc.):
python3 ${CLAUDE_SKILL_DIR}/display/push_stats.py --player Flerb --temp-hp 8   # set
python3 ${CLAUDE_SKILL_DIR}/display/push_stats.py --player Flerb --temp-hp 0   # clear

# Hit dice (short rest):
python3 ${CLAUDE_SKILL_DIR}/display/push_stats.py --player Flerb --hit-dice-use          # spend one
python3 ${CLAUDE_SKILL_DIR}/display/push_stats.py --player Flerb --hit-dice-restore 2    # restore N

# Conditions — full replace:
python3 ${CLAUDE_SKILL_DIR}/display/push_stats.py --player Flerb --conditions "Poisoned,Frightened"
python3 ${CLAUDE_SKILL_DIR}/display/push_stats.py --player Flerb --conditions ""          # clear all

# Conditions — granular (preferred mid-session):
python3 ${CLAUDE_SKILL_DIR}/display/push_stats.py --player Flerb --conditions-add "Poisoned"
python3 ${CLAUDE_SKILL_DIR}/display/push_stats.py --player Flerb --conditions-add "Poisoned:3r"   # ends by itself: 3r / 10m / 1h
python3 ${CLAUDE_SKILL_DIR}/display/push_stats.py --player Flerb --conditions-remove "Poisoned"

# Death saves — the track starts by itself at 0 HP and ends with any healing; the player rolls from the phone
python3 ${CLAUDE_SKILL_DIR}/display/push_stats.py --player Flerb --death-save failure   # hit while down
python3 ${CLAUDE_SKILL_DIR}/display/push_stats.py --player Flerb --death-save crit      # critical hit: two failures
python3 ${CLAUDE_SKILL_DIR}/display/push_stats.py --player Flerb --death-save stable    # Spare the Dying, Medicine DC 10
python3 ${CLAUDE_SKILL_DIR}/display/push_stats.py --player Flerb --death-save reset     # also: start, success

# Concentration:
python3 ${CLAUDE_SKILL_DIR}/display/push_stats.py --player Flerb --concentrate "Bless"
python3 ${CLAUDE_SKILL_DIR}/display/push_stats.py --player Flerb --concentrate ""        # clear

# Spell slots — full replace (on /dm:dnd load):
python3 ${CLAUDE_SKILL_DIR}/display/push_stats.py --player Flerb \
  --spell-slots '{"1":{"used":1,"max":4},"2":{"used":0,"max":2}}'

# Spell slots — granular (preferred mid-session):
python3 ${CLAUDE_SKILL_DIR}/display/push_stats.py --player Flerb --slot-use 1      # expend one 1st-level slot
python3 ${CLAUDE_SKILL_DIR}/display/push_stats.py --player Flerb --slot-restore 2  # restore one 2nd-level slot

# Inventory — granular (preferred to full --sheet rewrite):
python3 ${CLAUDE_SKILL_DIR}/display/push_stats.py --player Flerb --inventory-add "Iron key"
python3 ${CLAUDE_SKILL_DIR}/display/push_stats.py --player Flerb --inventory-remove "Folded paper"

# Faction standings (party-wide — REQUIRED at /dm:dnd load to show faction panel):
python3 ${CLAUDE_SKILL_DIR}/display/push_stats.py \
  --factions '[{"name":"Pale Court","standing":"Allied"},{"name":"Watch","standing":"Neutral"}]'
python3 ${CLAUDE_SKILL_DIR}/display/push_stats.py --factions '[]'   # clear all

# Combat turn order (on /dm:dnd combat start):
python3 ${CLAUDE_SKILL_DIR}/display/push_stats.py --turn-order \
  '{"order":["Goblin 1","Flerb","Goblin 2"],"current":"Goblin 1","round":1}'

# Advance turn pointer:
python3 ${CLAUDE_SKILL_DIR}/display/push_stats.py --turn-current "Flerb"

# New round:
python3 ${CLAUDE_SKILL_DIR}/display/push_stats.py --turn-current "Goblin 1" --turn-round 2

# Combat ended:
python3 ${CLAUDE_SKILL_DIR}/display/push_stats.py --turn-clear

# Where the party is — background, particles and background music for everyone.
# One of: tavern city castle temple forest mountain desert swamp ocean mine cave
#         dungeon ruins crypt fire arcane night
python3 ${CLAUDE_SKILL_DIR}/display/push_stats.py --scene tavern

# World time clock — only for a campaign with no calendar.json; otherwise calendar.py sends it (see Calendar Script):
python3 ${CLAUDE_SKILL_DIR}/display/push_stats.py --world-time \
  '{"date":"19 Ashveil 1312 AR","day_name":"Moonday","time":"morning","season":"Long Hollow","weather":"calm"}'

# Clear display (use push_stats.py, NOT curl — raw curl lacks the auth token in LAN mode):
python3 ${CLAUDE_SKILL_DIR}/display/push_stats.py --clear

# Autorun cycle countdown (shown in party input panel):
python3 ${CLAUDE_SKILL_DIR}/display/push_stats.py --autorun-waiting true --autorun-cycle 60
python3 ${CLAUDE_SKILL_DIR}/display/push_stats.py --autorun-waiting false   # hide after turn resolves

# N-player threshold — auto-fire when N players (not all) are ready:
python3 ${CLAUDE_SKILL_DIR}/display/push_stats.py --autorun-threshold 2   # fire when 2 ready
python3 ${CLAUDE_SKILL_DIR}/display/push_stats.py --autorun-threshold 0   # reset to player count
```

**Player input queue — `display/check_input.py`:**
```bash
# Called at the start of each turn BEFORE processing the player's message.
# Drains any actions queued from the display companion (e.g. iPad) and prints them.
# Output: "[Max of Thraxx]: I draw my rapier" — empty if nothing queued. Clears the display indicator.
python3 ${CLAUDE_SKILL_DIR}/display/check_input.py
```

If `check_input.py` returns output, prepend it to the player's terminal input when forming the turn:
- Only queued input: treat as the full player action this turn
- Queued input + terminal input: merge as `[Character]: <queued>\n[Character]: <terminal>`
- Empty queue: proceed as normal (use only terminal input)

---

**When to push stats:**
- `/dm:dnd load` → `--replace-players --json` (full stats) + `--spell-slots` + `--factions` + `--scene` (where the party is), then `calendar.py -c <campaign> push` for the clock and the calendar (`--world-time` by hand only when the campaign has no calendar)
- Party arrives somewhere new (inn, town, castle, mine, crypt, forest, ship…) → `--scene NAME`, before narrating the arrival. It sets the music too, so push it every time the place really changes — not for a room inside the same place. Combat music is automatic with `--turn-order` / `--turn-clear`.
- HP change → `--player NAME --hp <current> <max>`
- Temp HP gained/lost → `--player NAME --temp-hp N` (0 to clear)
- XP awarded → `--player NAME --xp <current> <next>`
- Second Wind used/recovered → `--player NAME --second-wind false/true`
- Hit die spent → `--player NAME --hit-dice-use`; restored → `--hit-dice-restore N`
- Spell slot used → `--player NAME --slot-use <level>`; restored → `--slot-restore <level>`
- Condition gained → `--player NAME --conditions-add "Name"`; removed → `--conditions-remove "Name"`
- Condition with a set length ("until the end of its next turn", "for 1 minute") → add it as `"Name:1r"` / `"Name:10r"` / `"Name:10m"` / `"Name:1h"`. The pill shows what is left; rounds go down each time the tracker reaches that character (`--turn-current`), minutes and hours run on the clock, and at zero the display removes the condition and announces it. A save that ends it early is still yours: `--conditions-remove`. Sending the same turn twice does not count twice
- Concentration started → `--player NAME --concentrate "Spell"`; ended → `--concentrate ""`
- PC at 0 HP → just push the HP: the death-save track appears on their card and their phone gets the roll on their turn. Hit while down → `--death-save failure` (`crit` on a critical hit); stabilised → `--death-save stable`; healed → push the HP, the track goes away. See SKILL.md → *Death saving throws*
- Item picked up → `scripts/inventory.py add NAME "Item"`; dropped/used → `inventory.py remove NAME "Item"` (they update the sheet file and the display together; the bare `push_stats.py --inventory-add/--inventory-remove` changes the display only)
- Coins gained or spent → `scripts/inventory.py gain / pay / buy / sell / split`
- Timed effect starts → `--effect-start "NAME:SPELL:DURATION[:conc]"` bundled with narration send
- Timed effect ends → `--effect-end "NAME:SPELL"` bundled with narration send
- Faction standing changes → `--factions '[...]'` (full replace)
- Combat start → `--turn-order`; each turn → `--turn-current`; end → `--turn-clear`. Push `--turn-current` the moment a turn starts, not after narrating it: it is what buzzes that player's phone ("your turn") and warns the next one.
- Level up → push updated full stats
- Long rest → restore HP, hit dice, spell slots, second wind; `calendar.py rest long` moves the clock
- Any rest or time advance → `calendar.py advance / rest / time` — it updates the display's clock and calendar itself

**Keep the clock honest.** The world clock is continuity, not decoration. Narrate consistently with the time you last pushed, and advance it *deliberately* when an action costs time — a brief exchange is a few minutes, a search or a shopping trip is longer, a rest or a journey longer still. Never let the time of day drift on its own or silently reset between scenes. If the clock and the fiction ever disagree, reconcile it to the truth with `calendar.py set` / `time` rather than compounding the error; setting it *backward* to the correct time is fine and undoes nothing, since timed effects run on their own round/minute/hour durations in `tracker.py`, independent of the wall clock.

---

## Shop — `display/shop.py`

Put a shop's price list on the players' screens. Each player buys from their own phone (tap **Buy**, then **Confirm**) and the display applies the purchase at once with `inventory.py`: coins off the sheet with change, item on it, stock down. Nothing to add up, nothing to remember.

```bash
# One item per line:  name | price | stock | note      (stock and note optional)
python3 ${CLAUDE_SKILL_DIR}/display/shop.py open "Emporio di Brakka" --keeper "Brakka" --note "Odore di spezie" << 'DNDEND'
Corda di canapa (15 m) | 1gp
Pozione di guarigione | 50gp | 3 | sigillo di cera rossa
Razioni (1 giorno) | 5sp
[weapons] Spada corta | 10gp | 1
[armour] Scudo | 10gp
DNDEND

python3 ${CLAUDE_SKILL_DIR}/display/shop.py show     # what is on sale now, with the stock left
python3 ${CLAUDE_SKILL_DIR}/display/shop.py close    # when the party leaves
```

- Prices in `pp gp ep sp cp` (Italian `mp mo me ma mr` too). No stock = it never runs out. `[weapons]` / `[armour]` in front of a name files the item in that list of the buyer's sheet.
- Use SRD prices as the baseline and move them for the place (frontier markup, a grateful merchant's discount). To change a price or restock, `open` the shop again with the new list — it replaces the old one.
- **What was sold** reaches you with the next `check_input.py` / `autorun_wait.py` as `[[SHOP: Aldric bought Corda di canapa (15 m) for 1gp at "Emporio di Brakka" — already paid and added to the sheet …]]`. It is information, not an action: **do not charge or add it again**; mention it in the narration only if it matters.
- Haggling, selling to the merchant, and anything not on the list are ordinary actions: play them out, then apply the result with `inventory.py buy --price … / sell --price …`.
- Open a shop when the party walks into one and wants to look at the goods; close it when they leave. Keep the list to what this shop would really stock (10–20 lines), not the whole equipment chapter.

---

## Tracker Script — `scripts/tracker.py`
Tracks conditions, concentration, timed effects, and death saves. State persists at `~/.claude/dnd/campaigns/<name>/tracker.json`.

```bash
CAMP=my-campaign

# Timed effects — duration: 10r (rounds), 60m (minutes), 8h (hours), indef
# Append 'conc' to mark as concentration (auto-sets concentration field)
python3 ${CLAUDE_SKILL_DIR}/scripts/tracker.py -c $CAMP effect start "Max of Thraxx" "Web" 10r conc
python3 ${CLAUDE_SKILL_DIR}/scripts/tracker.py -c $CAMP effect start "Ethros the 19th" "Disguise Self" 1h
python3 ${CLAUDE_SKILL_DIR}/scripts/tracker.py -c $CAMP effect start "Ethros the 19th" "Hunter's Mark" indef
python3 ${CLAUDE_SKILL_DIR}/scripts/tracker.py -c $CAMP effect end   "Max of Thraxx" "Web"   # narrative end (broken/dispelled)
python3 ${CLAUDE_SKILL_DIR}/scripts/tracker.py -c $CAMP effect tick  "Max of Thraxx"         # call on actor's turn — decrements rounds, prints expiry

# Conditions — an optional duration (3r / 10m / 1h) makes it end by itself on `effect tick`
python3 ${CLAUDE_SKILL_DIR}/scripts/tracker.py -c $CAMP condition add "Ethros the 19th" poisoned
python3 ${CLAUDE_SKILL_DIR}/scripts/tracker.py -c $CAMP condition add "Ethros the 19th" frightened 3r
python3 ${CLAUDE_SKILL_DIR}/scripts/tracker.py -c $CAMP condition remove "Ethros the 19th" poisoned
python3 ${CLAUDE_SKILL_DIR}/scripts/tracker.py -c $CAMP condition clear "Ethros the 19th"

# Concentration (auto-clears previous if switching spells)
python3 ${CLAUDE_SKILL_DIR}/scripts/tracker.py -c $CAMP concentrate "Max of Thraxx" "Bless"
python3 ${CLAUDE_SKILL_DIR}/scripts/tracker.py -c $CAMP concentrate "Max of Thraxx" break

# Death saves
python3 ${CLAUDE_SKILL_DIR}/scripts/tracker.py -c $CAMP saves "Ethros the 19th" success
python3 ${CLAUDE_SKILL_DIR}/scripts/tracker.py -c $CAMP saves "Ethros the 19th" failure
python3 ${CLAUDE_SKILL_DIR}/scripts/tracker.py -c $CAMP saves "Ethros the 19th" stable
python3 ${CLAUDE_SKILL_DIR}/scripts/tracker.py -c $CAMP saves "Ethros the 19th" reset

# Status / clear
python3 ${CLAUDE_SKILL_DIR}/scripts/tracker.py -c $CAMP status
python3 ${CLAUDE_SKILL_DIR}/scripts/tracker.py -c $CAMP status "Ethros the 19th"
python3 ${CLAUDE_SKILL_DIR}/scripts/tracker.py -c $CAMP clear           # conditions + concentration + effects
python3 ${CLAUDE_SKILL_DIR}/scripts/tracker.py -c $CAMP clear --all     # also clears death saves
```

**When to run:** condition applied/removed; caster begins/loses concentration (immediately, not end of turn); PC drops to 0 HP; each death save rolled; end of encounter → `clear`. With the display running the death saves are rolled and counted there (SKILL.md → *Death saving throws*): `saves` is for a table with no display, and each step it records is mirrored on the display's card.

---

## Calendar Script — `scripts/calendar.py`

The in-world date and time, and with them the season, the moon, the weather and what is coming. **Every command that changes something also updates the display** — the world clock and the players' Calendar panel — so there is no separate `--world-time` push to remember.

```bash
# One-time setup, from the Calendar block of world.md (run during /dm:dnd new, or at the first load):
python3 ${CLAUDE_SKILL_DIR}/scripts/calendar.py -c $CAMP init \
    --date "15 Harvestmoon 1247" --time "morning" --era "AR" \
    --months "Frostfall,Deepwinter,Thawmonth,Seedtime,Bloomtide,Highsun,Harvestmoon,Duskfall" \
    --month-length 30 \
    --day-names "Sunday,Moonday,Ironday,Windday,Earthday,Fireday,Starday" \
    --seasons "Winter:Frostfall,Deepwinter;Spring:Thawmonth,Seedtime;Summer:Bloomtide,Highsun;Autumn:Harvestmoon,Duskfall" \
    --moon "Selune:28" --full-moon "20 Harvestmoon 1247" \
    --climate temperate                      # cold | temperate | warm | arid

# Add or change era / seasons / moon / climate later — only what you pass changes:
python3 ${CLAUDE_SKILL_DIR}/scripts/calendar.py -c $CAMP setup --moon "Vela:29" --full-moon "16 Brumosa 1147"

# Time advancement
python3 ${CLAUDE_SKILL_DIR}/scripts/calendar.py -c $CAMP advance 8 hours
python3 ${CLAUDE_SKILL_DIR}/scripts/calendar.py -c $CAMP advance 2 days
python3 ${CLAUDE_SKILL_DIR}/scripts/calendar.py -c $CAMP rest short   # +1 hour
python3 ${CLAUDE_SKILL_DIR}/scripts/calendar.py -c $CAMP rest long    # +8 hours

# Query / manual set
python3 ${CLAUDE_SKILL_DIR}/scripts/calendar.py -c $CAMP now          # date, season, weather, moon, next events
python3 ${CLAUDE_SKILL_DIR}/scripts/calendar.py -c $CAMP set "22 Harvestmoon 1247" evening
python3 ${CLAUDE_SKILL_DIR}/scripts/calendar.py -c $CAMP time night   # dawn morning midday afternoon dusk evening night midnight

# Weather — keys: clear cloudy overcast fog rain storm snow windy · freezing cold mild warm hot
python3 ${CLAUDE_SKILL_DIR}/scripts/calendar.py -c $CAMP weather roll                 # from season and climate
python3 ${CLAUDE_SKILL_DIR}/scripts/calendar.py -c $CAMP weather set fog cold --text "nebbia che non si alza"
python3 ${CLAUDE_SKILL_DIR}/scripts/calendar.py -c $CAMP weather auto on              # every new day rolls its own

# Coming events
python3 ${CLAUDE_SKILL_DIR}/scripts/calendar.py -c $CAMP event add "Notte delle Lanterne Basse" "30 Brumosa" --yearly --note "festa di Morwen"
python3 ${CLAUDE_SKILL_DIR}/scripts/calendar.py -c $CAMP event add "Dieta d'Inverno" "21 Lungabuio 1147"
python3 ${CLAUDE_SKILL_DIR}/scripts/calendar.py -c $CAMP event add "Il rito della Confraternita" "16 Brumosa 1147" --secret
python3 ${CLAUDE_SKILL_DIR}/scripts/calendar.py -c $CAMP event remove "Dieta d'Inverno"
python3 ${CLAUDE_SKILL_DIR}/scripts/calendar.py -c $CAMP events

# Send the clock and the calendar to the display again (after /dm:dnd load or a --clear)
python3 ${CLAUDE_SKILL_DIR}/scripts/calendar.py -c $CAMP push
```

- **Seasons** say which months they hold. The weather roll needs to know which is the winter: names like Winter / Inverno / Hiver are recognised, anything else takes `Name=winter:Month,…` (`winter`, `spring`, `summer`, `autumn`).
- **Moon:** a cycle length and the date of one full moon; the script and the players' calendar work out every phase from that.
- **Weather** is stored as keys the display translates into each player's language; `--text` replaces them with your own words (keep the key right — it picks the icon and the sky). With `weather auto on` the script rolls when a day begins and prints the result: **narrate it**. Yesterday's weather often lingers. Without it, the script reminds you at each new day that the weather is still what you last set.
- **Events** the players know about go on their calendar with the days to go; `--secret` ones are yours alone and are never sent. `--yearly` for a recurring feast (a date with no year). When time advances the script prints `★ TODAY: …` for an event that has come and `★ PASSED …` for one skipped over, and `☾ … is full tonight` — act on them.
- A campaign with an old `calendar.json` keeps working; `setup` adds what it lacks.

**When to run:** after every rest; after significant travel or time skip; when the time of day changes in the fiction (`time`); when the weather changes (`weather set`, or leave it to `weather auto on`); when the party learns of a dated event (`event add`); when manually updating `state.md` date — use `calendar.py set` to keep them in sync.

---

## Campaign Search — `scripts/campaign_search.py`
Keyword search across campaign files. Use this **before** loading full files into context when looking up a specific past event, NPC detail, or plot thread.

```bash
CAMP=my-campaign

# Search all default files (state, log, archive, world, npcs):
python3 ${CLAUDE_SKILL_DIR}/scripts/campaign_search.py -c $CAMP Lasswater

# Narrow to specific files:
python3 ${CLAUDE_SKILL_DIR}/scripts/campaign_search.py -c $CAMP "merchant letter" --files log,archive

# Multi-keyword AND search:
python3 ${CLAUDE_SKILL_DIR}/scripts/campaign_search.py -c $CAMP VARETH Kel

# More context lines around each match:
python3 ${CLAUDE_SKILL_DIR}/scripts/campaign_search.py -c $CAMP Harwick -C 6
```

File keys: `state`, `log`, `archive`, `world`, `seeds`, `npcs`, `npcsfull`
Default files searched: state, log, archive, world, npcs

**When to use:** Any time a player asks about a past event, NPC detail, location, or plot thread that may not be in active context. Run this first — only escalate to a full `Read` if the search returns insufficient context.

---

## Session Recap — `scripts/session_recap.py`

Deterministic state-diff between two character snapshots. Computes the mechanical change set (HP/temp/level/hit dice/death saves/conditions/concentration/exhaustion/inspiration/spell slots) from data so narration never recomputes it — recaps are the single thing an LLM is most likely to hallucinate. Reads `<campaign>/characters/*.md` and merges live `tracker.json` conditions/concentration. Zero LLM calls.

```bash
CAMP=my-campaign

# Snapshot the party now — sets the baseline (writes to <campaign>/.recap/,
# rolling last → prev). Run this at session START (e.g. /dm:dnd load) so there
# is a baseline to diff against later.
python3 ${CLAUDE_SKILL_DIR}/scripts/session_recap.py snapshot --campaign $CAMP

# Diff the baseline against current state → one-paragraph summary, then ADVANCE
# the baseline to "now" so the next diff chains from here. Run at /dm:dnd save
# (end of session) for a since-start recap, or each turn for a since-last-turn
# recap — either way it advances, so consecutive diffs never re-report old deltas.
python3 ${CLAUDE_SKILL_DIR}/scripts/session_recap.py diff --campaign $CAMP
# → "Aldric: took 18 damage (30→12 HP); gained Poisoned; spent 2 level 1 slots."

# Same comparison without moving the baseline (ad-hoc "what changed so far?"):
python3 ${CLAUDE_SKILL_DIR}/scripts/session_recap.py diff --campaign $CAMP --no-roll

# Structured change list instead of prose:
python3 ${CLAUDE_SKILL_DIR}/scripts/session_recap.py diff --campaign $CAMP --json

# Diff two snapshot files directly (no campaign lookup):
python3 ${CLAUDE_SKILL_DIR}/scripts/session_recap.py diff-files before.json after.json
```

---

## Oracle — `scripts/oracle.py`

Dice-driven solo/improv oracles (Mythic chaos factor, Ironsworn yes/no, Random Event Focus, scene-meaning word pairs). Keeps pacing transparent and rollable instead of invented. Rolls are stdlib-random and seedable (`--seed N`). The chaos factor persists in `state.md → ## Session Flags` as `chaos_factor: N`. Zero LLM calls.

```bash
CAMP=my-campaign

# Chaos factor (1-9): show / set / adjust (persisted to state.md)
python3 ${CLAUDE_SKILL_DIR}/scripts/oracle.py chaos --campaign $CAMP
python3 ${CLAUDE_SKILL_DIR}/scripts/oracle.py chaos set --campaign $CAMP --value 7
python3 ${CLAUDE_SKILL_DIR}/scripts/oracle.py chaos adjust --campaign $CAMP --pc-lost

# Yes/no oracle — likelihood + chaos modifier → verdict + d100
python3 ${CLAUDE_SKILL_DIR}/scripts/oracle.py ask --likelihood likely --campaign $CAMP
# → "NO-BUT  (d100=82, likelihood=likely, chaos=8)"

# Random Event Focus (d100 → direction label)
python3 ${CLAUDE_SKILL_DIR}/scripts/oracle.py event

# Scene-meaning word pair (action / subject)
python3 ${CLAUDE_SKILL_DIR}/scripts/oracle.py scene
```

Likelihoods: `sure-thing`, `likely`, `50/50`, `unlikely`, `no-way`. Verdict suffixes: `-and` (extreme, on doubles), `-but` (qualified, near threshold).

---

## Deterministic Graph Extraction — `scripts/graph_extract_deterministic.py`

Zero-LLM relationship extractor. Pattern-matches session-log sentences against the bundled verb-table seed (`data/graph/verb_table_seed.yaml`) and emits typed edge proposals in the exact shape `campaign_graph.py` consumes. ~50% recall (clean subject-verb-object only), ~95% precision, no Claude API call. Usually driven through `campaign_graph.py extract --deterministic` rather than directly:

```bash
CAMP=my-campaign

# Propose edges (stdout), no writes:
python3 ${CLAUDE_SKILL_DIR}/scripts/campaign_graph.py extract --campaign $CAMP --deterministic

# One-shot auto-apply high-confidence proposals into graph.json (idempotent):
python3 ${CLAUDE_SKILL_DIR}/scripts/campaign_graph.py extract --campaign $CAMP \
    --deterministic --apply --min-confidence high
```

---

## Data Commands — `scripts/sync_srd.py`, `scripts/build_srd.py`, and `scripts/lookup.py`

Dataset is bundled at `${CLAUDE_SKILL_DIR}/data/dnd5e_srd.json`. No runtime download required.

```bash
# Check / rebuild dataset (only needed when upstream sources update):
python3 ${CLAUDE_SKILL_DIR}/scripts/sync_srd.py             # rebuild if 5e-bits or FoundryVTT has new commits
python3 ${CLAUDE_SKILL_DIR}/scripts/sync_srd.py --check     # check upstream SHAs, don't rebuild
python3 ${CLAUDE_SKILL_DIR}/scripts/sync_srd.py --force     # always rebuild
python3 ${CLAUDE_SKILL_DIR}/scripts/build_srd.py --status   # show current dataset metadata

# Lookup during play (CLI):
python3 ${CLAUDE_SKILL_DIR}/scripts/lookup.py spell "fireball"
python3 ${CLAUDE_SKILL_DIR}/scripts/lookup.py item "cloak of protection"
python3 ${CLAUDE_SKILL_DIR}/scripts/lookup.py feature "sneak attack"
python3 ${CLAUDE_SKILL_DIR}/scripts/lookup.py condition "poisoned"
python3 ${CLAUDE_SKILL_DIR}/scripts/lookup.py monster "goblin"
python3 ${CLAUDE_SKILL_DIR}/scripts/lookup.py monster "dragon" --all   # all fuzzy matches

# Programmatic (used by display companion /srd-lookup endpoint):
from lookup import lookup, lookup_record, lookup_with_level, suggest
lookup("fireball", category="spell")                  # → formatted string
lookup_with_level("sneak attack", category="feature", level=3)  # → level-resolved string
suggest("poisonned", category="condition")            # → [("Poisoned", "conditions"), ...]
```

**Did-you-mean recovery.** A mistyped name doesn't dead-end. When a lookup misses, the CLI prints a `Did you mean: …?` line and the display's SRD modal offers tappable near-miss chips — both powered by `suggest()`, which fuzzy-matches the query against real names (`poisonned` → Poisoned, `fireballl` → Fireball, `gobblin` → Goblin). Suggestions respect the category when one is given, and search all categories otherwise. Use the suggested name rather than guessing at a spelling.

**When to use:** combat (monster stat blocks before using them); spellcasting (range, components, duration, at-higher-levels); conditions (rule text before applying); loot and equipment; NPC generation (monster stat block as mechanical base). The display companion's character sheet modal handles lookups automatically during play — these CLI calls are for DM reference outside the UI.

---

## Display Companion Setup (one-time)

```bash
cd ${CLAUDE_SKILL_DIR}/display
pip3 install -r requirements.txt
```

```
Terminal (run claude directly — no wrapper needed)
    ↓ send.py calls per narration block / dice roll / stat change
Flask on https://localhost:5001 (dnd-display-app.py — HTTPS, self-signed cert)
    ↓ Server-Sent Events
Browser tab → Chromecast → TV
```

**Start the display:**
```bash
bash ${CLAUDE_SKILL_DIR}/display/start-display.sh          # localhost
bash ${CLAUDE_SKILL_DIR}/display/start-display.sh --lan    # LAN mode (phones, tablets)
open https://localhost:5001                                  # open browser before /dm:dnd load
```

`start-display.sh` always force-kills any previous instance before starting — no manual pre-kill needed.

**Load a campaign:**
```
/dm:dnd load <campaign-name>   # skill auto-detects running display, pushes party stats
```

The DM skill sends each narration block, dice result, and stat update via `send.py` calls (see Active DM Mode in SKILL.md for full send sequence and stat flag reference).

Open the browser tab and Chromecast it *before* running `/dm:dnd load` so the browser is connected when the opening narration streams in. The display buffers the last 60 chunks and replays them to reconnecting browsers.

**Scene detection:** server scans narration for keywords and shifts background gradient + particle type (17 scenes: tavern, dungeon, forest, crypt, arcane, ocean, etc.). Crossfades over ~2.5 s.

**Audio (Python-side):** `audio.py` auto-imported by `dnd-display-app.py`. Two toggles: Ambient (looping soundscape) and Effects (one-shot SFX). Both default off. Scene changes crossfade the ambient loop. All synthesis via numpy — no audio files needed.

---

## Secret Notes — `display/whisper.py`

A note only one player reads: it appears on that player's phone and nowhere else — not in the narration, not in the voice, not on the shared screen, not on the other phones.

```bash
# The others see just "the DM passes a note to Aldric" (good table tension):
python3 ${CLAUDE_SKILL_DIR}/display/whisper.py --to "Aldric" "Dietro l'arazzo senti qualcuno respirare."
# The others see nothing at all:
python3 ${CLAUDE_SKILL_DIR}/display/whisper.py --to "Aldric" --no-teaser "Il mercante ti fa l'occhiolino."
# Re-read the notes of this campaign (all, or one character's thread):
python3 ${CLAUDE_SKILL_DIR}/display/whisper.py --list --character "Aldric"
```

**When to use it** — whenever a fact belongs to one character and saying it aloud would hand it to the whole table:
- what only the PC who passed the check notices (Perception, Insight, Arcana);
- a vision, a dream, a telepathic voice, a patron's or deity's message;
- the command behind a charm or domination, a curse the others should not know about;
- a private aside from an NPC, or the answer to a secret note the player sent.

Narrate publicly only what everyone perceives ("Aldric si irrigidisce un istante"), then send the note. The player decides whether to tell the others — do not do it for them.

**Notes from players.** A player can write to the DM in secret from their phone (*Secret notes*). `check_input.py` and `autorun_wait.py` print each one as a line like:

```
[[SECRET NOTE from Mira — for the DM only, not a turn action. …]] Sfilo la chiave ad Aldric di nascosto.
```

It is not a turn action and not for the table: resolve it quietly (a hidden roll if it needs one), answer with `whisper.py --to "Mira"`, and keep the public narration free of anything the other characters could not perceive. A secret note ends an autorun wait early; after answering it, go back to waiting.

---

## Battle Maps — `display/map_render.py`

Write the layout as text; the image backend paints the terrain, the script draws grid, coordinates and tokens on top (exact squares), saves an SVG into `<campaign>/media/` and shows it on the display.

```bash
python3 ${CLAUDE_SKILL_DIR}/display/map_render.py << 'DNDEND'
title: Cripta di Vessar
scale: 1 quadretto = 1,5 m
art: damp underground crypt, cracked grey flagstones, scattered bones, candlelight
---
############
#..A....g..#
#..B..%%...+
#~~~..&..<.#
####+#######
---
A: Aldric | pc
B: Mira | ally
g: Goblin arciere | foe
DNDEND

python3 ${CLAUDE_SKILL_DIR}/display/map_render.py --symbols   # print the full key
```

| Symbol | Meaning | Symbol | Meaning |
|---|---|---|---|
| `#` | wall | `.` | floor |
| space / `_` | off-map | `+` | door (orients itself to the wall) |
| `~` | water | `^` | trap / hazard |
| `*` | tree / bush | `%` | difficult terrain |
| `,` | grass / open ground | `=` | furniture (table, altar, crate) |
| `&` | pillar / statue | `<` `>` | stairs up / down |
| `!` | fire / brazier | letter | token |

Tokens are letters. Legend line `X: Name | side`, side ∈ `pc`, `ally`, `npc`, `foe`, `neutral`. Unlisted letters: UPPERCASE = pc, lowercase = foe. Header and legend are optional; `---` separates the three parts. Max 60×60 — keep maps ≤ 20×15 so they read on a phone.

**Effects** — what just happened on the field, as extra lines in the legend part. Coordinates are the map's ruler (column,row, from 1):

```
@ 9,8 r1 fire | Esplosione di cenere      # 9,8 and one square around it (3×3)
@ 4,2-6,3 magic | Portale                 # rectangle, corner to corner
@ 12,5 light                              # one square, no legend entry
```

Kinds: `fire` `magic` `cold` `poison` `acid` `lightning` `dark` `light` `blood` `smoke` (aliases: explosion→fire, portal/arcane→magic, ice→cold, shadow→dark, holy/radiant→light, fog→smoke, gas→poison). Effects are drawn over the art and under the tokens and never change the terrain, so a re-send with an effect uses the cached painting and is instant. Labelled effects are listed in the map legend. Leave the line out on the next re-send once the effect is gone.

Re-sending a map with the same title (or `--name <id>`) marks the older copy in the feed as superseded. `--caption TEXT` overrides the caption, `--no-send` only saves, `--out file.svg` writes a plain map elsewhere.

**Terrain art.** `art:` (English, optional) describes the look; without it the look is guessed from the symbols. How it is made depends on the backend (`map_art` in `images.json`, or `--art`):

| Mode | Backend | Result |
|---|---|---|
| `paint` | local, gemini | the layout is repainted (img2img) — walls and doors stay where you drew them |
| `tiles` | any (pollinations) | one generated texture per terrain type (floor, wall, water, grass, rough), tiled per square |
| `auto` | — | paint on local/gemini, tiles otherwise (default) |
| `off` | — | plain vector map |

Doors, known traps and stairs stay marked on top of painted art; tokens always do. Art is cached per terrain + `art:` line, so re-sends with moved tokens are instant — keep `art:` identical for the same place. New terrain: the plain map is shown immediately, the art version supersedes it when ready (run it with `run_in_background: true`). Any art failure leaves the plain map (stderr line, exit 0).

## Progress Clocks — `display/clock.py`

A circle cut in segments that fills as something gets closer: a ritual, the guards' patience, a collapsing bridge, the party's own progress on a long task. Every screen shows the public ones — small in the top bar, large in the **Clocks** panel — and a toast says when one moves.

```bash
python3 ${CLAUDE_SKILL_DIR}/display/clock.py add "Il rito della Confraternita" 6 --kind danger --note "alla luna piena"
python3 ${CLAUDE_SKILL_DIR}/display/clock.py add "Scassinare il caveau" 4 --kind goal
python3 ${CLAUDE_SKILL_DIR}/display/clock.py add "I rinforzi arrivano" 8 --secret     # yours alone
python3 ${CLAUDE_SKILL_DIR}/display/clock.py tick "rito"          # one more segment; tick "rito" 2 for two
python3 ${CLAUDE_SKILL_DIR}/display/clock.py back "rito"          # the party bought time
python3 ${CLAUDE_SKILL_DIR}/display/clock.py set "rito" 5
python3 ${CLAUDE_SKILL_DIR}/display/clock.py reveal "rinforzi"    # the players see it from now on (hide: back to secret)
python3 ${CLAUDE_SKILL_DIR}/display/clock.py remove "caveau"
python3 ${CLAUDE_SKILL_DIR}/display/clock.py list                 # all of them, the secret ones too
python3 ${CLAUDE_SKILL_DIR}/display/clock.py clear
```

- 2 to 12 segments. Kinds: `danger` (red — something bad is coming), `goal` (green — the party's progress), `time` (a plain countdown; the default). Italian words work (`pericolo`, `obiettivo`, `tempo`).
- A name can be cut to any part that means one clock only. A wrong name or number changes nothing.
- Clocks live in `<campaign>/clocks.json` and are still there next session. `--campaign NAME` (before the subcommand) works on another campaign.
- When the last segment fills the script prints `★ FULL`: **what the clock was counting down to happens now** — narrate it, then `remove` the clock.

**When to use one:** whenever the fiction has "how long before…?" or "how close are we to…?" and the answer is not a date on the calendar. Size it by how many setbacks or steps it should take: 4 for something close, 6 for a scene-long pressure, 8 for a session-long one. Tick a danger clock when the party fails a roll, wastes time or makes noise — say why as you narrate — and a goal clock when they make real progress. Keep a clock `--secret` while the characters have no way to know it is running; reveal it the moment they do. Two or three running at once is plenty.

---

## Region Map — `display/region_map.py`

The map of the region, drawn on parchment as the party discovers it: places visited or heard of, roads with the days of travel, and a flag where the party is. Saved in `<campaign>/region.json`; every screen has it one tap away, and tapping a place shows what the party knows about it and how many days away it is. No image backend needed.

```bash
python3 ${CLAUDE_SKILL_DIR}/display/region_map.py init "Valle di Cenere" --legend "I numeri sulle strade sono giorni di cammino"

# The first place needs a position on a 0–100 grid (x → east, y → south); start near the middle:
python3 ${CLAUDE_SKILL_DIR}/display/region_map.py add "Sarvenna" --kind city --at 40,55 --note "Capitale del ducato"
# Every other place: where it is from one already on the map. It is placed and the road is drawn:
python3 ${CLAUDE_SKILL_DIR}/display/region_map.py add "Cripta di Vessar" --kind dungeon --near "Sarvenna" --dir NE --days 2 --road trail
python3 ${CLAUDE_SKILL_DIR}/display/region_map.py add "Porto Grigio" --kind port --near "Sarvenna" --dir W --days 3 --rumor

python3 ${CLAUDE_SKILL_DIR}/display/region_map.py road "Cripta di Vessar" "Porto Grigio" --days 4 --kind trail
python3 ${CLAUDE_SKILL_DIR}/display/region_map.py here "Sarvenna"            # the party is here; shows the map
python3 ${CLAUDE_SKILL_DIR}/display/region_map.py travel "Cripta di Vessar"  # shortest known route; moves the party
python3 ${CLAUDE_SKILL_DIR}/display/region_map.py note "Sarvenna" "Il duca è morto; regge il siniscalco"
python3 ${CLAUDE_SKILL_DIR}/display/region_map.py list                       # places, coordinates, roads
python3 ${CLAUDE_SKILL_DIR}/display/region_map.py remove "Porto Grigio"
python3 ${CLAUDE_SKILL_DIR}/display/region_map.py show                       # put the map in the story now
```

- **Kinds:** `city town village castle tower ruin dungeon forest mountain lake port temple camp landmark` (Italian words — città, villaggio, rovine, cripta, bosco… — are understood). **Roads:** `road` (default), `trail`, `river`, `sea`. **Directions:** `N NE E SE S SW W NW`.
- **Add a place only when the characters learn it exists** — they arrive, read it on a handout, or an NPC names it. `--rumor` = heard of but never seen: drawn faint with a question mark; reaching it with `travel` or `here` makes it visited. Never put secrets on the map.
- **Keep it consistent with `world.md`**: same names, same directions and distances. Run `list` before adding if unsure where things are. Half days are fine (`--days 1.5`).
- `travel` follows the shortest route along the roads on the map, marks every place on the way as visited and prints the legs and the total days. **It does not move the calendar**: when the journey has been played (encounters, camps), advance it with `calendar.py -c <campaign> advance N days`. If no known road leads there, add one with `road` — or use `here` when the party gets there some other way (teleport, ship, captured).
- The map zooms out by itself when the known world outgrows the sheet. `note` text is shown to the players: write only what the party knows.

## Generated Images — `display/image_gen.py`

**In play, generate only `scene` images of places (plus battle maps via `map_render.py`)** — see SKILL.md → *Images & Maps*. The other kinds below are for explicit player requests only.

Portraits, monsters, scenes and items. Backend (Pollinations / local Forge / Gemini / off) is set in `~/.config/claude-dnd/images.json` — setup: `docs/SKILL-images.md` at the plugin root.

```bash
# New NPC portrait (prompt: English, physical description only)
python3 ${CLAUDE_SKILL_DIR}/display/image_gen.py --kind portrait --subject "Vesna" \
  --prompt "half-elf innkeeper woman, 50s, grey braid, burn scar on left hand, wary eyes, leather apron" \
  --caption "Vesna, l'ostessa"

# Same subject again → re-shows the cached file, no generation, no --prompt needed
python3 ${CLAUDE_SKILL_DIR}/display/image_gen.py --kind portrait --subject "Vesna"

python3 ${CLAUDE_SKILL_DIR}/display/image_gen.py --kind monster --subject "Ghoul" --prompt "..."
python3 ${CLAUDE_SKILL_DIR}/display/image_gen.py --kind scene   --subject "Porto di Karsa" --prompt "..."

# A moment of the fight / a dramatic event — always a new image; the outcome, after the roll
python3 ${CLAUDE_SKILL_DIR}/display/image_gen.py --kind action --subject "Lllooo colpisce il segugio" \
  --prompt "stout dwarf warrior, braided black beard, chain mail, greataxe raised high, dark tavern" \
  --caption "Lllooo alza l'ascia"

# Two subjects: one --figure each (shape:position:size | its own description); --prompt = the whole scene.
python3 ${CLAUDE_SKILL_DIR}/display/image_gen.py --kind action --subject "Lllooo spacca il segugio" \
  --prompt "a dwarf warrior cleaving a fire elemental hound with a greataxe in a dark medieval tavern" \
  --figure "humanoid-short:left:large | stout dwarf warrior facing right, braided black beard, chain mail, swinging a huge greataxe" \
  --figure "quadruped:right:large | fire elemental hound facing left, body of cracked black charcoal and grey ash, glowing lava cracks" \
  --caption "L'ascia di Lllooo spacca il segugio"

python3 ${CLAUDE_SKILL_DIR}/display/image_gen.py --kind item    --subject "Lama di Vessar" --prompt "..."

python3 ${CLAUDE_SKILL_DIR}/display/image_gen.py --list      # this campaign's images + the prompts used
python3 ${CLAUDE_SKILL_DIR}/display/image_gen.py --status    # backend + keys (masked)
```

`--subject` is the cache key — always use the canonical name from `npcs.md`. `action` never reuses the cache (each call is the next `--vN` of its subject). `--figure` (repeatable, up to 4): on the local backend with `local_couple` each description is its own regional prompt (Forge Couple) and a light depth sketch places the silhouettes; elsewhere the descriptions are folded into the one prompt. `--compose SPEC` = positions only, one shared prompt. Kinds and framing: `portrait` head and shoulders · `monster` full body · `scene` wide establishing shot · `action` medium shot, motion · `item` single object. Keep prompts to one subject and one action (12–25 words); start PC prompts with their sheet's `**Image look:**` line. `--regenerate` makes a new version (`--v2`, …) when the old one is wrong. `--seed N` fixes the seed; the one used is stored in `media/index.json`. Exit code 1 + a stderr line on a backend failure; exit 0 with "images are off" when disabled.

---

## Handouts — `display/handout.py`

A document the party finds, shown as a sheet of paper in the story and kept in the players' **Documents** list (saved in `<campaign>/handouts.json`, so it is still there next session).

```bash
# Text from stdin (a heredoc keeps the line breaks), or --body for one line:
python3 ${CLAUDE_SKILL_DIR}/display/handout.py --title "Lettera trovata sul corriere" --kind letter --from "V." << 'DNDEND'
Ilde,
il carico parte alla terza luna. Nessuno deve sapere della cripta.
Brucia questa lettera.
DNDEND

# A picture already in <campaign>/media/ (image_gen.py --kind item, or a map rendered with --no-send):
python3 ${CLAUDE_SKILL_DIR}/display/handout.py --title "Mappa del contrabbandiere" --image mappa.png

python3 ${CLAUDE_SKILL_DIR}/display/handout.py --list            # id, kind, title
python3 ${CLAUDE_SKILL_DIR}/display/handout.py --show a1b2c3d4   # put an old one back on every screen
python3 ${CLAUDE_SKILL_DIR}/display/handout.py --remove a1b2c3d4 # the party lost it, or it burned
```

| `--kind` | Looks like | Use for |
|---|---|---|
| `letter` (default) | handwriting, signature bottom right | letters, messages, confessions |
| `notice` | centred capitals in a double frame | wanted posters, edicts, shop signs, contracts |
| `journal` | italic on ruled paper | diaries, logbooks, ledgers, research notes |
| `scroll` | centred text on dark parchment | prophecies, rituals, inscriptions, riddles |
| `note` | a small tilted scrap | hurried notes, passwords, a name on a napkin |

**When to use it:** whenever the characters hold a text in their hands and its exact wording matters — a clue to re-read, a riddle, a contract to sign, a list of names. Write the document itself, in the voice of whoever wrote it (title = what the players would call it; `--from` = the signature, if it has one); narrate the finding with `send.py` as usual and do not repeat the text in the narration. For a text only one character reads in secret, use `whisper.py` instead.

---

## Continuity Autosave — `scripts/autosave_checkpoint.py`, `scripts/install_autosave_hook.py`

Behind-the-scenes continuity checkpoint for long sessions, so a context compaction never loses the player's place. Two layers; see the *Continuity micro-save* rule in SKILL.md and the `/dm:dnd autosave` command.

```bash
# Opt-in: register the Stop hook (writes ~/.claude/settings.json, idempotent)
python3 ${CLAUDE_SKILL_DIR}/scripts/install_autosave_hook.py
python3 ${CLAUDE_SKILL_DIR}/scripts/install_autosave_hook.py --uninstall
python3 ${CLAUDE_SKILL_DIR}/scripts/install_autosave_hook.py --status

# The hook target (also runnable by hand to force a snapshot or inspect state)
python3 ${CLAUDE_SKILL_DIR}/scripts/autosave_checkpoint.py --status
python3 ${CLAUDE_SKILL_DIR}/scripts/autosave_checkpoint.py --campaign <name> --snapshot-only
```

`autosave_checkpoint.py` runs as a Claude Code **Stop hook** (after each turn). It reads the active campaign from `<runtime-dir>/active-campaign.json` (written at `/dm:dnd load`) and the `autosave` flag from that campaign's `state.md`. It **no-ops** when no campaign is active (e.g. a non-D&D session), when `autosave: off`, or when already inside a hook-driven continuation. Every turn it snapshots `state.md` to the runtime dir; every N turns (default 10, `DND_AUTOSAVE_EVERY` to override) it emits a Stop-hook `block` decision that prompts the DM to flush continuity before yielding. The hook is **opt-in** — the in-model micro-save cadence works without it.

**When to use:** offer `install_autosave_hook.py` to players running long imported modules who hit compaction mid-session. The flag toggle (`/dm:dnd autosave on|off`) is the in-session control.

## Campaign Backup — `scripts/backup.py`

A dated zip of the campaign folder in `<DATA root>/backups/<campaign>/`, so a bad save or a wrong edit is never the only copy.

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/backup.py save    --campaign <name> --label end   # every /dm:dnd end
python3 ${CLAUDE_SKILL_DIR}/scripts/backup.py list    --campaign <name>
python3 ${CLAUDE_SKILL_DIR}/scripts/backup.py restore --campaign <name> 1 --yes       # 1 = the newest
```

- It holds every file of the campaign except `media/` (pictures and maps are never edited and are most of the weight; `--media` for a complete copy) and the global roster sheets.
- The newest 20 are kept (`--keep N`, `0` = all). `--dest DIR` or `DND_BACKUP_DIR` puts them on another disk or in a synced folder.
- `restore` saves what is there now as `…-pre-restore.zip`, then puts the campaign back exactly as it was in the archive — files created since are removed, `media/` is left alone. It only runs with `--yes`: **ask the DM first, and stop the display before restoring** (it keeps writing `session_tail.json`). `--roster` also puts back the roster sheets.

**When to run:** at `/dm:dnd end` (step 1g); before anything that rewrites many files at once (a level-up of the whole party, a migration, an `npc rename`); whenever the DM asks. Not on every save — the point is a copy from *before* the session's edits went wrong.

---

## Lazy Corpus — `scripts/corpus_check.py`

Imported (structured) campaigns keep the full module text as a lazily-loaded reference layer instead of inlining it. Layout:

```
<campaign>/
  world.md           # load-time core (Foundations, Three Truths, factions)
  world-nodes.md     # lazy: full Quest Seed Bank + Adventure Nodes (per-act read)
  arc.md             # lazy: full act/chapter tree (state.md holds current+next only)
  source-index.md    # chapter-id -> source file -> one-line scope
  source/<id>.md     # lazy: one file per chapter, the module's source text
```

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/corpus_check.py --campaign <name>
```

Validates that every chapter id in `source-index.md` has a matching `source/<id>.md` (and vice-versa) and that `arc.md` exists. Run it at the end of `/dm:dnd import`. A campaign with no `source/` layer (dynamic, sandbox, or a pre-v2.2.0 import) is reported as a clean no-op — nothing to validate, and its load path is unchanged.
