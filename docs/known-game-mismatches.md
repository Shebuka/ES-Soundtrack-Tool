# Known game mismatches

Automatic matching is a fuzzy comparison, so a title that only extends the
query with a subtitle scores exactly 90.0 and a wrong sequel number can score
around 95. The candidate filter keeps both, which is why a handful of
soundtracks are still matched to a different game in the frozen dataset (build
20261117).

`scripts/rank_batch.py mismatches` regenerates this list from `data/` and prints
the same rows:

| url | soundtrack | matched game | game id | similarity |
|---|---|---|---|---|
| /music/237 | Command & Conquer: Red Alert 2 | Command And Conquer - Alerte Rouge 3 | 16222 | 85.4 |
| /music/648 | Final Fantasy X | Final Fantasy | 1252 | 95.0 |
| /music/798 | Mega Man X5 | Mega Man X | 554780 | 95.2 |
| /music/803 | Pokémon Crystal | Pokémon - Crystal Clear | 490544 | 85.5 |
| /music/804 | Pokémon Emerald | Pokémon Emerald Rogue | 419457 | 95.0 |
| /music/135 | Rainbow Islands: The Story of Bubble Bobble 2 | Rainbow Islands - The Story of Bubble Bobble 2 - Extra Version | 363256 | 95.0 |
| /music/688 | Sonic 3D Blast | Sonic Blast | 856 | 95.0 |
| /music/248 | Super Mario Bros. 3 | Super Mario Bros 35 | 497894 | 94.7 |

Do **not** hand-edit `soundtracks.json`: the next `es-ost-prep build`
recomputes matches and would undo the change. Fix the matching rule (or the
candidate filter) and rebuild instead.
