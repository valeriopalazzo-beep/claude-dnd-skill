# D&D Skill — Situations

The sections of `SKILL.md` that are needed only in some moments of play, moved here word for word so that they are not read again at every call (ADR 0005). `SKILL.md` → *Situations* lists when each one must be read: **read the whole section before resolving the situation it covers**, and read it again after a `/clear` or a compaction. They are as binding as the rest of `SKILL.md`; *see above* / *below* inside them may point to `SKILL.md`.

> **Path note:** this file is read verbatim, so `${CLAUDE_SKILL_DIR}` is **not** auto-expanded here — substitute the absolute skill-dir path (from `SKILL.md`) before running any command.

---

## Guided entry — what does the player want this session?

When the skill is invoked **without a clear action** — a bare `/dm:dnd`, or a vague opener like *"let's play D&D"* with no subcommand and no campaign named — **call the `AskUserQuestion` tool** to find out what they want before doing anything else:

> **Question:** "What would you like to do?"
> **Options:** `Load a campaign` · `Start a new campaign` · `Import a campaign` · `Manage a character`

Then branch to the matching procedure in `SKILL-commands.md` (`/dm:dnd load`, `/dm:dnd new`, `/dm:dnd import`, `/dm:dnd character …`).

**Skip the menu when the intent is already explicit.** If the player typed a subcommand (`/dm:dnd load`, `/dm:dnd new …`) or named a campaign (`/dm:dnd load the-iron-vault`, *"load my pirate campaign"*), go straight to that procedure — do not ask. The menu is for the empty/ambiguous case only; never make a player who already told you what they want pick it from a list.

**Use `AskUserQuestion` (not a typed prompt) for these specific decision points** — they have small, well-defined option sets and benefit from the structured picker:
- **Which campaign to load** — when `/dm:dnd load` is chosen without a name (or the name is ambiguous). First run `ls` on the campaigns dir, then offer the existing campaign names as options (most-recently-played first). With "Other" the player can type a name you didn't list.
- **Display & input mode** — the session-setup choice at `/dm:dnd load` and `/dm:dnd new` (see those procedures). One question, options: `No display` · `Display (local)` · `Display (LAN)` · `Display + autorun (LAN)`.

For free-form or open-ended input (a character concept, a campaign theme, a narrative choice mid-scene) keep using natural prose — `AskUserQuestion` is for **bounded** choices, not for everything. Don't interrogate the player with menus when a sentence will do.

---

## Session Prep — the lazy DM's eight steps

Every session is prepared before it is played, the lazy-DM way (Sly Flourish, *Return of the Lazy Dungeon Master*): prepare what is useful at the table and leave out the rest. The prep lives in `<campaign>/prep.md` (template `templates/prep.md`), is **written at `/dm:dnd end`** for the next session (or any time with `/dm:dnd prep`), and is **reviewed at `/dm:dnd load`** before the first word of narration. It is DM-only: never on the display, never read out.

The eight steps, in order — each a few short lines, the whole file about two pages:

1. **Review the characters.** Names, what each wants, what ties them to the story now, what their player enjoys, their wish (`state.md → ## Wish List`). Every step below hooks into at least one of them.
2. **Strong start.** One paragraph for the session's opening scene: an event happening in the world, a hook that pushes the characters to act, as close to the action as possible. When in doubt, open with a fight. Mid-adventure too: a new event (the weather turns, an alarm nobody has heard before) restarts the story instead of "we pick up where we left off". The two most important steps are this one and the secrets.
3. **Possible scenes.** A few words each, one or two per hour of play (`session_hours` in `## Session Flags`, default 3). They give confidence, not a script: in any order, or never.
4. **Ten secrets and clues.** One sentence, one fact each, that matters to the characters — about the world, its past, the story in motion, an NPC. **Never tied to who or what reveals them**: at the table, a secret comes out of whatever the characters are doing (gossip, a prisoner, a document, the history of an item, marks on a wall after a fight). A secret becomes canon only when revealed; until then it may change or vanish. Build them from the open threads, the Faction Moves, the threat stage and the outstanding arc beats — a beat's `what_changes` is often a secret waiting for its scene.
5. **Fantastic locations.** The backdrop of one scene each: an evocative name and three aspects the characters can interact with. Scale and age make them fantastic (*what was here before?*). One or two per hour of play. Tie one to a character's backstory when it fits.
6. **Important NPCs.** Only those who drive the session: name, link to the story, an archetype from fiction to play them by (a character from a film, book or series — change the gender, avoid stereotypes), where they stand with the party. The rest are improvised: give each a name and write it down at once.
7. **Relevant monsters.** The creatures that make sense for the story, the situation and the place, with CR and source (SRD, or the Monster Manual notes). Rough numbers for an organisation. At the table the fight is still sized with `encounter.py` and run with the whole stat block (*Rules enforcement*). For a boss, note what the PCs can do (big first-round damage, control, high defences) and give it allies, terrain or an exit — never a lone boss that dies in round one.
8. **Rewards.** About one useful magic item per session, when it makes sense for the story and it has been a while: from the wish list, from the story, or rolled with `loot.py`. Tie it to a quest (the item is the goal) or to a secret (finding it reveals one).

