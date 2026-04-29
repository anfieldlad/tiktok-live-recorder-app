# Feature Plan: Download TikTok Video and Picture Posts

## Summary

Add a separate app workflow for downloading public TikTok video posts and picture carousel posts from user-provided URLs.

This feature should complement the existing TikTok Live recording workflow, not replace or merge with it. The current app records live streams; this feature saves already-published public posts.

## Product Goal

Let a user paste a TikTok post URL, inspect basic post metadata, and download the media locally through the web UI.

Supported post types for v1:

- Public TikTok video posts
- Public TikTok picture carousel posts
- TikTok desktop URLs and mobile/share URLs

Out of scope for v1:

- Private or restricted posts
- Authentication bypass
- Bulk account/feed downloading
- Downloading all posts from a profile
- No-watermark options
- Reposting, editing, or remix workflows

## User Flow

1. User opens a new `Download Post` page.
2. User pastes a TikTok post URL.
3. App validates the URL format.
4. App fetches post metadata when possible:
   - author
   - caption
   - post type
   - thumbnail or preview
   - estimated output files
5. User starts the download.
6. App saves media under `output/`.
7. UI shows download status and completed files.
8. User downloads the resulting file or files from the browser.

## MVP Behavior

For video posts:

- Download the video as one media file.
- Save useful metadata when available.
- Show a clear error if the post is private, unavailable, invalid, or unsupported.

For picture posts:

- Download each image in the carousel.
- Prefer returning a single `.zip` for browser download, while optionally keeping individual images in the output directory.
- Save useful metadata when available.

For all downloads:

- Use one download job per submitted URL.
- Track job status in local JSON, consistent with existing `JobStore` and `WatchStore` patterns.
- Keep a download history in the UI.
- Avoid logging sensitive session or cookie values.

## Proposed App Structure

Potential new files:

```text
app/api/downloads.py
app/models/download.py
app/services/download_store.py
app/services/post_download_service.py
app/templates/download.html
app/static/js/download-page.js
```

Potential existing files to update:

```text
app/main.py
app/templates/base.html
app/static/css/app.css
app/services/config.py
tests/
```

## Backend API Sketch

Create a download:

```http
POST /downloads
Content-Type: application/json

{
  "url": "https://www.tiktok.com/@example/video/1234567890"
}
```

List downloads:

```http
GET /downloads
```

Get one download:

```http
GET /downloads/{download_id}
```

Download output file:

```http
GET /downloads/{download_id}/files/{file_index}
```

Delete a download and its local files:

```http
DELETE /downloads/{download_id}
```

## Download Engine Recommendation

Use `yt-dlp` behind a dedicated service layer for v1.

Reasons:

- It already handles many TikTok post URL variants.
- It can extract metadata and media URLs.
- It avoids custom TikTok extraction logic in this app.
- It can be upgraded independently when TikTok behavior changes.

Implementation note:

- Keep all `yt-dlp` calls inside `PostDownloadService`.
- Do not call `yt-dlp` directly from API handlers.
- Treat `yt-dlp` as an external engine, similar to how the app treats the upstream live recorder.

## Configuration Ideas

Potential `.env` settings:

```env
POST_DOWNLOADS_FILE=data/post_downloads.json
POST_DOWNLOAD_OUTPUT_DIR=output/posts
POST_DOWNLOAD_ENGINE=yt-dlp
POST_DOWNLOAD_MAX_ACTIVE=1
POST_DOWNLOAD_DELETE_AFTER_BROWSER_DOWNLOAD=false
```

The default can reuse `OUTPUT_DIR` if a separate post output directory feels unnecessary.

## Data Model Ideas

Download job fields:

- `id`
- `url`
- `status`
- `post_type`
- `author`
- `caption`
- `created_at`
- `started_at`
- `finished_at`
- `error`
- `output_files`
- `metadata_file`

Suggested statuses:

