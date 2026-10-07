# Google Sheets add-on: paste a URL, get the product row

Paste a product page URL into a column of your sheet. A few seconds later the same row is filled with the
product's name, brand, SKU, price, currency, availability, description, dimensions, colour, finish and
material, the product image appears with `=IMAGE()`, and every cell the extractor was **not** confident about
is highlighted yellow so you know what to check by hand. Anything that was not found stays empty.

Everything runs in **your** Google account and talks only to **your** API. No keys or accounts belong to anyone else.

```
you paste a URL in column H  ->  onEdit trigger  ->  POST /extract on your API  ->  row filled in,
                                                                                    low-confidence cells yellow
```

## Before you start

You need the product-data-extractor API running somewhere **Google's servers can reach**. Apps Script runs on
Google's infrastructure, so `http://localhost:8000` cannot work, even if the API is running on your own computer.

- For real use, run the Docker image on any server or container host you control and put it behind HTTPS
  (see the main [README](../README.md#quickstart)).
- For a quick test from your own computer, any tunnelling tool that gives your local port a temporary public HTTPS
  address will do. Use it only while testing, and set `API_KEY` on the server (below) so strangers cannot use it.

## Setup (about five minutes)

1. **Open your Google Sheet** and give row 1 these headers, in this order (the default column mapping expects them):

   | A | B | C | D | E | F | G | H | I | J | K | L | M | N |
   |---|---|---|---|---|---|---|---|---|---|---|---|---|---|
   | Name | Brand | SKU | Price | Currency | Availability | Image | **Product URL** | Description | Dimensions | Colour | Finish | Material | Notes |

2. **Extensions → Apps Script.** A code editor opens in a new tab.
3. **Delete** whatever is in the editor, then **paste the whole of [`Code.gs`](Code.gs)**.
4. **Edit the `CONFIG` block at the very top** (it is the only part you need to touch):
   - `API_URL`: the address of your API, for example `https://extractor.yourdomain.com` (no trailing slash).
   - `API_KEY`: only if you set `API_KEY` on the server. For a sheet that other people can edit, leave this empty and
     use *Project Settings → Script properties → Add script property* with the name `API_KEY` instead, so editors
     cannot read the key from the code.
   - `URL_COLUMN`, `COLUMNS`, `SHEET_NAME`, `FIRST_DATA_ROW`: change these if your layout differs. To skip a field,
     delete its line from `COLUMNS`.
5. Click **Save** (disk icon).
6. **Install the trigger.** In the function drop-down choose `installTrigger`, then click **Run**.
   Google asks you to authorise the script:
   - It needs permission to *see and edit your spreadsheets* (to write the row) and to *connect to an external
     service* (to call your API).
   - Because you wrote this script yourself, you will see **"Google hasn't verified this app"**. Click
     **Advanced → Go to (project name) (unsafe) → Allow**. It is your own code in your own account.
7. **Try it.** Paste a product URL into `H2`. The Notes cell shows `Fetching…`, then `N fields found | …warnings`.

You can reload the sheet and use the new **Product extractor** menu: *Install automatic trigger*, *Extract selected
rows* (re-run rows you already filled) and *Remove automatic trigger*.

## What you will see

| In the sheet | Meaning |
|---|---|
| Yellow cell | The extractor reports **low** confidence for that field (an HTML guess or an LLM value). Check it. |
| Empty cell | The page did not state that value. Nothing was invented. |
| Price column | A real number, so you can sum, sort and filter it. The currency is in its own column. |
| Notes: `10 fields found \| …` | How many fields were found, followed by the API's warnings (for example, "No price was found on the page."). |
| Notes: `Error: …` | The request failed; the text is the API's own message (see below). |

Fields from structured data on the page (JSON-LD, microdata) are `high`, Open Graph is `medium`, and anything
read from loose HTML or by the LLM fallback is `low`. By default only `low` is highlighted; change
`HIGHLIGHT_CONFIDENCE` (for example to `['low', 'medium']`) to be stricter.

## Behaviour worth knowing

- **Only the URL column triggers it.** Editing any other cell does nothing, and so does pasting something that is
  not an `http(s)` link.
- **Pasting many URLs at once** processes up to `MAX_ROWS_PER_EDIT` (10) rows per paste; the rest are reported.
  Select the remaining rows and use *Product extractor → Extract selected rows*.
- **Re-pasting a different URL in a row replaces the old values**, including clearing fields the new page does not have.
- **The script runs as the person who installed the trigger.** Anyone who can edit the sheet can cause requests to
  your API under that person's name. Keep the API key out of the code for shared sheets (step 4).
- Google limits how many external requests a script may make per day and how long one run may take. See Google's
  Apps Script *quotas* page. The API is also rate-limited per domain on purpose, so pasting dozens of URLs from one
  shop is slow by design.

## Troubleshooting

| Notes cell says | What to do |
|---|---|
| `Set CONFIG.API_URL at the top of the script…` | You have not replaced the placeholder address in `CONFIG`. |
| `DNS error: …` or `Address unavailable: …` | Google cannot reach `API_URL`. It must be public (not `localhost`) and correct. |
| `unauthorized: Missing or wrong X-API-Key header.` | The server has `API_KEY` set; put the same value in `CONFIG.API_KEY` or in Script properties. |
| `blocked_address: …` | The URL points at a private/internal address. This protection is deliberate and cannot be switched off from the sheet. |
| `blocked_by_robots: …` | The site's robots.txt does not allow automated fetching of that page. |
| `fetch_failed: The site answered HTTP 403` | The shop blocks automated visitors. This tool does not try to get around that. |
| `invalid_url: …` | The cell does not contain a valid `http(s)` URL. |
| Nothing happens at all | The trigger is not installed (run `installTrigger` again), the edit was not in `URL_COLUMN`, or `SHEET_NAME` does not match the tab name. In the Apps Script editor open **Executions** to see errors. |

## Tests

The script's logic is tested without Google by loading `Code.gs` unmodified into Node with mocks of `SpreadsheetApp`,
`UrlFetchApp` and friends:

```bash
node --test apps_script/tests/code.test.js
```

(The same tests run as part of `make test` when Node is installed.) What cannot be tested outside Google is the
real authorisation screen and the real quotas; those are described above.
