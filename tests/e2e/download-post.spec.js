const { test, expect } = require("@playwright/test");

/**
 * Post URLs rot. The two hardcoded here before this rewrite were both dead by
 * August 2026 — TikTok answered them with a "Site Maintenance" page — and the
 * suite reported that as a download bug for months. So the URL now comes from
 * the environment, and the test skips rather than fails when the post is gone.
 *
 *   TIKTOK_POST_URL=https://www.tiktok.com/@someone/video/123 npm run test:e2e
 *
 * Find a current one with: node .robot-out/get-post-url3.mjs
 */
const POST_URL = process.env.TIKTOK_POST_URL;

test("downloads a real TikTok post", async ({ page }) => {
  test.skip(!POST_URL, "set TIKTOK_POST_URL to a current TikTok post to run this");

  await page.goto("/download");
  await page.getByLabel("TikTok post URL").fill(POST_URL);
  await page.getByRole("button", { name: "Download" }).click();

  const notice = page.locator("#post-download-notice");
  await expect(notice).not.toBeEmpty();

  // A post that has since been deleted is not a failure of this app.
  const noticeText = await notice.innerText();
  test.skip(
    /no longer available|not available on TikTok/i.test(noticeText),
    `the post at ${POST_URL} is gone: ${noticeText}`,
  );

  await expect(notice).toContainText("Post downloaded");

  const result = page.locator("#post-download-result");
  await expect(result).toContainText("Post download complete");
  await expect(result).toContainText("output/posts/");

  const fileLinks = result.locator("a.btn-primary");
  await expect(fileLinks.first()).toBeVisible();

  const response = await page.request.get(await fileLinks.first().getAttribute("href"));
  expect(response.ok()).toBeTruthy();
  expect(Number(response.headers()["content-length"] || "0")).toBeGreaterThan(0);
});