- `queued`
- `running`
- `finished`
- `failed`
- `deleted`

## UI Notes

Add a navigation item such as `Download Post`.

The page should include:

- URL input
- download/start button
- current job status
- completed files list
- clear errors for invalid/private/unavailable posts
- compact history list

Keep the UI consistent with the existing `Record Now` and `Watch Mode` pages.

## Testing Plan

Unit tests should cover:

- URL validation
- download store read/write behavior
- corrupt JSON recovery
- failed download state transitions
- output file listing
- API validation errors

Integration-style tests should cover:

- `POST /downloads` with missing URL returns a structured 422 error
- `GET /downloads` returns stored jobs
- completed jobs expose downloadable files

Avoid tests that require live TikTok network access. Mock the download engine boundary instead.

## Open Product Decisions

- Should picture posts download as individual images, a `.zip`, or both?
- Should completed downloads be deleted after browser download, like recordings?
- Should captions and metadata always be saved next to media files?
- Should the app show a metadata preview before starting download, or start immediately after URL submission?
- Should `yt-dlp` be a required dependency or an optional dependency with a clearer setup step?

## Recommended V1

Build a new `Download Post` page backed by `yt-dlp`, with support for one-off public video and picture carousel URLs. Save files locally, track download jobs in JSON, and provide browser download links. Keep private posts, profile bulk downloads, and no-watermark behavior out of the first release.

## Incremental Technical Implementation Plan

Goal: ship the smallest version that actually works first, then improve it in controlled steps.

### Phase 1: Smallest Working Feature

This phase should download one public TikTok post from a URL using a backend API. No UI, no persistence, no history, no unit tests yet.

Checklist:

- [x] Add `yt-dlp` to `requirements.txt`.
- [x] Create `app/services/post_download_service.py`.
- [x] Implement a simple `PostDownloadService.download(url: str) -> PostDownloadResult`.
- [x] Save output files under `output/posts/`.
- [x] Use a safe generated folder name per download, such as timestamp plus short random id.
- [x] Reject obviously invalid URLs before calling `yt-dlp`.
- [x] Create `app/api/downloads.py`.
- [x] Add `POST /downloads` with request body `{ "url": "..." }`.
- [x] Run the download synchronously for this first slice.
- [x] Return a simple response with status and local output file names.
- [x] Register the router in `app/main.py`.
- [x] Manually test with one public TikTok video URL.
- [x] Manually test with one public TikTok picture post URL.
- [x] Verify `yt-dlp` is installed in `.venv`.
- [x] Smoke test invalid URL handling.
- [x] Smoke test successful API response path with a mocked download service.
- [x] Run `.venv/bin/python -m unittest discover -s tests`.

Manual test note:

- 2026-04-29: Initial test of `https://vt.tiktok.com/ZS9DQ1xkt/` returned `HTTP Error 403: Forbidden` through the stable `yt-dlp` path.
- Fixed by using the newest available `yt-dlp` dev build, adding `curl-cffi`, enabling browser impersonation, clearing stale `yt-dlp` cache per attempt, and passing saved TikTok cookies when configured.
- Retest succeeded and produced:
  - `output/posts/20260429-044155-531b74/Al Masjid an Nabawi-7430349171061804293.info.json`
  - `output/posts/20260429-044155-531b74/Al Masjid an Nabawi-7430349171061804293.mp4`
- 2026-04-29: Tested picture post `https://vt.tiktok.com/ZS9DQ2HEL/`.
- `yt-dlp` does not support TikTok `/photo/...` URLs directly, so the service falls back to a photo metadata endpoint for image posts.
- Retest succeeded and produced `metadata.json` plus 11 image files under `output/posts/20260429-044802-028ef0/`.

Expected result:

- A user or developer can call `POST /downloads`.
- A public TikTok post gets saved to `output/posts/...`.
- The API returns enough information to know where the downloaded files are.

Example response:

```json
{
  "status": "finished",
  "files": [
    "output/posts/20260429-abc123/video.mp4"
  ]
}
```

Notes:

- Keep this intentionally rough.
- Do not build a queue yet.
- Do not build history yet.
- Do not build browser download links yet.
- Do not add complicated metadata parsing yet.

### Phase 2: Browser Download Links

This phase makes the downloaded files usable from the web app/API.

Checklist:

- [x] Introduce a download id in the API response.
- [x] Keep completed download results in memory for the running app process.
- [x] Add `GET /downloads/{download_id}`.
- [x] Add `GET /downloads/{download_id}/files/{file_index}`.
- [x] Return `FileResponse` for downloaded media.
- [x] Prevent path traversal by only serving files known to the download result.
- [x] Manually test that a browser can download a video file.
- [x] Manually test that picture post files are downloadable.
- [x] Smoke test `POST /downloads` returns file URLs with a mocked download result.
- [x] Smoke test `GET /downloads/{download_id}` with a mocked download result.
- [x] Smoke test `GET /downloads/{download_id}/files/{file_index}` streams a known file.
- [x] Smoke test missing file index returns 404.
- [x] Run `.venv/bin/python -m unittest discover -s tests`.

Manual test note:

- 2026-04-29: Retested video URL `https://vt.tiktok.com/ZS9DQ1xkt/`; `GET /downloads/{download_id}/files/1` returned `200` and streamed a 2.9 MB MP4.
- 2026-04-29: Retested picture URL `https://vt.tiktok.com/ZS9DQ2HEL/`; `GET /downloads/{download_id}/files/1` returned `200` and streamed the first JPG image.

Expected result:

- `POST /downloads` creates a download and returns an id.
- The user can fetch files through the app instead of browsing the filesystem.

### Phase 3: Minimal UI Page

This phase adds the smallest usable UI.

Checklist:

- [x] Add `app/templates/download.html`.
- [x] Add `app/static/js/download-page.js`.
- [x] Add a `Download Post` route in `app/main.py`.
- [x] Add a navigation item in `app/templates/base.html`.
- [x] Build a URL input and download button.
- [x] Show loading state while the request is running.
- [x] Show success state with file links.
- [x] Show API error messages clearly.
- [x] Reuse existing CSS patterns from `app/static/css/app.css`.
- [x] Manually test from the browser with one video post.
- [x] Manually test from the browser with one picture post.
- [x] Smoke test `/download` returns the page with the form and script.
- [x] Smoke test running app serves `/download` over HTTP.

Manual test note:

- 2026-04-29: Verified `/download` renders the `Download Post` page, includes `post-download-form`, and loads `download-page.js`.
- 2026-04-29: Verified the running app serves `/download` over HTTP.
- The page uses the Phase 1 and Phase 2 API paths that were manually verified with real TikTok video and picture URLs.
- 2026-04-29: Added Playwright e2e tests and verified browser-click flow with real video URL `https://vt.tiktok.com/ZS9DQ1xkt/` and real picture URL `https://vt.tiktok.com/ZS9DQ2HEL/`.
- Run with `npm run test:e2e`.

Expected result:

- A non-technical user can paste a TikTok post URL and download the result through the browser.

### Phase 4: Persistent Download Store

This phase makes downloads visible after refresh/restart and aligns with existing app patterns.

Checklist:

- [ ] Create `app/models/download.py`.
- [ ] Create `app/services/download_store.py`.
- [ ] Persist download jobs to `data/post_downloads.json`.
- [ ] Follow the recovery behavior used by `JobStore` and `WatchStore`.
- [ ] Add config values in `app/services/config.py`.
- [ ] Save job fields:
  - [ ] `id`
  - [ ] `url`
  - [ ] `status`
  - [ ] `created_at`
  - [ ] `started_at`
  - [ ] `finished_at`
  - [ ] `error`
  - [ ] `output_files`
