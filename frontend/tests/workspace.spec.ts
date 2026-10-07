import { expect, test } from '@playwright/test';
import path from 'node:path';

const fixture = path.resolve('tests/fixtures/electricity.pdf');
const nav = (page: any, name: string) =>
  page
    .getByRole('navigation', { name: '실사 응답 준비 단계' })
    .getByRole('button', { name: new RegExp(name) });

test('saving clears unsaved warnings after API field reordering', async ({ page }) => {
  await page.goto('/');
  await page.getByRole('button', { name: '예시로 먼저 둘러보기' }).click();
  await page.getByRole('button', { name: '사용한 전력량을 확인해 주세요. 살펴보기' }).click();
  await page.getByLabel('답변', { exact: true }).fill('140000');
  await page.getByRole('button', { name: '저장', exact: true }).click();
  await expect(page.getByText('답변을 저장했습니다. 검토 완료는 별도 동작입니다.')).toBeVisible();
  await expect(page.getByText('저장하지 않은 수정이 있습니다.', { exact: true })).toBeHidden();
  await expect(page.getByRole('button', { name: '저장', exact: true })).toBeDisabled();
  await page.getByRole('button', { name: '문항 목록', exact: true }).click();
  await expect(page.getByRole('dialog')).toBeHidden();
  await expect(page.getByRole('table', { name: '실사 응답 목록' })).toContainText('140000 kWh');
  await page.getByRole('button', { name: '사용한 전력량을 확인해 주세요. 살펴보기' }).click();
  await page.getByLabel('답변', { exact: true }).fill('');
  await page.getByLabel('검토 메모').fill('전력 집계 자료 부족');
  await page.getByRole('button', { name: '저장', exact: true }).click();
  await expect(page.getByRole('button', { name: '저장', exact: true })).toBeDisabled();
  await page.getByRole('button', { name: '문항 목록', exact: true }).click();
  const row = page.getByRole('row').filter({ hasText: '사용한 전력량을 확인해 주세요.' });
  await expect(row).toContainText('답변 없음');
  await expect(row).toContainText('자료 필요');
  await page.getByRole('button', { name: '사용한 전력량을 확인해 주세요. 살펴보기' }).click();
  await page.getByLabel('답변', { exact: true }).fill('150000');
  await page.getByRole('button', { name: '문항 목록', exact: true }).click();
  await expect(page.getByRole('dialog')).toBeVisible();
});

test('save protection, saved values, review completion and real Excel/PDF downloads', async ({
  page,
}, info) => {
  const errors: string[] = [];
  page.on('pageerror', (e) => errors.push(e.message));
  await page.goto('/');
  await page.getByRole('button', { name: '예시로 먼저 둘러보기' }).click();
  await expect(page.getByRole('table', { name: '실사 응답 목록' })).toBeVisible();
  await page.screenshot({ path: info.outputPath('review-list.png'), fullPage: true });
  await page.getByLabel('문항 검색').fill('폐기물');
  await page
    .getByRole('button', { name: '폐기물 중 재활용하는 비율은 얼마인가요? 살펴보기' })
    .click();
  await page.getByLabel('답변', { exact: true }).fill('30.25');
  await page
    .getByLabel('측정 범위', { exact: true })
    .fill('2026년 4월 · 김해 제1공장 · 위탁 폐기물');
  await page.getByLabel('검토 메모').fill('연간 자료 추가 확인 필요');
  await page.getByLabel('수정 이유').fill('원문 값과 적용 범위 확인');
  await nav(page, '응답서').click();
  await expect(page.getByRole('dialog')).toBeVisible();
  await page.getByRole('button', { name: '계속 작성' }).click();
  await expect(page.getByLabel('답변', { exact: true })).toHaveValue('30.25');
  await page.getByRole('button', { name: '문항 목록', exact: true }).click();
  await page.getByRole('button', { name: '저장하고 이동' }).click();
  await expect(page.getByLabel('문항 검색')).toHaveValue('폐기물');
  await expect(page.getByRole('table', { name: '실사 응답 목록' })).toContainText('30.25 %');
  await expect(nav(page, '답변 검토')).toContainText('0 / 3 검토 완료');
  await page
    .getByRole('button', { name: '폐기물 중 재활용하는 비율은 얼마인가요? 살펴보기' })
    .click();
  await page.screenshot({ path: info.outputPath('review-detail.png'), fullPage: true });
  await page.getByLabel('답변', { exact: true }).fill('99');
  await nav(page, '응답서').click();
  await page.getByRole('button', { name: '수정 버리고 이동' }).click();
  await expect(page.getByRole('table', { name: '응답서 미리보기' })).toContainText('30.25 %');
  await expect(page.getByRole('table', { name: '응답서 미리보기' })).toContainText(
    '연간 자료 추가 확인 필요',
  );
  await page.screenshot({ path: info.outputPath('response-sheet.png'), fullPage: true });
  for (const [kind, label] of [
    ['xlsx', 'Excel 내려받기'],
    ['pdf', 'PDF 내려받기'],
  ]) {
    const waiting = page.waitForEvent('download');
    await page.getByRole('button', { name: label }).click();
    const download = await waiting;
    expect(download.suggestedFilename()).toMatch(new RegExp(`\\.${kind}$`));
    await download.saveAs(info.outputPath(`response.${kind}`));
  }
  await page
    .getByRole('button', { name: '폐기물 중 재활용하는 비율은 얼마인가요?', exact: true })
    .click();
  await page.getByRole('button', { name: '검토 완료 후 다음' }).click();
  await expect(nav(page, '답변 검토')).toContainText('1 / 3 검토 완료');
  await page.reload();
  await expect(page.getByRole('table', { name: '실사 응답 목록' })).toContainText('30.25 %');
  expect(errors).toEqual([]);
});

