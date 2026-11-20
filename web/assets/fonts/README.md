# Локальные шрифты интерфейса

Актуальная айдентика использует **Unbounded** для заголовков и **Montserrat** для основного текста. Шрифты поставляются вместе с фронтендом; обращения к внешним CDN не нужны. Involve сохранён для совместимости с прежними стилями.

## Unbounded

- Автор: NaN / The Unbounded Project Authors.
- Официальный исходник: https://github.com/googlefonts/unbounded
- Зафиксированный commit: `f3ec43228a864a72487e41552e2140efab9884ea`.
- Файл исходника: `fonts/variable/Unbounded[wght].ttf`.
- Локальный файл: `Unbounded-Variable.woff2`.
- Ось `wght`: 200–900. Кириллица и латиница сохранены полностью.
- Лицензия: SIL Open Font License 1.1, полный текст в `OFL-Unbounded.txt`.

## Montserrat

- Авторы: Julieta Ulanovsky, Sol Matas, Juan Pablo del Peral, Jacques Le Bailly / The Montserrat Project Authors.
- Официальный исходник: https://github.com/JulietaUla/Montserrat
- Зафиксированный commit: `555facfb2a18c72c3c0380f0d9c0f060453a9058`.
- Файл исходника: `fonts/variable/Montserrat[wght].ttf` (Version 9.000).
- Локальный файл: `Montserrat-Variable.woff2`.
- Ось `wght`: 100–900, включая используемые 400, 500, 600 и 700. Кириллица и латиница сохранены полностью.
- Лицензия: SIL Open Font License 1.1, полный текст в `OFL-Montserrat.txt`.
- SHA-256 исходного TTF: `1e2c44ef8c956ed66998649d8c832d221e22c04eaab63c044f18eb05d1c63b7c`.
- Проверены русские буквы, `Ё/ё`, `№`, `₽`, `←`, `↑`, `→` и `×`. Для `↶` используется системный fallback или SVG-иконка.

## Involve — совместимость с прежними стилями

- Автор: Stefan Peev / Involve Project Authors.
- Официальный исходник: https://github.com/StefanPeev/Involve
- Зафиксированный commit: `e257a5a23f1e53e782008f36d03d6c91449f832b`.
- Файл исходника: `fonts/Involve-VF.ttf` (Version 1.001).
- Локальный файл: `Involve-Variable.woff2`.
- Ось `wght`: 400–700. Кириллица и латиница сохранены полностью.
- Лицензия: SIL Open Font License 1.1, полный текст в `OFL-Involve.txt`.
- Copyright из метаданных: Involve © 2024 Stefan Peev; Evolventa © 2016 Alex I. Kuznetsov; URW Gothic L © 1996 URW.
- Стрелки (например, `→`) отсутствуют в Involve: используйте SVG-иконку, Unbounded или системный fallback.

## Подключение

Пути ниже рассчитаны на CSS-файл в каталоге `web/assets`.

```css
@font-face {
  font-family: "Unbounded";
  src: url("fonts/Unbounded-Variable.woff2") format("woff2");
  font-weight: 200 900;
  font-style: normal;
  font-display: swap;
}
@font-face {
  font-family: "Montserrat";
  src: url("fonts/Montserrat-Variable.woff2") format("woff2");
  font-weight: 100 900;
  font-style: normal;
  font-display: swap;
}
```

## Прежняя служебная подпись

Gilroy упоминался в прежнем макете, но не используется в актуальной айдентике. Его файлы не входят в комплект: лицензированный webfont не был предоставлен. Файл Involve и его лицензия сохранены; удалять их следует только вместе с зависимостью прежнего CSS от Involve.

## Обработка файлов

Исходные TTF преобразованы в WOFF2 через FontTools с Brotli. Контуры, набор символов,
метаданные авторства и диапазоны переменного начертания не изменялись; подмножества
не создавались. Сохраните файлы лицензий при передаче и публикации фронтенда.

## Контрольные суммы

| Файл | Байт | SHA-256 |
| --- | ---: | --- |
| `Involve-Variable.woff2` | 35332 | `f4ec922dc0804749b734b18e5b2f4e86d090967df542045eb2bf641206d813b0` |
| `Montserrat-Variable.woff2` | 216008 | `ff5f24630d3fb506d7eaff91286928e753a9870c5f72eb4d54f9db4268c6af0c` |
| `Unbounded-Variable.woff2` | 260732 | `f5bfd3e21b2c891bb17a1156a60ca095536592ec7831a74504133b1d78497b79` |
