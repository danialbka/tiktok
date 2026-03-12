---
name: instagram-browser-upload
description: Use when the user wants to upload or publish an Instagram reel/post through the browser instead of the API, especially when a logged-in browser session, cookies, or saved Playwright storage state must be reused.
---

# Instagram Browser Upload

Use this skill when Instagram publishing needs to happen through the web UI instead of the API.

Use this workflow from the local project workspace. Keep session-state files local and out of git.

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
  - the left-nav profile link points to the expected handle
  - Instagram feed loads while signed in

Practical check:

```javascript
const profileHref = await page.locator('a[href^="/"][href$="/"]').evaluateAll(nodes =>
  nodes.map(node => node.getAttribute("href")).find(href => href && href !== "/" && href.split("/").length === 3)
);
console.log(profileHref);
```

## Upload Flow

1. From the home feed, click the visible `Create` entry in the left nav.
   - `page.locator('a:has-text("Create")').first().click()` worked reliably.
2. In the create popover, click the `Post` link.
   - `page.getByRole("link", { name: /Post/ }).first().click()`
3. Attach the video file by setting the existing file input directly.
   - `await page.locator('input[type="file"]').first().setInputFiles("/abs/path/video.mp4")`
   - In this flow the input already existed, so this was smoother than waiting for a chooser.
4. If Instagram shows the `Video posts are now shared as reels` prompt, click `OK`.
5. On the crop dialog, click the dialog-local `Next`.
6. On the edit dialog, click `Next` again.
7. On the final share dialog:
   - caption field: `getByRole("textbox", { name: /Write a caption/i })`
   - share button: `getByRole("button", { name: /^Share$/ })`
8. After clicking `Share`, do not click again if the page shows `Sharing`.
9. Poll until one of these appears:
   - `Reel shared`
   - `Your reel has been shared.`
   - or `Sharing` disappears

Known-good share loop:

```javascript
const dialog = page.locator('[role="dialog"]').first();
await dialog.getByRole("textbox", { name: /Write a caption/i }).fill("Blood Diamond 💎🎬");
await dialog.getByRole("button", { name: /^Share$/ }).click();

for (let i = 0; i < 24; i++) {
  await page.waitForTimeout(5000);
  const text = await page.locator("body").innerText();
  if (text.includes("Your reel has been shared.") || text.includes("Reel shared") || !text.includes("Sharing")) {
    break;
  }
}
```

## Known Working Caption Pattern

Simple title-plus-emojis works well:

```text
The Housemaid 🖤🎬
```

Also verified:

```text
Blood Diamond 💎🎬
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
- If the upload sits on `Sharing`, poll for up to a few minutes before assuming failure.
- If selectors are ambiguous, prefer `getByRole(...)` over plain text matching.
- For the `Create` opener specifically, `a:has-text("Create")` was more reliable than role/name matching.
- For the caption field, target the textbox named `Write a caption...` so you do not accidentally hit `Add location` or `Add collaborators`.
- Keep session files out of git.
