# API для фронтенда — шпаргалка

Для Маши. Полная схема: `docs/openapi.json`, интерактивно — `http://127.0.0.1:8000/docs`.
Все ответы и примеры ниже сняты с работающего сервера (ветка `nikita/backend-integration`).

Примеры запросов и ответов ниже иллюстративные; версия правил и значения конкретного запуска передаются сервисом в его снимке. Описание автоматического сопоставления ниже актуально для `rules-1.2.1`.

## 1. Подключение

| Что | Значение |
| --- | --- |
| Базовый адрес | `http://127.0.0.1:8000` (запуск: `./scripts/start.sh`) |
| Формат | JSON, UTF-8; загрузка файлов — `multipart/form-data` |
| CORS | По умолчанию разрешён любой порт `localhost` / `127.0.0.1` (Vite 5173, CRA 3000 и т. д.) — ничего настраивать не нужно |
| Другой адрес фронтенда | `KONTROL_CORS_ORIGINS=https://demo.example ./scripts/start.sh` (список через запятую заменяет правило по умолчанию) |
| Токен | Только если задан `KONTROL_API_TOKEN`: заголовок `Authorization: Bearer <токен>` |

```js
const API = 'http://127.0.0.1:8000';

async function api(path, options = {}) {
  const response = await fetch(API + path, options);
  const body = response.headers.get('content-type')?.includes('json') ? await response.json() : await response.blob();
  if (!response.ok) throw Object.assign(new Error(errorText(body)), { status: response.status, body });
  return body;
}
const json = (method, data) => ({ method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data) });
```

**Числа.** Количества и деньги приходят **строками** (`"1200.00"`, `"10"`), чтобы не терять копейки. Показывайте как есть, не считайте через `Number`.

## 2. Основной сценарий

```text
POST /api/cases                         → создать проверку (дело)
POST /api/cases/{case_id}/documents     → загрузить файлы (по одному запросу на файл)
POST /api/cases/{case_id}/runs          → запустить сверку → результат с расхождениями
POST /api/runs/{run_id}/signals/{signal_id}/reviews → статус + комментарий по расхождению
GET  /api/runs/{run_id}/export?format=pdf          → отчёт
```

### 2.1 Главная страница — список проверок

`GET /api/cases`

```json
[{
  "id": "65a5b5f84f8e478bbb3b9e0e55fd423a",
  "title": "Поставка мебели",
  "created_at": "2026-11-19T18:41:03.333694+00:00",
  "revision": 3,
  "document_count": 3,
  "last_run_id": "a2e053a0848f4d0ab950b53e61c460e3",
  "last_run_status": "needs_review",
  "last_run_at": "2026-11-19T18:41:03.364386+00:00",
  "run_count": 1,
  "last_run_stale": false,
  "description": "Поставка для городского парка",
  "contract_number": "ГК-2026/0142",
  "metadata_revision": 0,
  "updated_at": "2026-11-19T18:41:03.364386+00:00",
  "signal_count": 1,
  "reviewed_signal_count": 0,
  "pending_signal_count": 1
}]
```

- `last_run_status`: `ok` | `needs_review` | `incomplete` | `null` (ещё не проверяли).
- `last_run_stale: true` — после последней проверки документы менялись, нужно «Проверить заново».
- Счётчики `signal_count`, `reviewed_signal_count`, `pending_signal_count` относятся только к последнему запуску. Рассмотренным считается сигнал, у которого последнее решение в этом запуске имеет статус `confirmed`, `resolved` или `dismissed`. Повторные комментарии не увеличивают счётчик; возврат в `needs_review` уменьшает его. Решение из предыдущего запуска не переносится в эти показатели.
- `updated_at` — время последнего события дела, включая загрузку, изменение карточки, сверку и решение по сигналу.
- `contract_number` в реестре берётся из карточки. Если он пуст, используется единственный различающийся номер действующих документов типа `contract` (`number`, затем `contract_id`). Если таких номеров несколько или контрактов нет, возвращается пустая строка. Это подпись карточки, не подтверждение реквизита.