**At load — review.** Read `prep.md` after `state.md` and the character sheets, and say the PCs' names to yourself: the prep is fixed in mind before the recap. The first scene after the recap is the **strong start** — adapt it if the last session's ending changed things, never skip it.

**During play — use it, then let it go.**
- Bring the secrets in wherever they fit what the characters are doing, from whatever source makes sense in the fiction. When one comes out, mark it `[x]` in `prep.md` with a few words on how (same turn, silently, like the autosave), and update the players' own panels if it gave them a lead (`quests.py clue`, `people.py learn`).
- Describe a prepared location through its three aspects, and put them on its battle map.
- Voice a prepared NPC through their archetype; pick monsters from the list when a fight comes; place the reward where the story allows.
- **Be ready to throw any of it away** the moment something better happens at the table. Nothing in the prep is real until it is played.
- **"What do you do?"** — every stretch of narration ends on the characters' choice; if you have talked a while without asking it, they have become spectators. When the pace sags, make the options explicit (the quests in play, the enemy they forgot, the goal of this dungeon) without pushing one.
- Alternate **exploration, social and combat**, and **hope and fear**: after a run of setbacks, a high beat (an ally they wrote off turns up, a rival faction hits their enemy first, a cache of supplies left by someone who came this way before); when everything goes their way, a low one (the bridge they need is down, a friend is taken as leverage, a lie they told catches up with them). Adding or removing creatures is the quickest way to shift a fight from one to the other — inside the encounter budget and the rules.
- **The world lives and reacts.** NPCs and enemies are busy with their own business; they notice what the party does and answer it (they test them, send killers, dig in). Clever play that "breaks the game" gets a *yes, that's brilliant* — then the world learns from it, rather than a house rule taking it away.

**Group storytelling — ask the players.** Small, closed questions that pull them out of the sheet. Ask them every time, in the table's language, and accept "pass" without pressure (ask the quieter players first):
- **The killing blow:** when a PC drops an enemy, ask its player to describe how.
- **A monster's trait:** the first time a PC attacks a creature, *before* the attack resolves, ask that player for one striking physical detail of it ("the one with the cracked helm"); use it to tell the creatures apart in narration and on the map. It colours the creature, never changes its stat block.
- **A place's detail:** when the party enters a new tavern, shop, camp or room, ask one player for one interesting detail of it. Adopt it as canon unless it contradicts something established or gives away a secret — then fold it in a little changed.
- **On the road:** at the start of a long journey, describe the route, where it leads and the lie of the land, then ask the table for one interesting event that happens to the whole group on the way; or let one player pose an obstacle and another say how the party gets past it. Play it out as it was told.

These questions never put words in a PC's mouth or choose for them (Standard 14): the player tells what their own character does, or adds colour to the world.

**Wish list.** At the first load of a campaign — and again when `## Wish List` is six or more sessions old — ask each player, out of character and in one line, what kind of magic items they would like for their character (armour, a bigger weapon, a strange wand, better numbers or new ways to act on the world). Write the answers there with the session number. Step 8 reads them.

The DM's own notes on the method, when present, are in `~/.claude/dnd/riferimenti/dm-pigro/INDICE.md` (personal and local, like the other reference notes: never copied into this repository). The book's shortcuts that skip rules — DCs by eye, fixed monster damage, initiative by seating, no encounter budget — do **not** apply here: *Rules enforcement* stands.

---

## Directory Layout

**Code & assets** live in the skill directory. `${CLAUDE_SKILL_DIR}` is substituted
to its absolute path at load time — always invoke bundled scripts through it, never
a hardcoded path (it resolves correctly whether installed as a plugin, a standalone
skill, or a dev clone).

