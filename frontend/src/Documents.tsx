import { useRef, useState } from 'react';
import { api } from './api';
import type { Config, Document, Project } from './types';
export function Documents({
  project,
  config,
  busy,
  onUpdate,
  act,
  onAnalyze,
  onReview,
}: {
  project: Project;
  config: Config;
  busy: boolean;
  onUpdate: (p: Project) => void;
  act: (work: () => Promise<void>) => Promise<void>;
  onAnalyze: () => void;
  onReview: () => void;
}) {
  const input = useRef<HTMLInputElement>(null);
  const [search, setSearch] = useState('');
  const [page, setPage] = useState(0);
  const [preview, setPreview] = useState<Document | null>(null);
  const upload = (files: FileList | null, id?: string) => {
    if (!files) return;
    const items = Array.from(files);
    void act(async () => {
      let latest: Project | undefined;
      for (const file of items) {
        const form = new FormData();
        form.append('file', file);
        latest = await api<Project>(
          `/projects/${project.id}/documents${id ? `/${id}/replace` : ''}`,
          'POST',
          form,
        );
        onUpdate(latest);
      }
    });
  };
  const filtered = project.documents.filter((d) =>
    d.name.toLocaleLowerCase().includes(search.toLocaleLowerCase()),
  );
  const pages = Math.max(1, Math.ceil(filtered.length / 10));
  const current = Math.min(page, pages - 1);
  const included = project.documents.filter((d) => d.included && !d.error).length;
  return (
    <>
      <div className="upload-strip">
        <div>
          <strong>가지고 있는 서류를 추가하세요</strong>
          <p>
            여러 PDF·사진을 함께 추가할 수 있습니다. 파일당 {config.max_file_mb}MB · 이전 원본 포함{' '}
            {config.max_project_mb}MB
          </p>
        </div>
        <button
          className="primary"
          disabled={busy || project.mode === 'example'}
          onClick={() => input.current?.click()}
        >
          파일 추가
        </button>
        <input
          className="sr-only"
          ref={input}
          aria-label="자료 파일 추가"
          type="file"
          multiple
          accept=".pdf,.png,.jpg,.jpeg"
          onChange={(e) => {
            upload(e.target.files);
            e.target.value = '';
          }}
        />
      </div>
      <div className="list-tools">
        <span>
          전체 {project.documents.length}개 · 분석 포함 {included}개
        </span>
        <input
          aria-label="자료 검색"
          placeholder="파일명으로 검색"
          value={search}
          onChange={(e) => {
            setSearch(e.target.value);
            setPage(0);
          }}
        />
      </div>
      <div className="document-table" role="table" aria-label="자료 목록">
        <div className="document-head" role="row">
          {['분석 포함', '파일명', '자료 구분', '처리 상태', '관리'].map((t) => (
            <span role="columnheader" key={t}>
              {t}
            </span>
          ))}
        </div>
        {filtered.slice(current * 10, current * 10 + 10).map((d) => (
          <div className={`document-row ${!d.included ? 'excluded' : ''}`} role="row" key={d.id}>
            <div role="cell">
              <input
                type="checkbox"
                aria-label={`${d.name} 분석 포함`}
                checked={d.included}
                disabled={busy || d.example}
                onChange={(e) =>
                  act(async () =>
                    onUpdate(
                      await api<Project>(`/projects/${project.id}/documents/${d.id}`, 'PATCH', {
                        included: e.target.checked,
                      }),
                    ),
                  )
                }
              />
            </div>
            <div role="cell">
              <button
                className="source-link"
                onClick={() => setPreview(preview?.id === d.id ? null : d)}
              >
                {d.name}
              </button>
              <small>
                버전 {d.version} · {d.pages ? `${d.pages}쪽` : '읽기 실패'} ·{' '}
                {(d.size / 1024).toFixed(0)}KB
              </small>
              {!!d.versions?.length && (
                <details>
                  <summary>이전 원본 {d.versions.length}개</summary>
                  {d.versions.map((v) => (
                    <a
                      className="source-link"
                      key={v.version}
                      href={`/api/projects/${project.id}/documents/${d.id}/original?version=${v.version}`}
                    >
                      {v.name} · 버전 {v.version}
                    </a>
                  ))}
                </details>
              )}
            </div>
            <div role="cell">
              <select
                aria-label={`${d.name} 자료 구분`}
                value={d.role}
                disabled={busy || d.example}
                onChange={(e) =>
                  act(async () =>
                    onUpdate(
                      await api<Project>(`/projects/${project.id}/documents/${d.id}`, 'PATCH', {
                        role: e.target.value,
                      }),
                    ),
                  )
                }
              >
                <option value="evidence">증빙 자료</option>
                <option value="company_answer">회사 작성 답변</option>
              </select>
            </div>
            <div role="cell">
              <span
                className={
                  d.error || d.status.includes('실패')
                    ? 'error-text'
                    : d.status.includes('필요')
                      ? 'warning-text'
                      : ''
                }
              >
                {d.status}
              </span>
              {(d.error || project.result?.document_errors?.[d.name]) && (
                <small className="error-text">
                  {d.error || project.result?.document_errors?.[d.name]}
                </small>
              )}
            </div>
            <div role="cell">
              <label className={`replace-button secondary ${busy ? 'disabled' : ''}`}>
                파일 교체
                <input
                  className="sr-only"
                  type="file"
                  aria-label={`${d.name} 파일 교체`}
                  disabled={busy || d.example}
                  accept=".pdf,.png,.jpg,.jpeg"
                  onChange={(e) => {
                    upload(e.target.files, d.id);
                    e.target.value = '';
                  }}
                />
              </label>
            </div>
          </div>
        ))}
      </div>
      {!filtered.length && <p className="empty-list">등록된 자료가 없습니다.</p>}
      <div className="pagination">
        <span>
          {current + 1} / {pages}
        </span>
        <button className="secondary" disabled={!current} onClick={() => setPage(current - 1)}>
          이전
        </button>
        <button
          className="secondary"
          disabled={current + 1 >= pages}
          onClick={() => setPage(current + 1)}
        >
          다음
        </button>
      </div>
      {preview && (
        <div className="document-preview">
          <div className="section-heading">
            <h3>{preview.name}</h3>
            <button className="secondary" onClick={() => setPreview(null)}>
              닫기
            </button>
          </div>
          {!preview.example && !preview.error ? (
            <img
              alt={`${preview.name} 1쪽 원문`}
              src={`/api/projects/${project.id}/documents/${preview.id}/pages/0?version=${preview.version}`}
            />
          ) : (
            <p>{preview.error || '사용법 예시 · 실제 원본이 없습니다.'}</p>
          )}
        </div>
      )}
      {!!project.affected_questions?.length && (
        <p className="inline-warning">
          자료 변경의 영향을 받는 문항 {project.affected_questions.length}개:{' '}
          {project.affected_questions.join(' · ')}
        </p>
      )}
      <div className="analysis-footer">
        <p>
          {project.job.status === 'complete' && !project.stale
            ? '분석을 완료했습니다. 답변 검토로 이어가세요.'
            : project.stale
              ? '자료 변경 후 재분석이 필요합니다. 기존 답변과 원본은 보관됩니다.'
              : '분석에 포함할 자료를 확인하세요.'}
        </p>
        <div>
          {project.result && (
            <button className="secondary" onClick={onReview}>
              답변 검토로 이동
            </button>
          )}
          <button
            className="primary"
            onClick={onAnalyze}
            disabled={busy || !included || !config.analysis_available || project.mode === 'example'}
          >
            {project.stale ? '재분석' : '분석 시작'}
          </button>
        </div>
      </div>
      {!config.analysis_available && (
        <p className="inline-warning">
          분석 연결이 아직 준비되지 않았어요. 연결 설정 후 실제 자료를 분석할 수 있습니다.
        </p>
      )}
      <p className="table-footnote">
        분석 제외·자료 구분 변경·교체는 관련 문항을 재검토 대상으로 표시합니다. 회사 작성 답변은
        독립 증빙과 구분합니다.
      </p>
    </>
  );
}