test('upload → analysis → save → replace → reanalysis preserves old originals and other completion', async ({
  page,
}, info) => {
  await page.goto('/');
  await page.getByRole('button', { name: '우리 회사로 시작하기' }).click();
  await page.getByLabel('회사명', { exact: true }).fill('실제 PDF 흐름 검증 회사');
  await page.getByRole('button', { name: '서류 올리러 가기' }).click();
  await page.getByLabel('자료 파일 추가').setInputFiles([
    {
      name: 'electricity.pdf',
      mimeType: 'application/pdf',
      buffer: await (await import('node:fs/promises')).readFile(fixture),
    },
    {
      name: 'other.pdf',
      mimeType: 'application/pdf',
      buffer: await (await import('node:fs/promises')).readFile(fixture),
    },
  ]);
  await expect(page.getByRole('table', { name: '자료 목록' })).toContainText('other.pdf');
  await page.screenshot({ path: info.outputPath('documents.png'), fullPage: true });
  await page.getByRole('button', { name: '분석 시작', exact: true }).click();
  await expect(page.getByRole('table', { name: '실사 응답 목록' })).toBeVisible();
  await page.getByRole('button', { name: 'electricity.pdf 살펴보기', exact: true }).click();
  await expect(page.getByRole('img', { name: 'electricity.pdf 1쪽 원문' })).toBeVisible();
  await page.getByLabel('답변', { exact: true }).fill('140000');
  await page.getByLabel('수정 이유').fill('담당자 집계로 수정');
  await page.getByRole('button', { name: '검토 완료 후 다음' }).click();
  await page.getByRole('button', { name: '검토 완료 후 다음' }).click();
  await expect(nav(page, '답변 검토')).toContainText('2 / 2 검토 완료');
  await nav(page, '자료 준비').click();
  await page.getByLabel('electricity.pdf 파일 교체').setInputFiles(fixture);
  await expect(page.getByRole('status').filter({ hasText: '서류나 회사 정보' })).toBeVisible();
  await nav(page, '응답서').click();
  await expect(page.getByRole('button', { name: 'Excel 내려받기' })).toBeDisabled();
  await expect(nav(page, '답변 검토')).toContainText('1 / 2 검토 완료');
  await nav(page, '자료 준비').click();
  await page.getByRole('button', { name: '재분석', exact: true }).click();
  await expect(page.getByRole('table', { name: '실사 응답 목록' })).toBeVisible();
  await expect(page.getByRole('table', { name: '실사 응답 목록' })).toContainText('140000 kWh');
  await page.getByRole('button', { name: 'electricity.pdf 살펴보기', exact: true }).click();
  await expect(page.getByText('이전 원본', { exact: false })).toBeVisible();
  await expect(page.getByRole('img', { name: 'electricity.pdf 1쪽 원문' })).toBeVisible();
  await page.getByLabel('새 근거 선택').selectOption('automatic');
  await expect(page.getByLabel('답변', { exact: true })).toHaveValue('140000');
  await page.getByRole('button', { name: '이 값과 근거 적용' }).click();
  await expect(page.getByLabel('답변', { exact: true })).toHaveValue('142,560');
  await page.getByRole('button', { name: '검토 완료 후 다음' }).click();
  await expect(nav(page, '답변 검토')).toContainText('2 / 2 검토 완료');
  await nav(page, '자료 준비').click();
  await page.getByLabel('자료 파일 추가').setInputFiles({
    name: 'broken.pdf',
    mimeType: 'application/pdf',
    buffer: Buffer.from('broken'),
  });
  await expect(page.getByRole('table', { name: '자료 목록' })).toContainText(
    '파일을 읽을 수 없어요.',
  );
  await expect(page.getByLabel('broken.pdf 파일 교체')).toBeEnabled();
});

test('many long file names, filters, pages and narrow windows remain readable', async ({
  page,
  request,
}, info) => {
  const headers = { 'X-ESGenie-Client': 'workspace' };
  const companyName = `많은 문항 검증 회사 ${Date.now()}`;
  const p = await (
    await request.post('/api/projects', {
      headers,
      data: { company_name: companyName, year: 2026 },
    })
  ).json();
  const data = await (await import('node:fs/promises')).readFile(fixture);
  for (let i = 1; i <= 23; i++) {
    await request.post(`/api/projects/${p.id}/documents`, {
      headers,
      multipart: {
        file: {
          name: `매우_긴_파일명_사업장별_전력사용량_월별집계_증빙문서_${i}.pdf`,
          mimeType: 'application/pdf',
          buffer: data,
        },
      },
    });
  }
  await request.post(`/api/projects/${p.id}/analysis`, { headers });
  await page.goto('/');
  await page.getByRole('button', { name: new RegExp(companyName) }).click();
  await expect(page.getByRole('table', { name: '실사 응답 목록' })).toBeVisible();
  await page.getByRole('button', { name: '다음', exact: true }).click();
  await page
    .getByRole('button', { name: /살펴보기/ })
    .first()
    .click();
  await page.getByRole('button', { name: '문항 목록', exact: true }).click();
  await expect(page.getByText('11–20 / 23문항')).toBeVisible();
  for (const width of [1440, 1024, 768, 375, 320]) {
    await page.setViewportSize({ width, height: 950 });
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(
      true,
    );
    await page.screenshot({ path: info.outputPath(`list-${width}.png`), fullPage: true });
  }
  await page
    .getByRole('button', { name: /살펴보기/ })
    .first()
    .click();
  for (const width of [1440, 1024, 768, 375, 320]) {
    await page.setViewportSize({ width, height: 950 });
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(
      true,
    );
    await page.screenshot({ path: info.outputPath(`detail-${width}.png`), fullPage: true });
  }
});
