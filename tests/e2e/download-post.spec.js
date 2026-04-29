const { test, expect } = require("@playwright/test");

const cases = [
  {
    name: "video post",
    url: "https://vt.tiktok.com/ZS9DQ1xkt/",
    expectedFilePattern: /\.mp4$/i
  },
  {
    name: "picture post",
    url: "https://vt.tiktok.com/ZS9DQ2HEL/",
    expectedFilePattern: /image-\d{3}\.jpg$/i
  }
];

for (const item of cases) {
  test(`downloads a real TikTok ${item.name}`, async ({ page }) => {
    await page.goto("/download");
    await page.getByLabel("TikTok post URL").fill(item.url);
    await page.getByRole("button", { name: "Download" }).click();

    await expect(page.locator("#post-download-notice")).toContainText("Post downloaded");

    const result = page.locator("#post-download-result");
    await expect(result).toContainText("Post download complete");
    await expect(result).toContainText("output/posts/");

    const fileLinks = result.locator("a.button-link");
    await expect(fileLinks.first()).toBeVisible();

    const matchingLink = fileLinks.filter({ hasText: item.expectedFilePattern });
    await expect(matchingLink.first()).toBeVisible();

    const response = await page.request.get(await matchingLink.first().getAttribute("href"));
    expect(response.ok()).toBeTruthy();
    expect(Number(response.headers()["content-length"] || "0")).toBeGreaterThan(0);
  });
}
