---
name: instagram-browser-upload
description: Use when the user wants to upload or publish an Instagram reel/post through the browser instead of the API, especially when a logged-in browser session, cookies, or saved Playwright storage state must be reused.
---

# Instagram Browser Upload

Use this skill when Instagram publishing needs to happen through the web UI instead of the API.

This workflow is written for the local environment at `/root/projects/realdebrid`. Keep session-state files local and out of git.

## Quick Start

1. Use `$playwright-interactive`.
2. Reuse existing `js_repl` Playwright bindings if they already exist.
3. Prefer loading a saved browser state file when one is available.
4. If the saved state is invalid, fall back to fresh cookies or ask the user to log in manually.

## Browser Bootstrap

If you need a fresh browser context, use a Chromium context and optionally load a saved storage state:

```javascript
var chromium;
var browser;
var context;
var page;

({ chromium } = await import("playwright"));
browser ??= await chromium.launch({ headless: true });
context = await browser.newContext({
  viewport: { width: 1440, height: 1200 },
  storageState: "/absolute/path/to/local-instagram-storage-state.json",
});
page = await context.newPage();
await page.goto("https://www.instagram.com/", { waitUntil: "domcontentloaded" });
```

## Verify Account

- Confirm the active account before uploading.
- Acceptable evidence:
  - profile/account picker shows the expected handle
  - Instagram feed loads while signed in

## Upload Flow

1. Open the create menu from the left nav.
   - A working entry was the link with accessible name like `New postCreate`.
2. Choose `Post`.
3. Attach the video file.
   - First try `page.waitForEvent("filechooser")`.
   - If no chooser appears, set the file on `input[type="file"]`.
4. If Instagram shows `Video posts are now shared as reels`, click `OK`.
5. In crop step, click `Next`.
6. In edit step, click `Next` again.
7. In the final details step:
   - caption field may be a `role="textbox"` element named `Write a caption...`
   - share button may be a `role="button"` named `Share`
8. After clicking `Share`, wait for either:
   - `Reel shared`
   - `Your reel has been shared.`

## Known Working Caption Pattern

Simple title-plus-emojis works well:

```text
The Housemaid 🖤🎬
```

## Persistence

- The current `js_repl` browser/page bindings stay usable only for the current Codex session.
- For later sessions, rely on a saved storage state file instead of assuming the old in-memory browser still exists.
- After a successful login or publish session, refresh the saved state:

```javascript
await context.storageState({
  path: "/absolute/path/to/local-instagram-storage-state.json",
});
```

## Failure Modes

- If Instagram asks for a password, the saved state is stale.
- If the upload gets stuck on `Sharing`, wait longer before assuming failure.
- If selectors are ambiguous, prefer `getByRole(...)` over plain text matching.
- Keep session files out of git.