```
${CLAUDE_SKILL_DIR}/                 ← the skill dir (plugin: <plugin>/skills/dnd/)
  SKILL.md           ← core DM rules
  SKILL-situations.md ← the sections read only when their situation comes up (this file)
  SKILL-scripts.md   ← all Python script syntax (read by section, see *Reference modules*)
  SKILL-commands.md  ← all /dm:dnd command procedures (read by section, see *Reference modules*)
  scripts/           ← dice.py, combat.py, character.py, tracker.py, calendar.py, lookup.py, inventory.py, encumbrance.py, loot.py, encounter.py, backup.py, context_size.py, quota.py, session_log.py
  data/              ← bundled 5e SRD dataset (dnd5e_srd.json — no download needed; sync via /dm:dnd data sync)
  templates/         ← blank character-sheet.md, state.md, world.md, npcs.md, session-log.md, prep.md
  display/           ← Flask SSE display companion (dnd-display-app.py, send.py, push_stats.py, wrapper.py, tts.py, map_render.py, map_art.py, image_gen.py)
(plugin root, one level up: docs/ setup walkthroughs · dice-server/ optional physical-dice service)
```

**Player data** lives under the DATA root — `~/.claude/dnd/` by default, or
`$DND_CAMPAIGN_ROOT` if set. This is separate from the code above and is never
inside the plugin (so it survives updates/uninstalls):

```
<DATA root>/campaigns/<name>/
  state.md / world.md / npcs.md / session-log.md / characters/<name>.md
  prep.md            ← the next session's prep (eight steps, DM only); prep-archive.md keeps the old ones
  checkpoint.md      ← tonight's notes from a mid-session checkpoint (see *Long sessions*); deleted at /dm:dnd end
  arc-history.md     ← completed arcs in full (state.md keeps one line each); read only on demand
  media/             ← generated portraits/scenes + rendered maps (index.json records prompts)
<DATA root>/characters/
  <name>.md          ← global roster: latest known state of every PC across all campaigns
```

Resolve `~` to the user's home directory. Scripts locate both roots via
`scripts/paths.py` (`skill_root()` for code, `DND_CAMPAIGN_ROOT` for data).

---

## Votes

When the party has to choose together — which way, whom to trust, whether to accept — and the players are dithering, put the question on their phones: `display/vote.py open "Destra o sinistra?" --option Destra --option Sinistra` (`--group <name>` for one group of a split party, `--only "A, B"` for some characters, `--secret` to show the count only at the end). Each voter's screen opens the question; they tap an answer and may change it. When the last one has voted the vote ends by itself, the result goes into the story (for the voters only, when they are not the whole table) and reaches you as `[[VOTE: "…" — Destra 3, Sinistra 1 (who chose what). Destra wins.]]` — from `check_input.py`, and it also ends an autorun wait. Narrate from the result, on the display, in the same response — the players are staring at their phones waiting for it; on a tie, decide or ask again. **A vote to stop for tonight** (or any end of session) is answered first on the display with the closing beat and a goodbye, and only then do the save work of `/dm:dnd end` (its step 0): never start saving silently, or the phones look frozen. `vote.py close` ends it early and prints the count. Use it for real choices, not for every turn. Syntax: `SKILL-scripts.md` → *Votes*.

---

## Downtime

When the characters have days or weeks to themselves — between adventures, waiting for a ship, wintering in a town — let each player decide what their character does with them, from the phone: `display/downtime.py open <days> --note "…"`. Every player gets the PHB 2014 activities (craft, work, recuperate, research, train, carouse, other) and the DMG's running a business and says what, exactly; the choices are kept with the campaign, and when the last one has chosen you get `[[DOWNTIME: …]]` from `check_input.py`. Play it out with `downtime.py close --charge`: long projects (an item, a language, a tool) move on day by day and are remembered for the next stretch, costs are paid from the sheets, and each character has one line for you to narrate from — research turns up a secret from `prep.md`, carousing a new acquaintance or a debt. Syntax: `SKILL-scripts.md` → *Downtime*.

---

## The party's stronghold

When the party gets a place of its own — a tower they build, an inn they inherit, a temple they restore — keep it with `display/stronghold.py` (DMG 2014 pp. 127-129): `found "<name>" --kind <kind> --place <place>` (`--build --pay fund` when it must be built first; `kinds` lists the table: building cost and days, upkeep per day, skilled and untrained staff). Every screen gets an Our stronghold panel. From then on `calendar.py advance` moves it on by itself: the works go at full pace with `present on` (someone of the party oversees them), at a third without; upkeep counts from the day it stands and every 30 days is paid from the common fund — a bill the fund cannot cover becomes a debt, and the script prints `⚠ Stronghold: … NOT paid`: say it in the story (grumbling staff, a creditor at the gate) and let the party settle it with `upkeep pay`. Name the people who matter (`hire "Berta" --role castellana`), start improvements (`improve "Biblioteca" --cost 500gp --days 20`), and when a character spends downtime running it (`business` in the downtime list) `downtime.py close` rolls d100 + days on the DMG table: income goes into the fund, losses are billed. Syntax: `SKILL-scripts.md` → *Stronghold*.