Создать дело: `POST /api/cases` с `{"title": "Поставка мебели"}` → `201`. Необязательные `description` и `contract_number` по умолчанию пустые. Ответ: `id`, `title`, `description`, `contract_number`, `created_at`, `updated_at`, `revision`, `metadata_revision`.

Сохранить карточку: `PUT /api/cases/{case_id}`:

```json
{
  "title": "Поставка уличных скамеек",
  "description": "Поставка для городского парка",
  "contract_number": "ГК-2026/0142",
  "expected_metadata_revision": 0
}
```

Все четыре поля обязательны. `title` — 1–300 символов, `description` — до 10 000, `contract_number` — до 300; крайние пробелы удаляются. Ответ содержит те же поля карточки, что создание, с новой `metadata_revision`. Если карточка уже изменена, возвращается `409` и нужно перечитать дело. Сохранение без фактических изменений не создаёт новую редакцию или событие.

`metadata_revision` начинается с 0 и защищает только карточку. `revision` по-прежнему относится к документам. Изменение названия, описания или номера карточки не меняет документы, результаты и признак `stale`. В журнал добавляется `case_updated` с изменёнными полями до/после. `GET /api/cases/{case_id}` возвращает сохранённые метаданные; в отличие от реестра, поле `contract_number` здесь не подменяется значением из документа. Чтобы исправить распознанный номер, используйте редактор документа.

### 2.2 Загрузка документов

`POST /api/cases/{case_id}/documents`, `multipart/form-data`:

| Поле | Обязательно | Что это |
| --- | --- | --- |
| `file` | да | PDF, DOCX, TXT, CSV, TSV, JSON, PNG, JPG, TIFF; до 20 МБ |
| `document_type` | нет | `contract`, `specification`, `delivery`, `acceptance`, `amendment` — если пользователь выбрал тип сам |
| `contract_id` | нет | номер контракта, если его нет в файле |
| `metadata` | нет | строка с JSON-объектом реквизитов |

```js
async function uploadFiles(caseId, files) {
  const results = [];
  for (const file of files) {            // по одному: так видно, какой файл упал
    const form = new FormData();
    form.append('file', file);
    try {
      results.push({ file: file.name, ok: true, data: await api(`/api/cases/${caseId}/documents`, { method: 'POST', body: form }) });
    } catch (error) {
      results.push({ file: file.name, ok: false, error: error.message });
    }
  }
  return results;
}
```

Ответ `201`:

```json
{
  "id": "doc-1f1c950bda15afae4904",
  "case_id": "65a5b5f84f8e478bbb3b9e0e55fd423a",
  "revision": 1,
  "document": { "id": "...", "type": "delivery", "number": "Н-17", "date": "2026-10-18", "items": [ ... ], "...": "..." },
  "duplicate": false,
  "warnings": [],
  "needs_review": false
}
```

- `duplicate: true` — тот же файл уже загружен; он **не** добавлен второй раз. Покажите плашку из `warnings[0]`.
- `needs_review: true` или непустой `warnings` — извлечение неуверенное (скан, нестандартный шаблон). Подсветите документ.
- Не ставьте заголовок `Content-Type` для `FormData` — браузер добавит его сам вместе с boundary.

### 2.3 Запуск проверки

`POST /api/cases/{case_id}/runs`

```json
{ "as_of": "2026-10-31", "delivery_complete": false }
```

`as_of` (дата среза, `YYYY-MM-DD`) обязательна. Остальное можно не передавать. Ответ `201` — полный запуск, тот же формат, что `GET /api/runs/{run_id}` (ниже).

### 2.4 Экран результатов

`GET /api/runs/{run_id}`

