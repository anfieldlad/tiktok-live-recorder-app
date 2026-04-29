const { defineConfig } = require("@playwright/test");

module.exports = defineConfig({
  testDir: "./tests/e2e",
  timeout: 240000,
  expect: {
    timeout: 180000
  },
  use: {
    baseURL: "http://127.0.0.1:8000",
    trace: "on-first-retry"
  },
  webServer: {
    command: ".venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000",
    url: "http://127.0.0.1:8000/health",
    reuseExistingServer: !process.env.CI,
    timeout: 30000
  }
});