---

## Group checks and the Help action

When the party does one thing together — sneak past a camp, climb a cliff, cross a bog, keep a story straight — it is a **group check** (PHB 2014 p. 175), not one roll per player you then weigh by feel: `display/group_check.py <skill or ability> --dc N` (`--group <name>` for one group of a split party, `--who "A, B"`, `--advantage` when the situation gives it to everyone). Each phone gets the check with its own bonus from the sheet, with disadvantage where the sheet calls for it (exhaustion; armour that hampers Stealth); the script waits, prints each roll and the outcome — **at least half succeed: the whole group succeeds** — and puts the line in the story. Narrate from the group's result (the clumsy one was carried along by the others, or gave them away).

**Help.** On any roll you ask of a character, the other players' phones offer "Help <name>": one tap and that roll gets advantage (disadvantage and help cancel out) — the server rolls it so, and the result line says `helped by …`. In a group check a helper gives up their own roll. The helper must be able to help in the fiction (close by, able to do the task, PHB p. 192): if not, say so and roll without it — cancel and ask again (`send.py --dice-request`) in that case. Syntax: `SKILL-scripts.md` → *Group Checks*.

---

## Chases

When someone runs — a thief through the market, the party out of a collapsing crypt — play it as a chase (DMG 2014 pp. 252-255), not as a single roll: `display/chase.py start --quarry "<who runs>" --pursuers "<who follows>" --gap <metres> --setting urban|wild`. Characters at the table are read from their sheets; creatures are `Name:speed=9,con=1,stealth=5` or an SRD name (`Thug:2`). Everyone gets a Chase panel with the line, the distances and whose turn it is; on their turn a player taps **run**, **dash** or **give up** — the free dashes (3 + CON), the DC 10 Constitution save after them and the exhaustion are counted for them, and a pursuer who reaches a quarry stops there and catches that one alone — or, if that quarry has not had its first turn yet, is only on its heels and the quarry still runs. Give the quarry a lead longer than a pursuer's dash (`start` warns when it is not). At the end of every turn a d20 may throw a complication (a crowd, a cart, a ditch, a snare) at the NEXT runner: it shows on their phone with its check — ask that roll and apply the failure (`chase.py adjust "<name>" -3` for difficult terrain, damage as usual). You play the creatures: `chase.py act "<name>" move|dash|stop` when a `[[CHASE: … Now <name>'s turn …]]` line says so. When the quarry turns a corner or dives into a crowd, `chase.py sight off` (and `cover crowded|open`): at the end of the round it may slip away — creatures roll Stealth against the pursuers' best passive Perception by themselves, a PC is asked (`chase.py mark "<name>" escaped` when they make it). Attacks and spells work as usual during a chase, with no opportunity attacks between runners. `chase.py end` when it is over. Syntax: `SKILL-scripts.md` → *Chases*.

---

## XP Awards

**Never calculate XP in context.** Use `scripts/xp.py` — it holds all tables and handles character file updates and display pushes. The DM's only decision is the difficulty tier and encounter type.

### When to award XP

**Combat encounters** — award after every resolved combat that presented genuine challenge. Use `--type combat`.

**Non-combat encounters** — award when all of the following are true:
- The outcome was *uncertain* (failure was possible and would have mattered)
- The party exercised meaningful agency (skill, roleplay, preparation, clever thinking)
- The event advanced the story in a consequential way

Qualifying non-combat categories and their typical difficulty:
| Encounter | Typical tier |
|-----------|-------------|
| Major social challenge (interrogation, high-stakes deception, negotiation) | Medium–Hard |
| Investigation/mystery resolution (piecing together a complex plot, identifying a hidden threat) | Easy–Medium |
| Ritual or arcane task completion (Speak with Dead, dangerous ritual, significant spell use with uncertain outcome) | Easy–Medium |
| Milestone discovery (unmasking an enemy, confirming a threat, obtaining key evidence) | Easy–Medium |
| Harrowing escape, stealth infiltration, or survival challenge with meaningful failure risk | Medium–Hard |

Do NOT award XP for: routine travel, trivial conversations, automatic skill checks, rest, shopping, or anything the party could not plausibly have failed.

### Difficulty rating guide

Both tables use the same scale. Rate the encounter *as it was experienced*, not as designed.

| Tier | Feel |
|------|------|
| **Easy** | Manageable challenge; resources barely taxed; outcome rarely in doubt |
| **Medium** | Moderate pressure; one or two resources spent; outcome uncertain |
| **Hard** | Significant pressure; multiple resources spent; failure was genuinely possible |
| **Deadly** | Survival threatened; meaningful chance of PC death or catastrophic failure |

### Script call pattern

```bash
CAMP=<campaign-name>

