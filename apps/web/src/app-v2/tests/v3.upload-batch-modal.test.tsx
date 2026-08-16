// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { BatchUploadModal } from '../components/documents/BatchUploadModal'
import type { V2Services } from '../types'

describe('BatchUploadModal knowledge-base target', () => {
  afterEach(() => cleanup())

  it('reports byte-level progress instead of waiting for a whole file to finish', async () => {
    let completeUpload: (() => void) | undefined
    const uploadBulk = vi.fn(async (_kbId, items, options) => {
      options.onProgress({
        total: 1,
        completed: 0,
        failed: 0,
        skipped: 0,
        perFile: new Map([[
          items[0].id,
          {
            id: items[0].id,
            path: items[0].path,
            size: items[0].size,
            status: 'uploading',
            uploadPhase: 'transferring',
            uploadedBytes: 5,
          },
        ]]),
      })
      await new Promise<void>((resolve) => { completeUpload = resolve })
      return { summary: { total: 1, successCount: 1, failedCount: 0, skippedCount: 0, successItems: [], failedItems: [], skippedItems: [] } }
    })
    const services = {
      knowledge: { list: vi.fn().mockResolvedValue({ state: 'ready', data: [] }) },
      documents: { uploadBulk },
    } as unknown as V2Services

    render(<BatchUploadModal open kbId="kb-existing" services={services} onClose={vi.fn()} />)
    fireEvent.change(await screen.findByLabelText('选择目录'), {
      target: { files: [new File(['0123456789'], 'progress.md', { type: 'text/markdown' })] },
    })
    fireEvent.click(await screen.findByRole('button', { name: '开始上传（1）' }))

    await waitFor(() => expect(screen.getByRole('progressbar').getAttribute('aria-valuenow')).toBe('50'))
    expect(screen.getByText('正在上传 50%')).toBeTruthy()
    completeUpload?.()
  })

  it('treats an empty knowledge-base list as a create-target state', async () => {
    const create = vi.fn().mockResolvedValue({
      state: 'ready',
      data: {
        id: 'kb-jinbo',
        name: '金博知识库',
        description: '',
        visibility: 'PRIVATE',
        documentCount: 0,
        updatedAt: '2026-08-14T00:00:00Z',
      },
    })
    const uploadBulk = vi.fn().mockResolvedValue({
      summary: {
        total: 1,
        successCount: 1,
        failedCount: 0,
        skippedCount: 0,
        successItems: [],
        failedItems: [],
        skippedItems: [],
      },
    })
    const services = {
      knowledge: {
        list: vi.fn().mockResolvedValue({ state: 'empty', data: [] }),
        create,
      },
      documents: { uploadBulk },
    } as unknown as V2Services

    render(
      <BatchUploadModal
        open
        kbId={null}
        services={services}
        onClose={vi.fn()}
      />,
    )

    const nameInput = await screen.findByLabelText('新知识库名称：')
    expect(screen.queryByText('知识库列表加载失败')).toBeNull()

    fireEvent.change(nameInput, { target: { value: '金博知识库' } })
    fireEvent.click(screen.getByRole('button', { name: '创建并选择' }))

    await waitFor(() => {
      expect(create).toHaveBeenCalledWith({ name: '金博知识库', visibility: 'PRIVATE' })
    })
    expect(screen.queryByLabelText('新知识库名称：')).toBeNull()

    fireEvent.change(screen.getByLabelText('选择目录'), {
      target: { files: [new File(['readme'], 'readme.md', { type: 'text/markdown' })] },
    })
    fireEvent.click(await screen.findByRole('button', { name: '开始上传（1）' }))

    await waitFor(() => {
      expect(uploadBulk).toHaveBeenCalledWith(
        'kb-jinbo',
        expect.arrayContaining([expect.objectContaining({ path: 'readme.md' })]),
        expect.objectContaining({ mode: 'DIRECTORY' }),
      )
    })
  })

  it('keeps a long relative path out of the bounded client item ID', async () => {
    const uploadBulk = vi.fn().mockResolvedValue({
      summary: { total: 1, successCount: 1, failedCount: 0, skippedCount: 0, successItems: [], failedItems: [], skippedItems: [] },
    })
    const services = {
      knowledge: { list: vi.fn().mockResolvedValue({ state: 'ready', data: [] }) },
      documents: { uploadBulk },
    } as unknown as V2Services
    const file = new File(['readme'], 'README.md', { type: 'text/markdown' })
    const path = `${'a/'.repeat(60)}README.md`
    Object.defineProperty(file, 'webkitRelativePath', { value: path })

    render(<BatchUploadModal open kbId="kb-existing" services={services} onClose={vi.fn()} />)
    fireEvent.change(await screen.findByLabelText('选择目录'), { target: { files: [file] } })
    fireEvent.click(await screen.findByRole('button', { name: '开始上传（1）' }))

    await waitFor(() => expect(uploadBulk).toHaveBeenCalledTimes(1))
    const uploaded = uploadBulk.mock.calls[0]?.[1][0]
    expect(uploaded.path).toBe(path)
    expect(uploaded.id.length).toBeLessThanOrEqual(128)
  })

  it('keeps whole-request failures retryable and recomputes retry progress', async () => {
    let attempt = 0
    const uploadBulk = vi.fn().mockImplementation(async (_kbId, items, options) => {
      attempt += 1
      if (attempt === 1) {
        const [first, second] = items
        options.onProgress({
          total: 2,
          completed: 1,
          failed: 1,
          skipped: 0,
          perFile: new Map([
            [first.id, { id: first.id, path: first.path, size: first.size, status: 'success', batchId: 'batch-original', uploadItemId: 'item-first' }],
            [second.id, { id: second.id, path: second.path, size: second.size, status: 'failed', batchId: 'batch-original', uploadItemId: 'item-second' }],
          ]),
        })
        return { summary: { total: 2, successCount: 1, failedCount: 1, skippedCount: 0, successItems: [], failedItems: [], skippedItems: [] } }
      }
      const [retry] = items
      options.onProgress({
        total: 1,
        completed: 0,
        failed: 0,
        skipped: 0,
        perFile: new Map([[retry.id, { id: retry.id, path: retry.path, size: retry.size, status: 'queued' }]]),
      })
      options.onProgress({
        total: 1,
        completed: 1,
        failed: 0,
        skipped: 0,
        perFile: new Map([[retry.id, { id: retry.id, path: retry.path, size: retry.size, status: 'success' }]]),
      })
      return { summary: { total: 1, successCount: 1, failedCount: 0, skippedCount: 0, successItems: [], failedItems: [], skippedItems: [] } }
    })
    const services = {
      knowledge: { list: vi.fn().mockResolvedValue({ state: 'ready', data: [] }) },
      documents: { uploadBulk },
    } as unknown as V2Services

    render(<BatchUploadModal open kbId="kb-existing" services={services} onClose={vi.fn()} />)
    fireEvent.change(await screen.findByLabelText('选择目录'), {
      target: {
        files: [
          new File(['first'], 'first.md', { type: 'text/markdown' }),
          new File(['second'], 'second.md', { type: 'text/markdown' }),
        ],
      },
    })
    fireEvent.click(await screen.findByRole('button', { name: '开始上传（2）' }))
    fireEvent.click(await screen.findByRole('button', { name: '重传失败项（1）' }))

    await waitFor(() => expect(uploadBulk).toHaveBeenCalledTimes(2))
    const retryOptions = uploadBulk.mock.calls[1]?.[2]
    expect(retryOptions.resumeItems.get(uploadBulk.mock.calls[1]?.[1][0].id)).toEqual({
      batchId: 'batch-original',
      uploadItemId: 'item-second',
    })
    expect(screen.queryByRole('button', { name: /重传失败项/ })).toBeNull()
    expect(screen.getByRole('progressbar').getAttribute('aria-valuenow')).toBe('100')
  })

  it('offers retry after an upload-batch request fails before progress exists', async () => {
    const services = {
      knowledge: { list: vi.fn().mockResolvedValue({ state: 'ready', data: [] }) },
      documents: { uploadBulk: vi.fn().mockRejectedValue(new Error('object storage unavailable')) },
    } as unknown as V2Services

    render(<BatchUploadModal open kbId="kb-existing" services={services} onClose={vi.fn()} />)
    fireEvent.change(await screen.findByLabelText('选择目录'), {
      target: { files: [new File(['failed'], 'failed.md', { type: 'text/markdown' })] },
    })
    fireEvent.click(await screen.findByRole('button', { name: '开始上传（1）' }))

    expect(await screen.findByRole('button', { name: '重传失败项（1）' })).not.toBeNull()
  })
})
