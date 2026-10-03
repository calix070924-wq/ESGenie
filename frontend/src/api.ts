export async function api<T>(path: string, method = 'GET', body?: unknown): Promise<T> {
  const form = body instanceof FormData;
  let response: Response;
  try {
    response = await fetch(`/api${path}`, {
      method,
      headers: {
        'X-ESGenie-Client': 'workspace',
        ...(!form && body !== undefined ? { 'Content-Type': 'application/json' } : {}),
      },
      body: body === undefined ? undefined : form ? body : JSON.stringify(body),
    });
  } catch {
    throw new Error('연결이 잠시 끊겼어요. 저장된 작업은 그대로 있으니 다시 시도해 주세요.');
  }
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    throw new Error(
      typeof data.detail === 'string'
        ? data.detail
        : '작업을 마치지 못했어요. 잠시 후 다시 시도해 주세요.',
    );
  }
  return response.json();
}

export async function download(projectId: string, kind: 'xlsx' | 'bundle') {
  const response = await fetch(`/api/projects/${projectId}/download/${kind}`);
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    throw new Error(data.detail || '파일을 준비하지 못했어요. 다시 시도해 주세요.');
  }
  const blob = await response.blob();
  const disposition = response.headers.get('Content-Disposition') || '';
  const name = decodeURIComponent(
    disposition.split("UTF-8''")[1] || `ESGenie.${kind === 'xlsx' ? 'xlsx' : 'zip'}`,
  );
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = name;
  document.body.append(link);
  link.click();
  link.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 60000);
}

export function rememberProject(id: string | null) {
  try {
    id ? localStorage.setItem('esgenie-project', id) : localStorage.removeItem('esgenie-project');
  } catch {
    /* Storage may be disabled. */
  }
}
export function lastProject() {
  try {
    return localStorage.getItem('esgenie-project');
  } catch {
    return null;
  }
}