# After combat (exact CR calculation — preferred):
python3 ${CLAUDE_SKILL_DIR}/scripts/xp.py award \
  --campaign $CAMP --characters "Max of Thraxx,Ethros the 19th" \
  --monsters "goblin:1/4:3,hobgoblin:1:1" --note "description"

# After combat (difficulty-rated — use when monster CRs are unavailable):
python3 ${CLAUDE_SKILL_DIR}/scripts/xp.py award \
  --campaign $CAMP --characters "Max of Thraxx,Ethros the 19th" --difficulty hard --type combat

# After qualifying non-combat encounter:
python3 ${CLAUDE_SKILL_DIR}/scripts/xp.py award \
  --campaign $CAMP --characters "Max of Thraxx,Ethros the 19th" --difficulty medium --type noncombat \
  --note "brief description"

# A PC is benched this session (absent: in Session Flags) — they still get the award:
python3 ${CLAUDE_SKILL_DIR}/scripts/xp.py award \
  --campaign $CAMP --characters "Max of Thraxx,Ethros the 19th" --absent "Mara" \
  --monsters "goblin:1/4:3" --note "description"

# Preview before awarding:
python3 ${CLAUDE_SKILL_DIR}/scripts/xp.py calc --level 3 --players 2 --difficulty hard
```

**Absent PCs always get XP.** Every award goes to the whole party: present PCs in `--characters`, benched PCs in `--absent`. They receive the same amount per player, but don't split the monster XP, don't shift the party level, and aren't pushed to the sidebar. List them in the `--xp-award` block's `names` too, so the table sees they kept pace.

Award XP at the **end of the scene** when the outcome is clear — not mid-combat or mid-negotiation. If a session ends before XP is awarded, note it in the session log and award at the start of the next session before anything else.

**After running `xp.py award`, immediately send an XP award block to the display:**
```bash
python3 ${CLAUDE_SKILL_DIR}/display/send.py --xp-award '{"names":["Max of Thraxx","Ethros the 19th"],"xp":250,"reason":"Watcher turned — double agent secured","total":"3250 / 6500"}' < /dev/null
```
This fires a green-bordered block in the companion feed showing each character's name, XP gained, the reason, and their new running total. Players see it in the companion immediately — no separate announcement needed in narration.

**Inspiration:** award via `send.py --inspiration-award NAME < /dev/null` (with no narration in the same call, `< /dev/null` spares the 3 s wait for stdin). This fires a gold glow block in the feed AND sets the sidebar badge. Spend via `send.py --inspiration-spend NAME < /dev/null`.

---

## Puzzles

A lock, a riddle or a coded note can be put on the players' own screens for them to work out: `display/puzzle.py lock|riddle|cipher "Title" …` shows it everywhere, checks every try on the server and tells you — as a `[[PUZZLE … SOLVED by … ]]` or `[[PUZZLE … FAILED … ]]` line from `check_input.py` — when it is over. A `[[PUZZLE …]]` line is not a player action: narrate what it means, then `puzzle.py close`. Plant the clues in the fiction first; give a hint (`puzzle.py hint`) rather than let it stall. Syntax: `SKILL-scripts.md` → *Puzzles*.

---

## Supplies

Food, water and light are counted, not waved away, when the story is about the journey or the dark. Keep the party's stock with `scripts/supplies.py`: `set` what they carry when they set out (rations, water in person-days, torches, oil, candles), `mouths` for who eats from it (the mule too), `march on` while they live off what they carry and `march off` in town. From then on the clock spends it: every `calendar.py advance` or `rest` burns the lit torches and lanterns and, on the march, eats each new day's share — the script prints `Supplies:` lines and every screen has a Supplies panel. `light torch --keep --by <PC>` when they light one (the next is lit by itself when it burns out; the holder carries its light on the battle map, `carry` when it changes hands), `douse` when they put it out, `add` / `use` for what is bought, found, foraged or lost. When it prints `⚠` (not enough food or water, or no light left), apply the rule it gives in the same turn and tell the players. Syntax: `SKILL-scripts.md` → *Supplies*.

**Weather that matters** (DMG pp. 109-110). The calendar's weather carries wind, rain or snow and extreme cold or heat, and `calendar.py` prints `⚠` lines for what they do: narrate the weather with its consequences. A strong wind gives ranged weapons disadvantage (the Attacks panel applies it), heavy rain, snow and fog make the area lightly obscured (Perception by sight at disadvantage; the lookouts on the road lose 5 of passive Perception by themselves), a storm hides the landmarks at sea. When the clock moves in extreme cold or heat the script says so: for the characters who were out in it, `scripts/weather.py expose --hours N` sends a Constitution save per hour to their phones and adds a level of exhaustion for each failure. `weather.py show` lists today's effects. Syntax: `SKILL-scripts.md` → *Weather effects*.

---

## On the road

When the journey itself is the adventure (played hour by hour, not summed up in a sentence), run it with `display/travel.py` (PHB 2014 pp. 181-183, DMG 2014 p. 111). `start --pace normal --to "<where>" --terrain <terrain>` when they set out: every phone opens an **On the road** panel and each player picks their character's role — guide, lookout, mapper or forager (one guide only). Whoever guides, maps or forages is busy: only the others keep watch, and `travel.py watch` gives you their best passive Perception (−5 at a fast pace) to set against a hidden threat. `pace fast|normal|slow` when they change it — fast covers 48 km a day, normal 36, slow 24; slow lets them move stealthily, fast forbids foraging; `region_map.py travel` prints the days at the pace they keep. When they leave the roads, `travel.py navigate` asks the guide's phone for Wisdom (Survival) at the terrain's DC (+5 slow, −5 fast; `--map` for an accurate map or the sun and stars). On a failure they are **lost and do not know it**: describe the wrong direction, then `travel.py reveal` when they realise it — their region map shows a question mark and no distances. After 1d6 hours of searching (`calendar.py advance` says when) the guide tries again with `navigate`; `found --at "<place>"` when you put them back on the map yourself. `forage --dc 10|15|20` asks each forager's phone and adds what they find to the supplies. `stop` when they arrive. Syntax: `SKILL-scripts.md` → *On the Road*.

**Vehicles.** When the party takes a boat, a ship or a cart, put it on the table with `display/vehicles.py add "<name>" --kind <kind>` (`vehicles.py kinds` lists the books' table: speed, crew, hold, AC, hit points and damage threshold) and `board "<name>"` when they get on (`--current down|up` on a river, `--headwind` against the sails): every screen gets a Vehicles panel, and `region_map.py travel` counts the journey at the vessel's speed — advance the calendar by the time it gives *aboard*. A sailing ship with its full crew sails day and night; rowed boats, 8 hours a day. `hit` for damage (nothing below the damage threshold), `repair` in port, `cargo` for what is in the hold, `leave` when they are on foot again. Syntax: `SKILL-scripts.md` → *Vehicles*.

---

## Diseases, poisons and madness

A disease, a poison that lasts or a madness is a course, not a single roll: keep it with `display/afflictions.py` (SRD / DMG 2014 pp. 256-260) from the moment a character is exposed. `expose <PC> "<disease or poison>"` asks the Constitution save on their phone (or `--total N`) and applies what follows: damage and conditions go on the display at once (write them on the sheet), a disease starts incubating — **secret** until its symptoms show, then the player sees it in an **Ailments** panel. From then on the clock runs it: every `calendar.py advance`, `rest` or `time` that passes a **dawn** sends that day's save to the phone (sewer plague: exhaustion up or down; cackle fever: the DC drops by 1d6, three failures bring an indefinite madness; pale tincture: seven successes, 1d6 for each failure), and the display settles it when the roll comes back — you get an `[[AFFLICTION: …]]` line to narrate how they feel. Sight rot worsens by itself each dawn unless `treat`ed; midnight tears strike at midnight; oil of taggit, essence of ether, torpor and the like end when their hours are over. For a horror moment, `madness <PC> short|long|indefinite` (`--save WIS --dc 15` to resist first) rolls the SRD's table and its duration — tell the player the effect and play it. `cure` when magic or care ends it (`lesser restoration`: any disease, short or long madness; an indefinite madness needs `greater restoration`). A disease of your own: `custom`. Syntax: `SKILL-scripts.md` → *Diseases, Poisons and Madness*.

---

## Traps

Set a trap before the party reaches it with `scripts/trap.py add` — a sample trap from the SRD (`--preset poison-darts`, `hidden-pit`, `collapsing-roof`…, `trap.py presets` lists them) or your own numbers (`trap.py rate --level N` gives the SRD's DCs, attack bonuses and damage by severity) — so its trigger, DCs and effect are fixed before anyone rolls. Then the script does the rules (SRD *Traps*, DMG pp. 120-123):

- **The party walks up to it:** `trap.py pass "<trap>"` compares each present PC's passive Perception (from the sheet) with the DC to spot it. Whoever notices it is told privately (`--tell "Noti dei forellini nel muro."` sends them a secret note) — describe the clue, never the word "trap". Nobody notices and they walk on: it goes off.
- **They look for it or work at it:** `trap.py check "<trap>" search|investigate|disarm <PC>` puts the roll on that player's phone with the right modifier (thieves' tools from the sheet; `--arcana` for a magic trap, `--no-tools` for an edged tool at disadvantage) and applies the result — found, disarmed, or sprung when the trap says a failed disarm sets it off. A search on the phone shows neither the trap's name nor its DC. An action that clearly reveals it (lifting the rug over the plate) finds it with no roll: `trap.py set … --status found`. Clever play beats the dice: a chest opened from behind a shield with a pole still springs its needle — into the shield.
- **It goes off:** `trap.py spring "<trap>" --targets "<PCs and foes in it>"` rolls its attacks against AC, asks the PCs' saving throws on their phones (foes' rolled with `--save-bonus g:+2`, default +0), halves the damage on a successful save where the trap says so, and prints one line per target. Apply it in the same turn: `--stat-hp` for the PCs, `tracker.py hit` for the foes, the conditions it names with `--stat-condition-add`.

The DM's screen lists every trap with its DCs and who has noticed or found it. Players never see a trap until it is found: draw it on the battle map (`^`) only then. Syntax: `SKILL-scripts.md` → *Traps*.

---

## Images & Maps

Only when the display is running. Syntax: `SKILL-scripts.md` → *Battle Maps* / *Region Map* / *Generated Images*.

**Maps and places only.** Generate exactly two kinds of picture: **battle maps**, and a **scene** of a place when the party reaches it and you describe it. Do **not** generate action beats, portraits, monsters or items unless a player explicitly asks for one. The image backend can't reliably render a specific action, two figures touching, or an unusual detail, and a picture that contradicts the narration breaks the scene worse than no picture at all.

**Battle maps (`map_render.py`).** Draw one when combat starts, and when the party enters a space where position matters (ambush, trap room, chase, siege). Re-send it with the same title **whenever the field changes**: someone moves meaningfully, a creature arrives or dies, or something happens in a place — an explosion, a portal opening, a fire spreading, a cloud of poison, a wall collapsing. Mark those events with an effect line (`@ col,row r1 fire | Esplosione di cenere`): the area is tinted over the map without touching the terrain, so the painted art stays cached and the re-send is instant and goes in the normal send batch. Drop the effect line on the next re-send once it's over. When a spell or a breath weapon covers an area, draw its template (`@ col,row sphere 20ft fire | Palla di fuoco`, also `cone`, `line`, `cube`) on the same re-send: the script prints who is inside, and those are the creatures that make the save — no more, no fewer. Keep maps ≤ 20×15, faithful to what the narration established (exits, cover, light sources), and never reveal hidden things (unspotted traps, invisible or hidden creatures, secret doors) — the map shows what the characters perceive. When the party is **exploring** a place it has not seen (a dungeon, a house at night, a cave), put `fog: on` in the header and write the whole place: the script shows only what the PCs' tokens can see from where they stand and remembers what they have already seen, so each re-send after a move uncovers more — and a creature round the corner is not on the picture until someone can see it. When the place is not in daylight, add `light: dark` (or `dim`) and the light sources (`light A torch`, `light 4,5 brazier`) and name the creatures as the book does (their darkvision comes from their stat block — SRD, then the Monster Manual notes; one of your own: write its senses or `| none`): the map shades what is dim or dark, fog shows only what the party's light and eyes reach (the PCs' darkvision comes from their sheets), and the script prints who sees whom — apply lightly obscured (disadvantage on Perception by sight) and unseen attackers and targets from those lines (`SKILL-scripts.md` → *Light and vision*). Each send also prints who has **cover** from whom (`map_render: cover — Goblin 2 has half cover from Hernio`): +2 (half) or +5 (three quarters) to AC and Dexterity saves, and total cover means it cannot be targeted directly (PHB p. 196). Attacks from the phone take it from the last map sent and say it in the line (`il tiro vale 15` = the roll to compare with the AC) — so keep the map up to date when people move; `map_render.py --cover A B` for any other pair.

You write the layout and the tokens; the image backend paints the terrain's look, and the script puts grid, coordinates, tokens and effects on top in the exact squares. Add an `art:` header line — **English**, what the place looks like (materials, mood, light), no names or game terms — and keep it identical on every re-send of that place. A new place, or changed walls/water/terrain (a collapse is a terrain change; a fireball is an effect), takes 5–90 s: run that call as its **own Bash call with `run_in_background: true`** — a plain map appears at once and the painted one replaces it. Re-sending while it paints is fine: only the latest send ever shows its painted copy (an older one never comes back over it with old positions), and the same terrain is painted once — so don't re-send just to fix the picture. If the art fails, the plain map stays; carry on and never mention it in the fiction.

**Place scenes (`image_gen.py --kind scene`).** Generate one when the party first reaches a place and you describe it: a town, a tavern, a ruin, a dungeon mouth, a clearing. It's a wide establishing shot of the place itself, with no characters in it. Use `--subject` = the place's canonical name, so a return visit re-shows the cached picture instead of making a new one. Other kinds (`portrait`, `monster`, `action`, `item`) only when a player asks to see something specific.

**Region map (`region_map.py`).** Keep the map of the region up to date as the party discovers it: add a place when the characters first reach it or hear of it (`--rumor`), link it with the days of travel, and move the party with `travel` / `here` when they go somewhere (`--quiet`, or `mark "<place>" visited`, when the map should change without a picture in the story). A region where places are hours apart rather than days is counted in hours (`scale hours`). It is drawn by the script (no image backend), every player can open it, and it answers "how far is…?" for them. Syntax and rules: `SKILL-scripts.md` → *Region Map*.

**Prompts.** Write them in **English**: physical description only, with no names, game terms or text in the image. Describe **only the place**: materials, light, weather, mood (*low-beamed stone tavern, soot-black hearth, rain at the open door, candlelight*). Keep it to 12–30 words. Leave out animals, people, props and anything else a model could turn into the subject. A "stuffed wolf above the hearth" came back as a live wolf standing in the room. Pass `--negative "people, animals"` when the place is known for its creatures or crowds. Captions go in the campaign language.

Generation takes 5–60 s each: run every `image_gen.py` call as its **own Bash call with `run_in_background: true`**, then carry on narrating — the image lands in the feed when ready. Several can be queued at once. If `image_gen.py` fails or reports images are off, stop calling it for the rest of the session and never mention it in the fiction.

**Handouts (`handout.py`).** When the characters hold a written text and its wording matters — a letter, a wanted poster, a diary page, a riddle, a contract — hand it out instead of reading it in the narration: it appears as a sheet of paper and stays in the players' Documents list to be re-read. Write the document itself, in its author's voice. When it is written in a language some characters may not know — an elven inscription, a dwarven ledger, orders in Infernal — add `--language <language>`: only the characters whose sheet lists it read the words; the others see the shapes of the script and who at the table can read it, so the reading becomes a scene of its own (who translates, and how faithfully). *Comprehend languages* or a translation later: `handout.py --reader <id> <name>`. No image backend needed. Syntax: `SKILL-scripts.md` → *Handouts*.

---

## Tutor Mode

Enabled via `/dm:dnd tutor on`. Stored as `tutor_mode: true` in `state.md → ## Session Flags`. Check this flag on every `/dm:dnd load`. Session-scoped — does not persist unless explicitly set again.