- [ ] Add `GET /downloads` for history.
- [ ] Update UI to render download history.
- [ ] Manually test app restart after a completed download.

Expected result:

- Completed and failed download jobs survive page refresh and app restart.

### Phase 5: Safer Job Execution

This phase prevents long downloads from blocking request handling.

Checklist:

- [ ] Change `POST /downloads` to create a queued job.
- [ ] Run the actual download in a background thread.
- [ ] Return quickly with `{ "id": "...", "status": "queued" }`.
- [ ] Add status polling from the UI.
- [ ] Limit active post downloads to 1 for v1.
- [ ] Mark queued/running jobs as failed or interrupted on app startup if needed.
- [ ] Ensure failed `yt-dlp` subprocesses produce clear user-facing errors.

Expected result:

- The UI remains responsive while a post downloads.
- Users can watch progress at a coarse job-status level.

### Phase 6: Picture Post Packaging

This phase improves picture carousel UX.

Checklist:

- [ ] Detect when a result contains multiple image files.
- [ ] Create a `.zip` for picture posts.
- [ ] Expose the `.zip` as the primary browser download.
- [ ] Optionally keep individual image links visible.
- [ ] Store the `.zip` path in the download job.
- [ ] Handle filename collisions safely.

Expected result:

- Picture posts are easy to download as one browser file.

### Phase 7: Metadata

This phase adds useful post context without making it required for downloading.

Checklist:

- [ ] Ask `yt-dlp` for JSON metadata.
- [ ] Store metadata as `metadata.json` next to media files.
- [ ] Extract basic fields:
  - [ ] author
  - [ ] caption/title
  - [ ] post type when available
  - [ ] thumbnail when available
- [ ] Show basic metadata in the UI.
- [ ] Avoid failing the whole download if metadata fields are missing.

Expected result:

- The user can identify downloaded posts from history.

### Phase 8: Unit and API Tests

Tests come after the first working implementation, but before treating the feature as stable.

Checklist:

- [ ] Unit test URL validation.
- [ ] Unit test `DownloadStore` persistence.
- [ ] Unit test corrupt JSON recovery.
- [ ] Unit test file listing from completed downloads.
- [ ] API test `POST /downloads` missing URL returns structured 422.
- [ ] API test invalid URL returns structured error.
- [ ] API test completed job file endpoint returns a file.
- [ ] Mock `PostDownloadService` or the `yt-dlp` boundary.
- [ ] Avoid tests that call live TikTok.
- [ ] Run `.venv/bin/python -m unittest discover -s tests`.

Expected result:

- Core behavior is covered without relying on TikTok network availability.

### Phase 9: Cleanup and Retention

This phase decides how files are cleaned up.

Checklist:

- [ ] Decide whether downloads are deleted after browser download.
- [ ] Add `DELETE /downloads/{download_id}`.
- [ ] Delete job metadata and local files safely.
- [ ] Add optional max-age cleanup config.
- [ ] Add UI delete action.
- [ ] Add tests for delete behavior.

Expected result:

- Users can manage local disk usage.

### Phase 10: Documentation

Checklist:

- [ ] Update `README.md` with setup changes for `yt-dlp`.
- [ ] Document the `Download Post` page.
- [ ] Document supported and unsupported TikTok post types.
- [ ] Document API examples.
- [ ] Update `AGENTS.md` if new test or run commands are introduced.

Expected result:

- A future maintainer can install, run, test, and use the feature.

## Suggested Implementation Order

1. Phase 1: smallest working API download.
2. Phase 2: browser file endpoints.
3. Phase 3: minimal UI.
4. Phase 4: persistent store.
5. Phase 5: background execution.
6. Phase 8: tests for the stabilized boundary.
7. Phase 6 and Phase 7: picture packaging and metadata.
8. Phase 9 and Phase 10: cleanup and docs.

This order intentionally proves that downloading works before investing in storage, UI polish, background workers, or test scaffolding.
