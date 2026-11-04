<!-- lang:en -->

Shows the 8 best heroes for your position in the hero selection screen. The list is recalculated after every enemy and teammate pick: it counts how strong each hero is against the picked enemies and together with your picked teammates.

- Data: https://dotapicker.com (current patch, no account needed) and, optionally, https://stratz.com. Both are downloaded when you patch; without internet the last downloaded copy is used.
- STRATZ: get a free token at https://stratz.com/api, paste it into `STRATZ API token` and press `Check STRATZ connection`. STRATZ adds its own `vs`/`with` columns, the hero's winrate on your position, real position statistics and the best items by winrate (click a hero to see them). STRATZ data is refreshed every 12 hours.
- Columns are grouped by source. `Total` combines both sources (vs enemies + with allies). The `||` button (or the `Split view` setting) shows two lists side by side instead: the top heroes by dotapicker and the top heroes by STRATZ, each ranked only by its own data, so you can pick the source you trust. The `<>` button stretches the panel and shows dotapicker's winrate columns and early/mid/late game strength.
- Position: placed automatically from your icon on the draft map (mid = 2, safe lane = 1/5, off lane = 3/4, jungle = 4). Core or support is decided by hero roles. Buttons `1`-`5` pick a position manually, `A` returns to automatic mode.
- Drag the title to move the panel, drag the bottom-right corner to scale it, drag the right edge to stretch it. `-` / `+` also scale it, `#` puts it back in place, `_` collapses it, `X` hides it. Size and place are remembered until Dota is restarted. The hotkey (default `F8`) shows/hides it. You can also bind any key in the console: `bind F8 minify_draft_helper_toggle`.
- Player info window: add `-netconport <port>` to Dota's launch options, enter the same port in `Dota console port`, enable `Player info window`, patch, then press `Open player info window`. A separate always-on-top window shows every player's ping, packet loss and connection state (read from the console's `status` in the background) plus their public STRATZ profile (rank, matches, winrate, smurf flag). Private profiles stay private. Run Dota in windowed or borderless mode to see it over the game.
- Main menu and lobby: the panel is also available there (small `DH` button at the right, or the hotkey). Outside a match it shows the best heroes by winrate for the chosen position and their best items.
- Hover a hero to see each matchup separately. `*` marks a situational pick for the position.

!!: Requires Workshop Tools. Map icon detection is experimental: if positions are wrong, enable `Debug output` in the settings and send the text from the debug box.

<!-- lang:ru -->

Показывает 8 лучших героев для вашей позиции на экране выбора героев. Список пересчитывается после каждого пика врага и тиммейта: учитывается, насколько герой силён против уже выбранных врагов и вместе с уже выбранными союзниками.

- Данные: https://dotapicker.com (текущий патч, без регистрации) и по желанию https://stratz.com. Скачиваются при патче, без интернета используется последняя скачанная копия.
- STRATZ: получите бесплатный токен на https://stratz.com/api, вставьте его в `STRATZ API token` и нажмите `Check STRATZ connection`. STRATZ добавляет свои столбцы `Пр.`/`С союз.`, винрейт героя на вашей позиции, реальную статистику позиций и лучшие предметы по винрейту (нажмите на героя, чтобы их увидеть). Данные STRATZ обновляются раз в 12 часов.
- Столбцы сгруппированы по источникам. `Итог` объединяет оба источника (против врагов + с союзниками). Кнопка `||` (или настройка `Split view`) показывает вместо этого два списка рядом: топ героев по dotapicker и топ по STRATZ, каждый только по своим данным, чтобы вы сами выбирали, какому источнику доверять. Кнопка `<>` растягивает панель и показывает столбцы винрейта dotapicker и силу героя в ранней, средней и поздней игре.
- Позиция определяется автоматически по вашей иконке на карте драфта (мид = 2, лёгкая линия = 1/5, сложная = 3/4, лес = 4). Кор или саппорт — по ролям героя. Кнопки `1`-`5` выбирают позицию вручную, `A` возвращает автоматический режим.
- Панель перетаскивается за заголовок, масштабируется за правый нижний угол и растягивается за правый край. `-` / `+` тоже масштабируют, `#` возвращает на место, `_` сворачивает, `X` скрывает. Размер и место запоминаются до перезапуска Доты. Горячая клавиша (по умолчанию `F8`) показывает и скрывает панель. Можно назначить любую клавишу через консоль: `bind F8 minify_draft_helper_toggle`.
- Окно игроков: добавьте `-netconport <порт>` в параметры запуска Доты, укажите тот же порт в `Dota console port`, включите `Player info window`, пропатчьте и нажмите `Open player info window`. Отдельное окно поверх всех покажет пинг, потери пакетов и состояние соединения каждого игрока (читается из `status` в фоне) и его открытый профиль STRATZ (ранг, матчи, винрейт, пометка о смурфе). Скрытые профили остаются скрытыми. Чтобы видеть окно поверх игры, запускайте Доту в окне или в режиме «без рамки».
- Главное меню и лобби: панель доступна и там (маленькая кнопка `DH` справа или горячая клавиша). Вне матча она показывает лучших героев по винрейту для выбранной позиции и их лучшие предметы.
- Наведите курсор на героя, чтобы увидеть каждый матчап отдельно. `*` — ситуативный пик для этой позиции.

!!: Нужны Workshop Tools. Определение позиции по иконке на карте экспериментальное: если позиции неверные, включите `Debug output` в настройках и пришлите текст из окна отладки.