**DM Help button vs Tutor Mode — these are separate:**
- The **◈ DM Help button** on the display fires a single one-shot hint via `dm_help.py`. It sends one `--tutor` block to the display, then stops. It does NOT set `tutor_mode: true` in state.md. It does NOT enable ongoing tutor sends from the DM.
- **Tutor Mode** (ongoing) is only active when `tutor_mode: true` is present in state.md. Check this flag at load; do not infer it from the presence of a tutor block in the display log.
- When a DM Help hint appears in context mid-session, do NOT start appending `--tutor` blocks to your own responses. Only do so if `tutor_mode: true` is set.

When active, append a `--tutor` send at the end of each Bash block for:

| Trigger | What to include |
|---------|----------------|
| Scene intro / new location | Skills worth attempting, what they'd reveal |
| Decision point | 2–3 visible options; note which close doors permanently |
| Before irreversible choice | Prefix `⚠ WARNING:` — renders in amber |
| After failed roll | Stat, DC, and the gap |
| Combat round end | Unused bonus actions, reactions, or features (the per-turn reminder of *Rules enforcement* is given anyway, tutor mode or not) |
| Spell / feature use | Range, duration, concentration conflicts |

Write from inside the fiction. 2–4 sentences. Never spoil undiscovered information. Omit if nothing is at stake.

```bash
# Warning variant (amber):
python3 ${CLAUDE_SKILL_DIR}/display/send.py --tutor << 'DNDEND'
⚠ WARNING: Moving the stone off the ship cannot be undone. Han-Ulish warned this would be read as invitation.
DNDEND

# Standard hint:
python3 ${CLAUDE_SKILL_DIR}/display/send.py --tutor << 'DNDEND'
There are at least two ways in — the front gate (visible, guarded) and the loading dock you passed (dark, unguarded).
DNDEND
```

The tutor block always goes **last** in the Bash send sequence.
