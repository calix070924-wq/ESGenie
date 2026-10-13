import { defineConfig } from '@playwright/test';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const projectRoot = fileURLToPath(new URL('..', import.meta.url));

export default defineConfig({
  testDir: './tests',
  testMatch: 'rehearsal-progress.spec.ts',
  workers: 1,
  timeout: 60000,
  expect: { timeout: 15000 },
  use: {
    baseURL: 'http://127.0.0.1:8784',
    channel: 'chrome',
    viewport: { width: 1280, height: 950 },
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  outputDir: '../outputs/rehearsal_fixes_20261008/progress/chrome/test-results',
  reporter: [['list']],
  webServer: {
    command: `${process.env.ESGENIE_PYTHON || 'python3'} -m scripts.serve_rehearsal_progress_validation`,
    cwd: '..',
    url: 'http://127.0.0.1:8784/api/config',
    reuseExistingServer: false,
    env: {
      ESGENIE_FORCE_MOCK: '1',
      HF_HUB_OFFLINE: '1',
      TRANSFORMERS_OFFLINE: '1',
      ESGENIE_WEB_DATA_DIR: path.resolve(
        projectRoot,
        'outputs/rehearsal_fixes_20261008/progress/chrome/projects',
      ),
      ESGENIE_PROGRESS_TEST_PORT: '8784',
    },
  },
});
