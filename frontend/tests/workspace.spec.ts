import { expect, test } from '@playwright/test';
import path from 'node:path';

test('beginner can explore, compare evidence, save, resume and download', async ({
  page,
}, info) => {
  const errors: string[] = [];
  page.on('pageerror', (error) => errors.push(error.message));
  await page.goto('/');
  await expect(
    page.getByRole('heading', { name: '가지고 있는 서류로, 답변 준비를 시작하세요.' }),
  ).toBeVisible();
  await page.screenshot({ path: info.outputPath('01-welcome.png'), fullPage: true });
  await page.getByRole('button', { name: '예시로 먼저 둘러보기' }).click();
  await expect(page.getByRole('heading', { name: '지금 확인할 내용' })).toBeVisible();
  await page.screenshot({ path: info.outputPath('02-guided-project.png'), fullPage: true });
  await page
    .getByRole('button', { name: '폐기물 중 재활용하는 비율은 얼마인가요? 살펴보기' })
    .click();
  await expect(page.getByText('회사가 적은 답변과 자료에서 계산한 값이 달라요.')).toBeVisible();
  await expect(page.getByText('2026년 · 추정', { exact: false }).first()).toBeVisible();
  await page.getByText('회사가 직접 적은 답변 보기', { exact: true }).click();
  await expect(page.getByText('재활용률 92%', { exact: true })).toBeVisible();
  await page.getByLabel('확인한 내용 메모').fill('회계팀에 원본 처리 내역 요청하기');
  await page.getByRole('button', { name: '작성 내용 저장' }).click();
  await expect(page.getByText('저장됨 · 확인 상태는 유지돼요.')).toBeVisible();
  await expect(page.getByLabel('답변 검토').getByText('확인 필요', { exact: true })).toBeVisible();
  await page.screenshot({ path: info.outputPath('03-answer-evidence.png'), fullPage: true });
  await page.reload();
  await page
    .getByRole('button', { name: '폐기물 중 재활용하는 비율은 얼마인가요? 살펴보기' })
    .click();
  await expect(page.getByLabel('확인한 내용 메모')).toHaveValue('회계팀에 원본 처리 내역 요청하기');
  await page.getByRole('button', { name: '확인할 내용', exact: true }).click();
  await page
    .getByRole('button', { name: '윤리 규정과 운영 내용을 설명해 주세요. 살펴보기' })
    .click();
  await expect(page.getByText('아직 확인하지 못했어요', { exact: true })).toBeVisible();
  await page.getByLabel('직접 작성한 답변').fill('분기별로 직원 윤리 교육을 진행합니다.');
  await page.getByRole('button', { name: '작성 내용 저장' }).click();
  await expect(page.getByText('저장됨 · 확인 상태는 유지돼요.')).toBeVisible();
  await page.getByRole('button', { name: /응답서 받기/ }).click();
  await expect(
    page.getByText('분기별로 직원 윤리 교육을 진행합니다.', { exact: true }),
  ).toBeVisible();
  const download = page.waitForEvent('download');
  await page.getByRole('button', { name: '응답서 Excel 받기' }).click();
  expect((await download).suggestedFilename()).toMatch(/사용법 예시.*\.xlsx$/);
  expect(errors).toEqual([]);
});

test('new company uploads familiar files and receives actionable errors', async ({ page }) => {
  await page.goto('/');
  await page.getByRole('button', { name: '우리 회사로 시작하기' }).click();
  await page.getByLabel('회사명', { exact: true }).fill('처음 사용하는 회사');
  await page.getByRole('button', { name: '서류 올리러 가기' }).click();
  await expect(page.getByRole('heading', { name: '지금 가지고 있는 서류부터' })).toBeVisible();
  await page
    .locator('input[type=file]')
    .setInputFiles(path.resolve('tests/fixtures/electricity.pdf'));
  await expect(page.getByText('electricity.pdf', { exact: true })).toBeVisible();
  await expect(page.getByLabel('electricity.pdf 자료 종류')).toHaveValue('evidence');
  await page.getByLabel('electricity.pdf 자료 종류').selectOption('company_answer');
  await page.reload();
  await expect(page.getByLabel('electricity.pdf 자료 종류')).toHaveValue('company_answer');
  await page.locator('input[type=file]').setInputFiles({
    name: 'broken.pdf',
    mimeType: 'application/pdf',
    buffer: Buffer.from('not a document'),
  });
  await expect(page.getByRole('alert')).toContainText('파일을 읽을 수 없어요.');
  await expect(page.getByRole('button', { name: '서류를 읽고 답변 준비하기' })).toBeDisabled();
  await expect(
    page.getByText('분석 연결이 아직 준비되지 않았어요.', { exact: false }),
  ).toBeVisible();
  await page.getByRole('button', { name: 'electricity.pdf 목록에서 빼기' }).click();
  await expect(page.getByText('electricity.pdf', { exact: true })).toHaveCount(0);
});

test('mobile and keyboard users can reach help and review without sideways scrolling', async ({
  page,
}, info) => {
  await page.setViewportSize({ width: 375, height: 850 });
  await page.goto('/');
  await page.getByText('쉬운 설명', { exact: true }).click();
  await expect(page.getByText('실사 응답서가 뭔가요?')).toBeVisible();
  await page.getByText('쉬운 설명', { exact: true }).click();
  await page.getByRole('button', { name: '예시로 먼저 둘러보기' }).click();
  await expect(page.getByRole('heading', { name: '지금 확인할 내용' })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(
    true,
  );
  await page.screenshot({ path: info.outputPath('04-mobile-project.png'), fullPage: true });
  await page
    .getByRole('button', { name: '폐기물 중 재활용하는 비율은 얼마인가요? 살펴보기' })
    .click();
  await page.setViewportSize({ width: 320, height: 800 });
  await expect(page.getByLabel('답변과 연결된 자료')).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(
    true,
  );
  await page.screenshot({ path: info.outputPath('05-small-review.png'), fullPage: true });
  await page.keyboard.press('Control+Home');
  await page.getByRole('button', { name: '확인할 내용', exact: true }).focus();
  await page.keyboard.press('Enter');
  await expect(page.getByRole('heading', { name: '지금 확인할 내용' })).toBeVisible();
});
