# n8n expressions cheatsheet

## Basics

| Need | Expression |
| --- | --- |
| Field of the current item | `{{ $json.email }}` |
| Key with space/dash | `{{ $json["first name"] }}`, `{{ $json["x-request-id"] }}` |
| Missing-safe | `{{ $json.customer?.address?.city ?? 'unknown' }}` |
| Matching item from an earlier node | `{{ $('Fetch order').item.json.total }}` |
| First / last / all items of a node | `{{ $('Fetch order').first().json.id }}`, `.last()`, `.all()` |
| All input items of this node | `{{ $input.all().length }}` |
| Item index | `{{ $itemIndex }}` |
| Binary file name | `{{ $binary.data.fileName }}` (the binary property name, often `data`) |
| Workflow / execution | `{{ $workflow.name }}`, `{{ $workflow.id }}`, `{{ $execution.id }}`, `{{ $execution.mode }}` |
| Resume URL (Wait node) | `{{ $execution.resumeUrl }}` |
| Instance variables | `{{ $vars.API_BASE }}` (Variables feature; plan-dependent) |
| Text with values | `=Hello {{ $json.name }}, your order {{ $json.id }} shipped` |
| Ternary | `{{ $json.total > 100 ? 'big' : 'small' }}` |
| Multi-line logic (keep rare) | `{{ (() => { const t = $json.total; return t > 100 ? t * 0.9 : t; })() }}` |

The whole field must be in Expression mode. In exported JSON that means the string
starts with `=`: `"text": "=Order {{ $json.id }}"`. Without `=` the braces are sent literally.

## Dates (Luxon)

`$now` and `$today` are Luxon DateTimes in the workflow timezone (Workflow settings >
Timezone, else `GENERIC_TIMEZONE`). `new Date()` is the server's UTC clock.

| Need | Expression |
| --- | --- |
| ISO timestamp now | `{{ $now.toISO() }}` |
| Yesterday's date | `{{ $today.minus({ days: 1 }).toISODate() }}` |
| Start of month | `{{ $now.startOf('month').toISO() }}` |
| Format | `{{ $now.toFormat('yyyy-LL-dd HH:mm') }}` (`LL` = month, `mm` = minutes) |
| Parse ISO string | `{{ DateTime.fromISO($json.created_at).toFormat('dd LLL yyyy') }}` |
| Parse custom format | `{{ DateTime.fromFormat($json.date, 'dd/MM/yyyy').toISODate() }}` |
| Unix seconds -> date | `{{ DateTime.fromSeconds($json.ts).toISO() }}` |
| To another zone | `{{ $now.setZone('Asia/Dhaka').toFormat('HH:mm') }}` |
| Difference in days | `{{ Math.floor($now.diff(DateTime.fromISO($json.due), 'days').days) }}` |
| String helper | `{{ $json.created_at.toDateTime().plus({ hours: 6 }).toISO() }}` |

Pitfall: `yyyy-MM-dd` in Luxon is correct (`MM` = month); `mm` is minutes. Moment-style
`YYYY` is wrong in Luxon (`yyyy`).

## Strings, numbers, arrays

| Need | Expression |
| --- | --- |
| Trim / lower | `{{ $json.email.trim().toLowerCase() }}` |
| Default for empty string | `{{ $json.name \|\| 'there' }}` (`??` keeps empty strings) |
| Number from string | `{{ Number($json.amount) }}`, `{{ parseFloat($json.amount.replace(',', '.')) }}` |
| Round to 2 decimals | `{{ Math.round($json.total * 100) / 100 }}` |
| Join array | `{{ $json.tags.join(', ') }}` |
| Pluck field from array | `{{ $json.items.map(i => i.sku).join(',') }}` |
| Filter + sum | `{{ $json.lines.filter(l => l.paid).reduce((s, l) => s + l.amount, 0) }}` |
| Object as text | `{{ JSON.stringify($json.payload) }}` |
| Is empty | `{{ $json.list?.length > 0 }}`, `{{ Object.keys($json.obj ?? {}).length === 0 }}` |
| Built-in helpers | `.isEmpty()`, `.extractEmail()`, `.toTitleCase()`, `.first()`, `.unique()`, `.sum()` on strings/arrays |

## JMESPath

`$jmespath(object, 'query')` - object first, query second:

```
{{ $jmespath($json, 'orders[?status==`paid`].id') }}
{{ $jmespath($('Fetch orders').all().map(i => i.json), '[].customer.email') }}
```

## JSONata / Make / Zapier translations

| JSONata / other | n8n |
| --- | --- |
| `$sum(items.price)` | `{{ $json.items.reduce((s, i) => s + i.price, 0) }}` or `{{ $json.items.map(i => i.price).sum() }}` |
| `items[price > 10]` | `{{ $json.items.filter(i => i.price > 10) }}` |
| `$string(x)` / `$number(x)` | `{{ String($json.x) }}` / `{{ Number($json.x) }}` |
| `$uppercase(s)` | `{{ $json.s.toUpperCase() }}` |
| `$now()` | `{{ $now.toISO() }}` |
| Make `formatDate(now; "YYYY-MM-DD")` | `{{ $now.toFormat('yyyy-LL-dd') }}` |
| Make `ifempty(a; b)` | `{{ $json.a \|\| $json.b }}` |
| Zapier "Formatter > Split text" | `{{ $json.text.split(',')[0] }}` |
| `a and b`, `a or b`, `not a` | `a && b`, `a \|\| b`, `!a` |

## Code node vs expression

| Expression | Code node |
| --- | --- |
| One value per field, per item | Many fields, loops, aggregation, parsing |
| `{{ }}` syntax | Plain JavaScript (no `{{ }}`) |
| `$json`, `$('Node').item` | All items mode: `for (const item of $input.all()) { item.json... }`; each-item mode: `$input.item.json` / `$json` |

Code node return value: an array of items `[{ json: {...} }]` (all-items mode) or one
item `{ json: {...} }` (each-item mode).

All-items template:

```javascript
const out = [];
for (const item of $input.all()) {
  const o = item.json;
  out.push({ json: { id: o.id, email: (o.email || '').toLowerCase() } });
}
return out;
```

`require('crypto')` and other modules need `NODE_FUNCTION_ALLOW_BUILTIN` /
`NODE_FUNCTION_ALLOW_EXTERNAL` on the instance (on the task runner container for external
runners). `$env` is blocked by default since n8n 2.0 (`N8N_BLOCK_ENV_ACCESS_IN_NODE`).

## Error messages

| Message | Usual cause | Fix |
| --- | --- | --- |
| `Referenced node doesn't exist` | Node renamed outside the editor, or typo | Update `$('Name')` |
| `Can't get data for expression` / node hasn't been executed | Referenced node is on another branch or after this one | Reference a node that ran before on this path; use `.first()` with "Execute Once" logic |
| `Paired item data unavailable` | Item linking broken by Code/Aggregate | `.first()`, `.all()[$itemIndex]`, or keep data with Set |
| `invalid syntax` | Python syntax, statements, unbalanced braces | One JS expression |
| Field shows `{{ $json.x }}` literally | Field is Fixed, not Expression | Toggle to Expression (`=` prefix) |
| `[object Object]` | Object put into a text field | `JSON.stringify(...)` or pick a sub-field |
| Date off by hours | `new Date()` (UTC) or wrong zone | `$now` / `setZone()` / set workflow timezone |
