<!-- lang:en -->

Shows the 8 best heroes for your position in the hero selection screen. The list is recalculated after every enemy and teammate pick: it counts how strong each hero is against the picked enemies and together with your picked teammates.

- Data: https://dotapicker.com (current patch). It is downloaded when you patch; without internet the last downloaded copy is used.
- Columns: `Total` = `vs` (advantage against enemies) + `with` (synergy with allies), `WR vs` is the winrate-based counter score, `WR` is the hero's overall winrate. The `<>` button stretches the panel and shows early/mid/late game strength.
- Position: placed automatically from your icon on the draft map (mid = 2, safe lane = 1/5, off lane = 3/4, jungle = 4). Core or support is decided by hero roles. Buttons `1`-`5` pick a position manually, `A` returns to automatic mode.
- `-` / `+` scale the panel, `_` collapses it, `X` hides it. The hotkey (default `F8`) shows/hides it. You can also bind any key in the console: `bind F8 minify_draft_helper_toggle`.
- Hover a hero to see each matchup separately. `*` marks a situational pick for the position.

!!: Requires Workshop Tools. Map icon detection is experimental: if positions are wrong, enable `Debug output` in the settings and send the text from the debug box.

<!-- lang:ru -->

Показывает 8 лучших героев для вашей позиции на экране выбора героев. Список пересчитывается после каждого пика врага и тиммейта: учитывается, насколько герой силён против уже выбранных врагов и вместе с уже выбранными союзниками.

- Данные: https://dotapicker.com (текущий патч). Скачиваются при патче, без интернета используется последняя скачанная копия.
- Столбцы: `Итог` = `Пр.` (преимущество против врагов) + `С союз.` (синергия с союзниками), `WR пр.` — контрпик по винрейту, `WR` — общий винрейт героя. Кнопка `<>` растягивает панель и показывает силу героя в ранней, средней и поздней игре.
- Позиция определяется автоматически по вашей иконке на карте драфта (мид = 2, лёгкая линия = 1/5, сложная = 3/4, лес = 4). Кор или саппорт — по ролям героя. Кнопки `1`-`5` выбирают позицию вручную, `A` возвращает автоматический режим.
- `-` / `+` масштабируют панель, `_` сворачивает, `X` скрывает. Горячая клавиша (по умолчанию `F8`) показывает и скрывает панель. Можно назначить любую клавишу через консоль: `bind F8 minify_draft_helper_toggle`.
- Наведите курсор на героя, чтобы увидеть каждый матчап отдельно. `*` — ситуативный пик для этой позиции.

!!: Нужны Workshop Tools. Определение позиции по иконке на карте экспериментальное: если позиции неверные, включите `Debug output` в настройках и пришлите текст из окна отладки.
