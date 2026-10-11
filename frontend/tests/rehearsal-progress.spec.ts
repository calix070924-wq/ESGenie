import { expect, test } from '@playwright/test';
import { readFile } from 'node:fs/promises';

const progress = (page: any) => page.getByRole('status', { name: '분석 진행 상황' });

async function setup(page: any, name: string, companyAnswer = false) {
  await page.goto('/');
  await page.getByRole('button', { name: '우리 회사로 시작하기' }).click();
  await page.getByLabel('회사명', { exact: true }).fill(name);
  await page.getByRole('button', { name: '서류 올리러 가기' }).click();
  const buffer = await readFile(new URL('./fixtures/electricity.pdf', import.meta.url));
  await page.getByLabel('자료 파일 추가').setInputFiles([
    { name: 'evidence.pdf', mimeType: 'application/pdf', buffer },
    { name: 'company-answer.pdf', mimeType: 'application/pdf', buffer },
  ]);
  await expect(page.getByRole('table', { name: '자료 목록' })).toContainText('company-answer.pdf');
  if (companyAnswer) {
    await page.getByLabel('company-answer.pdf 자료 구분').selectOption('company_answer');
  }
}

test('real worker events show file counts, injected retries, refresh and terminal completion', async ({
  page,
}, info) => {
  const errors: string[] = [];
  page.on('pageerror', (error) => errors.push(error.message));
  await setup(page, '진행 표시 검증 회사', true);
  await page.getByRole('button', { name: '분석 시작', exact: true }).click();
  await expect(progress(page)).toContainText('처리 중: evidence.pdf');
  await expect(progress(page)).toContainText('파일 처리 완료 0 / 2개');
  await expect(progress(page)).toContainText('AI 재시도 중');
  await expect(progress(page)).toContainText('시도 2 / 3');
  await page.screenshot({ path: info.outputPath('retry.png'), fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(
    true,
  );
  await page.screenshot({ path: info.outputPath('retry-narrow.png'), fullPage: true });
  await page.setViewportSize({ width: 1280, height: 950 });
  await page.reload();
  await expect(progress(page)).toContainText('전체 경과');
  await expect(progress(page)).toContainText('처리 중: company-answer.pdf');
  await expect(progress(page)).toContainText('파일 처리 완료 1 / 2개');
  await expect(progress(page)).toContainText('문항별 답변과 근거');
  await expect(progress(page)).toContainText('파일 처리 완료 2 / 2개');
  await expect(progress(page).locator('.spin')).toHaveCount(0);
  await expect(progress(page)).not.toContainText('AI 응답 대기 중');
  await expect(page.getByRole('table', { name: '실사 응답 목록' })).toBeVisible();
  await page.screenshot({ path: info.outputPath('complete.png'), fullPage: true });
  expect(errors).toEqual([]);
});

test('final injected failure stops spinner, keeps previous results and offers retry', async ({
  page,
}, info) => {
  await setup(page, '재분석 실패 검증 회사');
  await page.getByRole('button', { name: '분석 시작', exact: true }).click();
  await expect(page.getByRole('table', { name: '실사 응답 목록' })).toBeVisible({ timeout: 30000 });
  const tableBefore = await page.getByRole('table', { name: '실사 응답 목록' }).innerText();
  await page
    .getByRole('navigation', { name: '실사 응답 준비 단계' })
    .getByRole('button', { name: /자료 준비/ })
    .click();
  await page.getByRole('button', { name: '분석 시작', exact: true }).click();
  await expect(progress(page)).toContainText('파일 처리 완료 0 / 2개');
  await expect(page.getByRole('alert').filter({ hasText: '이전 결과는 보관' })).toBeVisible({
    timeout: 20000,
  });
  await expect(page.getByRole('button', { name: '다시 시도', exact: true })).toBeEnabled();
  await expect(progress(page).locator('.spin')).toHaveCount(0);
  await page.screenshot({ path: info.outputPath('failure.png'), fullPage: true });
  await page.reload();
  await expect
    .poll(() => page.getByRole('table', { name: '실사 응답 목록' }).innerText())
    .toBe(tableBefore);
  await expect(progress(page).locator('.spin')).toHaveCount(0);
  await page.getByRole('button', { name: '다시 시도', exact: true }).click();
  await expect(progress(page)).toContainText('파일 처리 완료 0 / 2개');
  await expect(page.getByRole('alert').filter({ hasText: '이전 결과는 보관' })).toBeVisible({
    timeout: 20000,
  });
});

test('polling disconnect removes indefinite spinner and reconnects to server status', async ({
  page,
}, info) => {
  await setup(page, '연결 복구 검증 회사');
  await page.getByRole('button', { name: '분석 시작', exact: true }).click();
  await expect(progress(page)).toContainText('처리 중');
  await page.route('**/api/projects/*', async (route) => {
    if (route.request().method() === 'GET') await route.abort();
    else await route.continue();
  });
  await expect(progress(page)).toContainText('진행 상태 연결이 끊겼어요');
  await expect(progress(page).locator('.spin')).toHaveCount(0);
  await page.screenshot({ path: info.outputPath('disconnect.png'), fullPage: true });
  await page.unroute('**/api/projects/*');
  await expect(progress(page)).not.toContainText('진행 상태 연결이 끊겼어요');
  await expect(page.getByRole('table', { name: '실사 응답 목록' })).toBeVisible({ timeout: 30000 });
});
