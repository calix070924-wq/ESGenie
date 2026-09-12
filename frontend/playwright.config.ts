import { defineConfig } from '@playwright/test';
import path from 'node:path';

export default defineConfig({
  testDir: './tests',
  fullyParallel: false,
  workers: 1,
  timeout: 40000,
  use: {
    baseURL: 'http://127.0.0.1:8771',
    channel: process.env.ESGENIE_LOCAL_CHROME ? 'chrome' : undefined,
    viewport: { width: 1280, height: 950 },
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  reporter: [['list'], ['html', { open: 'never' }]],
  webServer: {
    command: `${process.env.ESGENIE_PYTHON || 'python3'} -m esgenie.web --port 8771`,
    cwd: '..',
    url: 'http://127.0.0.1:8771/api/config',
    timeout: 30000,
    reuseExistingServer: false,
    env: {
      ESGENIE_FORCE_MOCK: '1',
      HF_HUB_OFFLINE: '1',
      TRANSFORMERS_OFFLINE: '1',
      ESGENIE_WEB_DATA_DIR: path.resolve('../outputs/browser-test-projects'),
    },
  },
});