```json
{
  "id": "a2e053a0...", "case_id": "65a5b5f8...", "case_revision": 3, "created_at": "...",
  "stale": false,
  "request": { "as_of": "2026-10-31", "documents": [ ... ], "...": "..." },
  "result": {
    "status": "needs_review",
    "as_of": "2026-10-31",
    "signals":   [ /* расхождения, см. 2.5 */ ],
    "positions": [ /* таблица позиций */ ],
    "matches":   [ /* как связаны строки документов */ ],
    "audit":     [ /* какие документы учтены/исключены и почему */ ],
    "active_document_ids": [], "ignored_document_ids": [], "applied_amendment_ids": [],
    "engine_version": "rules-1.2.1", "input_hash": "...", "result_hash": "..."
  },
  "reviews": [ /* вся история решений по этому запуску */ ],
  "latest_reviews": { "sig_a7c5...": { "status": "confirmed", "reviewer": "Маша", "comment": "...", "created_at": "..." } },
  "prior_reviews":  { "sig_a7c5...": { "status": "confirmed", "run_id": "<прошлый запуск>", "...": "..." } }
}
```

| `result.status` | Подпись |
| --- | --- |
| `ok` | Расхождения не обнаружены в пределах выполненных проверок |
| `needs_review` | Нужна проверка |
| `incomplete` | Недостаточно данных |

Позиция (`result.positions[]`):

```json
{"line_id": "t1-r2", "name": "Скамья парковая", "unit": "шт",
 "expected_quantity": "10", "delivered_quantity": "8", "accepted_quantity": "10",
 "remaining_quantity": "2", "complete": true, "sources": [ ... ]}
```

`stale: true` — документы дела менялись после этого запуска. Покажите предупреждение и кнопку «Проверить заново».

#### Похожие номера договоров и артикулы

`result.matches[]` содержит `document_id`, `line_id`, `target_document_id`, `target_line_id`, `state`, `method`, `candidates` и `explanation`. Сопоставляйте строку с её документом по **обоим** ID: `line_id` уникален только внутри документа. Исходные значения берите из сохранённого `run.request.documents`, а не из изменившегося после запуска дела.

- `method: "similar_sku_auto"`, `state: "matched"` — единственный похожий артикул сопоставлен автоматически. Покажите исходный и целевой артикулы и `explanation`; это не подтверждает другие поля и не доказывает идентичность товара.
- `method: "similar_sku"`, `state: "needs_review"` — автоматическая связь не выбрана. `candidates` содержит ID возможных целевых строк; предложите выбор без предварительного подтверждения. Отправка ручной связи требует `document_id`, `line_id`, `target_line_id`, `reviewer` и `comment` в `manual_links` следующего запуска.
- `result.audit[]` может содержать `SIMILAR_SKU` или `SIMILAR_CONTRACT_ID`: `document_ids` и `explanation` сохраняют основание автоматической связи. Сигнал `AMBIGUOUS_CONTRACT_ID` блокирует сравнение при более чем трёх неоднозначных заменах в номере договора; исправьте и подтвердите реквизит по оригиналу, затем выполните новую проверку.

Для автоматической связи нужен единственный вариант с не более чем тремя неоднозначными заменами `0/O/О`, `1/I/l` и без противоречия номера позиции; одинаково выглядящие латинские и кириллические буквы в лимит не входят. Разные цены, количества и другие реквизиты проверяются отдельно. Несколько кандидатов, конфликт позиций или больше трёх неоднозначных замен не подтверждаются автоматически. Прежний результат после изменения документа сохраняется как `stale`; повторная сверка создаёт новый снимок.

### 2.5 Карточка расхождения

`result.signals[]`:

```json
{
  "id": "sig_a7c52879ac2cfa87975c",
  "code": "QUANTITY_MISMATCH",
  "severity": "warning",
  "title": "Приёмка превышает документы поставки",
  "explanation": "В актах указано больше единиц, чем в учтённых накладных. ...",
  "rule": "Σ принято по действующим incremental актам > Σ поставлено по действующим накладным.",
  "expected": "8",
  "actual": "10",
  "position_id": "t1-r2",
  "status": "needs_review",
  "blocks_comparison": false,
  "sources": [
    {"document_id": "doc-1f1c950bda15afae4904", "filename": "delivery_8.csv", "page": null, "row": 9,
     "field": "quantity", "excerpt": "1 | BENCH-01 | Скамья уличная | шт. | 8 | 1 200,00 | 9 600,00"}
  ]
}
```

