/**
 * Pick an Instagram post to exercise the download path with.
 *
 * Rather than hardcoding a shortcode that will eventually 404, the robot takes
 * whatever the signed-in session can see right now — same philosophy as TikTok
 * live discovery.
 */

const FEEDS = ["https://www.instagram.com/explore/", "https://www.instagram.com/"];

export async function discoverInstagramPost(context, { log = () => {} } = {}) {
  const page = await context.newPage();
  try {
    for (const feed of FEEDS) {
      try {
        const response = await page.goto(feed, { waitUntil: "domcontentloaded", timeout: 60000 });
        if (!response?.ok()) {
          log(`  ${feed} returned ${response?.status()}`);
          continue;
        }
        await page.waitForTimeout(5000);

        const hrefs = await page.$$eval("a[href*='/p/'], a[href*='/reel/']", (anchors) =>
          anchors.map((anchor) => anchor.getAttribute("href")).filter(Boolean),
        );
        const match = hrefs.find((href) => /\/(p|reel)\/[A-Za-z0-9_-]+/.test(href));
        if (match) {
          const url = new URL(match, "https://www.instagram.com").toString();
          log(`  found ${url}`);
          return url;
        }
        log(`  no post links on ${feed}`);
      } catch (error) {
        log(`  ${feed} failed: ${error.message}`);
      }
    }
    return null;
  } finally {
    await page.close().catch(() => {});
  }
}