- `severity`: `info` | `warning` | `error` — цвет карточки.
- `expected` / `actual` — строка, число-строка, объект или `null`. Для объектов выводите пары ключ-значение.
- `blocks_comparison: true` — без исправления этих данных сверка неполная; поднимайте такие карточки наверх.
- **Текущий статус карточки** берите так: `latest_reviews[signal.id]?.status ?? signal.status`. Поле `signal.status` — исходный вывод алгоритма, оно не меняется.
- **Подсказка с прошлого запуска.** Если есть `prior_reviews[signal.id]`, этот же вывод уже рассматривали в прошлой проверке (id сигнала — хеш его содержания). Покажите «Ранее: подтверждено, Маша — комментарий» и кнопку «Применить», которая отправит такое же решение в текущий запуск. Автоматически решение не переносится.

### 2.6 Ссылки на исходные документы

У каждого `sources[]` есть `document_id`, `page`, `row`, `field`, `excerpt` (цитата из файла).

- Скачать/открыть оригинал: `GET /api/cases/{case_id}/documents/{document_id}/file` — отдаёт исходный файл с правильным именем. Для структурированных и демо-документов файла нет → `404`; тогда показывайте только `excerpt`.
- Реквизиты документа (номер, дата, строки) — в `GET /api/cases/{case_id}` → `documents[].document`.

```js
async function openSource(caseId, documentId) {
  const blob = await api(`/api/cases/${caseId}/documents/${documentId}/file`);
  window.open(URL.createObjectURL(blob), '_blank');      // PDF/картинки откроются во вкладке
}
```

### 2.7 Изменение статуса и комментарий

`POST /api/runs/{run_id}/signals/{signal_id}/reviews`

```json
{ "status": "confirmed", "reviewer": "Маша", "comment": "Сверено с бумажным актом" }
```

| `status` | Подпись |
| --- | --- |
| `needs_review` | Требует рассмотрения |
| `confirmed` | Подтверждено (расхождение реальное) |
| `resolved` | Устранено |
| `dismissed` | Отклонено (ложный сигнал) |

Все три поля обязательны, `comment` и `reviewer` — непустые. **Комментарий без смены статуса** — отправьте текущий статус и новый комментарий. Каждая запись добавляется в историю, ничего не перезаписывается. Ответ `201`:

```json
{"sequence": 1, "run_id": "...", "signal_id": "sig_...", "status": "confirmed",
 "comment": "Сверено с бумажным актом", "reviewer": "Маша", "created_at": "2026-11-19T18:36:15.315464+00:00"}
```

История комментариев карточки: `reviews.filter(r => r.signal_id === signal.id)`.

### 2.8 Исправление распознанных реквизитов

`PUT /api/cases/{case_id}/documents/{document_id}` — для формы «Реквизиты». Обязательны `document` (весь документ целиком), `expected_revision` (текущий `revision` документа), `reviewer`, `comment`; подтверждённые пользователем поля — в `confirmed_fields` и `confirmed_items: {"line_id": ["quantity"]}`. Если кто-то изменил документ раньше — `409`, перечитайте дело. История правок: `GET /api/cases/{case_id}/documents/{document_id}/history`. Подробности — README, раздел «Интеграция с интерфейсом».

## 3. История и журнал

**Журнал проверок (все дела):** `GET /api/runs?case_id=&status=&limit=100&offset=0`

```json
{"total": 1, "limit": 100, "offset": 0, "items": [{
  "id": "a2e053a0...", "case_id": "65a5b5f8...", "case_title": "Поставка мебели",
  "case_revision": 3, "created_at": "...", "status": "needs_review", "as_of": "2026-10-31",
  "signal_count": 1, "review_count": 0, "result_hash": "...", "stale": false}]}
```

Те же сводки (без `case_title`, `review_count`) лежат в `GET /api/cases/{case_id}` → `runs[]`, новые сверху.

**История действий по делу:** `GET /api/cases/{case_id}/events`

Фильтры: `action` (можно несколько: `?action=signal_reviewed&action=check_completed`), `since` (ISO-время), `after_sequence` (для догрузки новых), `limit`.

```json
{"sequence": 13, "case_id": "...", "action": "signal_reviewed", "created_at": "...",
 "payload": {"run_id": "...", "signal_id": "sig_...", "status": "confirmed", "comment": "Проверено", "reviewer": "Маша"}}
```

| `action` | Подпись |
| --- | --- |
| `case_created` | Создана проверка |
| `case_updated` | Изменены данные карточки проверки |
| `document_added` | Загружен документ |
| `duplicate_upload` / `duplicate_document` | Повторная загрузка (не учтена) |
| `extraction_reviewed` | Исправлены реквизиты |
| `check_completed` | Выполнена сверка |
| `signal_reviewed` | Решение по расхождению |
| `report_exported` | Выгружен отчёт |

## 4. Экспорт

`GET /api/runs/{run_id}/export?format=pdf|html|csv|json` — файл с заголовком `Content-Disposition` (доступен фронтенду через CORS).

```js
async function download(runId, format = 'pdf') {
  const response = await fetch(`${API}/api/runs/${runId}/export?format=${format}`);
  if (!response.ok) throw new Error(errorText(await response.json()));
  const name = /filename="([^"]+)"/.exec(response.headers.get('content-disposition') ?? '')?.[1] ?? `check.${format}`;
  const link = Object.assign(document.createElement('a'), { href: URL.createObjectURL(await response.blob()), download: name });
  link.click();
  URL.revokeObjectURL(link.href);
}
```

PDF содержит кириллицу, список документов, позиции, расхождения с цитатами источников, последние решения и историю рассмотрения.

## 5. Ошибки

Тело ошибки всегда `{"detail": ...}`, но `detail` бывает двух видов:

- **строка** — понятное сообщение по-русски, можно показывать как есть;
- **массив** (ошибки валидации `422`) — `[{"loc": ["body", "as_of"], "msg": "Field required", "type": "missing"}, ...]`.

```js
function errorText(body) {
  if (!body || !body.detail) return 'Неизвестная ошибка';
  if (typeof body.detail === 'string') return body.detail;
  return body.detail.map(e => `${e.loc.filter(p => p !== 'body').join('.')}: ${e.msg}`).join('; ');
}
```

| Код | Когда | Что показать |
| --- | --- | --- |
| `401` | задан токен, а заголовка нет | запросить токен |
| `404` | нет дела/запуска/документа/сигнала или у документа нет исходного файла | «Не найдено» (+ `detail`) |
| `409` | документ уже изменён кем-то ещё; занятый `id` документа | перечитать данные и повторить |
| `413` | файл больше 20 МБ | «Файл слишком большой» |
| `415` | неподдерживаемый формат | `detail` |
| `422` | пустой файл, не распознан, неверные поля, `OCR_LANGUAGE_MISSING` | `errorText(body)` у конкретного поля/файла |
| `501` | на сервере не установлен reportlab (PDF) | «PDF недоступен, выгрузите HTML» |

## 6. Демо-данные

`GET /api/demo` — 12 синтетических сценариев; `POST /api/demo/T02` создаёт дело и возвращает `{"case": {...}, "options": {...}}`. Дальше: `POST /api/cases/{case.id}/runs` с телом `options`. Удобно, чтобы верстать экраны без загрузки файлов.

## 7. Статические файлы интерфейса

`GET /` отдаёт `web/index.html`. Пути `/assets/...` обслуживаются из `web/assets/` (CSS, JavaScript, шрифты). Внешний сервер статики для запуска через `scripts/start.sh` не требуется.
