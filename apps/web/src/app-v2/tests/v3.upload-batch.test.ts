import { describe, expect, it, vi } from 'vitest'
import {
  MAX_CLIENT_BATCH_BYTES,
  MAX_CLIENT_BATCH_FILES,
  uploadBulkImpl,
} from '../adapters/documents'
import { SHA256_CHUNK_BYTES, sha256BlobIncremental, sha256File, sha256Hex } from '../crypto/sha256'
import type { BulkUploadProgress } from '../types'

function acceptedItem(id: string, path: string) {
  return {
    client_item_id: id,
    accepted: true,
    normalized_relative_path: path,
    display_path: path,
    byte_size: 4,
    detected_mime: 'text/plain',
    error_code: null,
    error_detail: null,
    upload_item_id: `item-${id}`,
    upload_session: null,
  }
}

function openedSession(id: string) {
  return {
    session_id: `session-${id}`,
    method: 'SINGLE',
    provider_upload_id: null,
    upload_urls: [`/api/v1/kb/uploads/objects?object_key=uploads%2Ftenant%2Fkb%2F${id}`],
    part_size: null,
    expires_at: '2026-08-14T01:00:00Z',
  }
}

function createAcceptedBatch(batchId: string, items: readonly { id: string; path: string; size: number }[]) {
  return {
    id: batchId,
    batch_id: batchId,
    status: 'ACCEPTED',
    created: true,
    items: items.map((item) => ({
      ...acceptedItem(item.id, item.path),
      byte_size: item.size,
    })),
  }
}

describe('directory batch upload adapter', () => {
  it('slices a 100 MB HTTP fallback hash into bounded chunks', async () => {
    const requestedLengths: number[] = []
    const largeBlob = {
      size: 100 * 1024 * 1024,
      slice: (start: number, end: number) => {
        requestedLengths.push(end - start)
        return { arrayBuffer: async () => new Uint8Array(0).buffer }
      },
    } as unknown as Blob

    await sha256BlobIncremental(largeBlob)

    expect(requestedLengths).toHaveLength(100)
    expect(Math.max(...requestedLengths)).toBe(SHA256_CHUNK_BYTES)
  })

  it('keeps file hashing incremental even when Web Crypto is available', async () => {
    const digest = vi.fn()
    vi.stubGlobal('Worker', undefined)
    vi.stubGlobal('crypto', { subtle: { digest } })
    try {
      const file = new File(['abcdefghij'], 'large.md', { type: 'text/markdown' })
      const arrayBuffer = vi.spyOn(file, 'arrayBuffer')

      await expect(sha256File(file)).resolves.toBe(
        '72399361da6a7754fec986dca5b7cbaf1c810a28ded4abaf56b2106d06cb78b0',
      )
      expect(digest).not.toHaveBeenCalled()
      expect(arrayBuffer).not.toHaveBeenCalled()
    } finally {
      vi.unstubAllGlobals()
    }
  })

  it.each(['empty-result', 'worker-error'] as const)(
    'rejects instead of hanging when %s and the fallback cannot read the file',
    async (failureMode) => {
      class FailingHashWorker {
        onmessage: ((event: MessageEvent<{ digest?: string }>) => void) | null = null
        onerror: ((event: ErrorEvent) => void) | null = null
        onmessageerror: ((event: MessageEvent) => void) | null = null

        terminate(): void {}

        postMessage(): void {
          queueMicrotask(() => {
            if (failureMode === 'empty-result') {
              this.onmessage?.({ data: {} } as MessageEvent<{ digest?: string }>)
            } else {
              this.onerror?.({} as ErrorEvent)
            }
          })
        }
      }

      vi.stubGlobal('Worker', FailingHashWorker)
      const unreadableFile = {
        size: 1,
        slice: () => ({ arrayBuffer: async () => Promise.reject(new Error('read failed')) }),
      } as unknown as File
      try {
        await expect(sha256File(unreadableFile)).rejects.toThrow('read failed')
      } finally {
        vi.unstubAllGlobals()
      }
    },
  )

  it('hashes manifests without Web Crypto so the public HTTP origin can start a batch', async () => {
    vi.stubGlobal('crypto', { randomUUID: () => 'operation-id' })
    try {
      await expect(sha256Hex(new TextEncoder().encode('abc'))).resolves.toBe(
        'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad',
      )
      const createUploadBatch = vi.fn().mockResolvedValue(createAcceptedBatch('batch-http', [{
        id: 'http-file', path: '金博/http.md', size: 4,
      }]))
      const result = await uploadBulkImpl({
        createUploadBatch,
        openUploadItemSession: vi.fn().mockResolvedValue(openedSession('item-http-file')),
        putUploadObject: vi.fn(),
        completeUploadItem: vi.fn().mockResolvedValue({
          version_id: 'version-http', document_id: 'document-http', ingest_job_id: 'job-http', status: 'COMPLETING',
        }),
        abortUploadItem: vi.fn(),
      } as never, 'kb-jinbo', [{
        id: 'http-file', file: new File(['data'], 'http.md'), path: '金博/http.md', size: 4,
      }], { mode: 'DIRECTORY' })

      expect(createUploadBatch).toHaveBeenCalledTimes(1)
      expect(result.summary).toMatchObject({ successCount: 1, failedCount: 0 })
    } finally {
      vi.unstubAllGlobals()
    }
  })

  it('emits byte-level progress while a direct object upload is in flight', async () => {
    const progress: BulkUploadProgress[] = []
    const putUploadObject = vi.fn(async (_url: string, _file: File, onProgress?: (bytes: number) => void) => {
      onProgress?.(2)
      onProgress?.(4)
    })
    await uploadBulkImpl({
      createUploadBatch: vi.fn().mockResolvedValue(createAcceptedBatch('batch-progress', [{
        id: 'progress-file', path: '金博/progress.md', size: 4,
      }])),
      openUploadItemSession: vi.fn().mockResolvedValue(openedSession('item-progress-file')),
      putUploadObject,
      completeUploadItem: vi.fn().mockResolvedValue({
        version_id: 'version-progress', document_id: 'document-progress', ingest_job_id: 'job-progress', status: 'COMPLETING',
      }),
      abortUploadItem: vi.fn(),
    } as never, 'kb-jinbo', [{
      id: 'progress-file', file: new File(['data'], 'progress.md'), path: '金博/progress.md', size: 4,
    }], {
      mode: 'DIRECTORY',
      onProgress: (next) => progress.push(next),
    })

    expect(putUploadObject).toHaveBeenCalledTimes(1)
    expect(progress.some((next) => next.perFile.get('progress-file')?.uploadedBytes === 2)).toBe(true)
    expect(progress.at(-1)?.perFile.get('progress-file')).toMatchObject({ status: 'success', uploadedBytes: 4 })
  })

  it('creates one DIRECTORY batch and uploads only server-accepted files', async () => {
    const createUploadBatch = vi.fn().mockResolvedValue({
      id: 'batch-directory',
      batch_id: 'batch-directory',
      status: 'ACCEPTED',
      created: true,
      items: [
        acceptedItem('first', '金博/操作手册.txt'),
        {
          client_item_id: 'rejected',
          accepted: false,
          normalized_relative_path: null,
          display_path: '金博/空文件.txt',
          byte_size: 0,
          detected_mime: 'text/plain',
          error_code: 'FILE_EMPTY',
          error_detail: { byte_size: 0 },
          upload_item_id: null,
          upload_session: null,
        },
      ],
    })
    const openUploadItemSession = vi.fn().mockImplementation((itemId: string) => openedSession(itemId))
    const putUploadObject = vi.fn().mockResolvedValue({ object_key: 'uploads/object', sha256: 'a'.repeat(64), byte_size: 4 })
    const completeUploadItem = vi.fn().mockResolvedValue({
      version_id: 'version-1',
      document_id: 'document-1',
      ingest_job_id: 'job-1',
      status: 'COMPLETING',
    })
    const progress: BulkUploadProgress[] = []
    const files = [
      {
        id: 'first',
        file: new File(['data'], '操作手册.txt', { type: 'text/plain', lastModified: 1 }),
        path: '金博/操作手册.txt',
        size: 4,
      },
      {
        id: 'rejected',
        file: new File([], '空文件.txt', { type: 'text/plain', lastModified: 2 }),
        path: '金博/空文件.txt',
        size: 0,
      },
    ] as const

    const result = await uploadBulkImpl({
      createUploadBatch,
      openUploadItemSession,
      putUploadObject,
      completeUploadItem,
      abortUploadItem: vi.fn(),
    } as never, 'kb-jinbo', files, {
      mode: 'DIRECTORY',
      concurrency: 2,
      onProgress: (next) => progress.push(next),
    })

    expect(createUploadBatch).toHaveBeenCalledTimes(1)
    expect(createUploadBatch).toHaveBeenCalledWith('kb-jinbo', expect.objectContaining({
      mode: 'DIRECTORY',
      items: [
        expect.objectContaining({ client_item_id: 'first', relative_path: '金博/操作手册.txt' }),
        expect.objectContaining({ client_item_id: 'rejected', relative_path: '金博/空文件.txt' }),
      ],
    }))
    expect(openUploadItemSession).toHaveBeenCalledWith('item-first')
    expect(putUploadObject).toHaveBeenCalledTimes(1)
    expect(completeUploadItem).toHaveBeenCalledWith('item-first', expect.objectContaining({ sha256: expect.stringMatching(/^[a-f0-9]{64}$/) }))
    expect(result.summary).toMatchObject({ total: 2, successCount: 1, failedCount: 1, skippedCount: 0 })
    expect(progress.at(-1)?.perFile.get('first')).toMatchObject({ status: 'success', batchId: 'batch-directory' })
    expect(progress.at(-1)?.perFile.get('rejected')).toMatchObject({ status: 'failed', batchId: 'batch-directory', error: { code: 'FILE_EMPTY' } })
    expect(progress.at(-1)?.perFile.get('rejected')?.uploadItemId).toBeUndefined()
  })

  it('re-preflights rejected items instead of opening their internal upload ID', async () => {
    const createUploadBatch = vi.fn().mockImplementation(async (_kbId, payload) => ({
      id: 'batch-rejected',
      batch_id: 'batch-rejected',
      status: 'ACCEPTED',
      created: false,
      items: payload.items.map((item: { client_item_id: string; relative_path: string; byte_size: number }) => ({
        client_item_id: item.client_item_id,
        accepted: false,
        normalized_relative_path: null,
        display_path: item.relative_path,
        byte_size: item.byte_size,
        detected_mime: 'text/markdown',
        error_code: 'FILE_EMPTY',
        error_detail: { byte_size: 0 },
        upload_item_id: 'internal-rejected-item-id',
        upload_session: null,
      })),
    }))
    const openUploadItemSession = vi.fn()
    const client = {
      createUploadBatch,
      openUploadItemSession,
      putUploadObject: vi.fn(),
      completeUploadItem: vi.fn(),
      abortUploadItem: vi.fn(),
    } as never
    const item = { id: 'empty', file: new File([], 'empty.md'), path: '金博/empty.md', size: 0 }

    await uploadBulkImpl(client, 'kb-jinbo', [item], { mode: 'DIRECTORY', operationId: 'rejected-operation' })
    await uploadBulkImpl(client, 'kb-jinbo', [item], { mode: 'DIRECTORY', operationId: 'rejected-operation' })

    expect(createUploadBatch).toHaveBeenCalledTimes(2)
    expect(openUploadItemSession).not.toHaveBeenCalled()
  })

  it('resumes an idempotent replay using the same batch contract', async () => {
    const createUploadBatch = vi.fn().mockResolvedValue({
      id: 'batch-replay',
      batch_id: 'batch-replay',
      status: 'ACCEPTED',
      created: false,
      items: [acceptedItem('resume', '金博/续传.md')],
    })
    const openUploadItemSession = vi.fn().mockImplementation((itemId: string) => openedSession(itemId))
    const putUploadObject = vi.fn().mockResolvedValue({})
    const completeUploadItem = vi.fn().mockResolvedValue({
      version_id: 'version-resume',
      document_id: 'document-resume',
      ingest_job_id: 'job-resume',
      status: 'COMPLETING',
    })

    const result = await uploadBulkImpl({
      createUploadBatch,
      openUploadItemSession,
      putUploadObject,
      completeUploadItem,
      abortUploadItem: vi.fn(),
    } as never, 'kb-jinbo', [{
      id: 'resume',
      file: new File(['data'], '续传.md', { type: 'text/markdown' }),
      path: '金博/续传.md',
      size: 4,
    }], { mode: 'DIRECTORY' })

    expect(createUploadBatch).toHaveBeenCalledTimes(1)
    expect(putUploadObject).toHaveBeenCalledTimes(1)
    expect(result.summary).toMatchObject({ successCount: 1, failedCount: 0 })
  })

  it('resumes the original completed item without creating a replacement batch', async () => {
    const createUploadBatch = vi.fn()
    const openUploadItemSession = vi.fn().mockResolvedValue({
      already_completed: true,
      document_id: 'document-original',
      ingest_job_id: 'job-original',
      status: 'COMPLETING',
    })
    const putUploadObject = vi.fn()
    const completeUploadItem = vi.fn()
    const result = await uploadBulkImpl({
      createUploadBatch,
      openUploadItemSession,
      putUploadObject,
      completeUploadItem,
      abortUploadItem: vi.fn(),
    } as never, 'kb-jinbo', [{
      id: 'lost-response',
      file: new File(['data'], 'lost-response.md', { type: 'text/markdown' }),
      path: '金博/lost-response.md',
      size: 4,
    }], {
      mode: 'DIRECTORY',
      resumeItems: new Map([['lost-response', { batchId: 'batch-original', uploadItemId: 'item-original' }]]),
    })

    expect(createUploadBatch).not.toHaveBeenCalled()
    expect(openUploadItemSession).toHaveBeenCalledWith('item-original')
    expect(putUploadObject).not.toHaveBeenCalled()
    expect(completeUploadItem).not.toHaveBeenCalled()
    expect(result.summary).toMatchObject({ successCount: 1, failedCount: 0 })
  })

  it('opens each upload session only when that worker starts its object transfer', async () => {
    const events: string[] = []
    const createUploadBatch = vi.fn().mockImplementation(async (_kbId, payload) => {
      events.push('create-batch')
      return createAcceptedBatch('batch-lazy', payload.items.map((item: { client_item_id: string; relative_path: string; byte_size: number }) => ({
        id: item.client_item_id,
        path: item.relative_path,
        size: item.byte_size,
      })))
    })
    const openUploadItemSession = vi.fn().mockImplementation(async (itemId: string) => {
      events.push(`open:${itemId}`)
      return openedSession(itemId)
    })
    const putUploadObject = vi.fn().mockImplementation(async (uploadUrl: string) => {
      events.push(`put:${uploadUrl.includes('item-one') ? 'item-one' : 'item-two'}`)
    })
    const completeUploadItem = vi.fn().mockImplementation(async (itemId: string) => {
      events.push(`complete:${itemId}`)
      return { version_id: `version-${itemId}`, document_id: `document-${itemId}`, ingest_job_id: `job-${itemId}`, status: 'COMPLETING' }
    })

    await uploadBulkImpl({
      createUploadBatch,
      openUploadItemSession,
      putUploadObject,
      completeUploadItem,
      abortUploadItem: vi.fn(),
    } as never, 'kb-jinbo', [
      { id: 'one', file: new File(['one'], 'one.md'), path: '金博/one.md', size: 3 },
      { id: 'two', file: new File(['two'], 'two.md'), path: '金博/two.md', size: 3 },
    ], { mode: 'DIRECTORY', concurrency: 1 })

    expect(events).toEqual([
      'create-batch',
      'open:item-one', 'put:item-one', 'complete:item-one',
      'open:item-two', 'put:item-two', 'complete:item-two',
    ])
  })

  it('scopes automatic batch idempotency keys to one upload operation', async () => {
    const createUploadBatch = vi.fn().mockImplementation(async (_kbId, payload) => ({
      id: `batch-${createUploadBatch.mock.calls.length}`,
      batch_id: `batch-${createUploadBatch.mock.calls.length}`,
      status: 'ACCEPTED',
      created: true,
      items: payload.items.map((item: { client_item_id: string; relative_path: string; byte_size: number }) => ({
        client_item_id: item.client_item_id,
        accepted: false,
        normalized_relative_path: null,
        display_path: item.relative_path,
        byte_size: item.byte_size,
        detected_mime: 'text/markdown',
        error_code: 'FILE_EMPTY',
        error_detail: null,
        upload_item_id: null,
        upload_session: null,
      })),
    }))
    const client = {
      createUploadBatch,
      openUploadItemSession: vi.fn(),
      putUploadObject: vi.fn(),
      completeUploadItem: vi.fn(),
      abortUploadItem: vi.fn(),
    } as never
    const item = { id: 'same-file', file: new File(['data'], 'same.md'), path: '金博/same.md', size: 4 }

    await uploadBulkImpl(client, 'kb-jinbo', [item], { mode: 'DIRECTORY', operationId: 'operation-one' })
    await uploadBulkImpl(client, 'kb-jinbo', [item], { mode: 'DIRECTORY', operationId: 'operation-one' })
    await uploadBulkImpl(client, 'kb-jinbo', [item], { mode: 'DIRECTORY', operationId: 'operation-two' })

    const firstKey = createUploadBatch.mock.calls[0]?.[1].client_request_id
    expect(createUploadBatch.mock.calls[1]?.[1].client_request_id).toBe(firstKey)
    expect(createUploadBatch.mock.calls[2]?.[1].client_request_id).not.toBe(firstKey)
  })

  it('retains the first partition reference when a later create request fails', async () => {
    const progress: BulkUploadProgress[] = []
    const createUploadBatch = vi.fn().mockImplementation(async (_kbId, payload) => {
      if (createUploadBatch.mock.calls.length === 1) {
        return createAcceptedBatch('batch-first', payload.items.map((item: { client_item_id: string; relative_path: string; byte_size: number }) => ({
          id: item.client_item_id,
          path: item.relative_path,
          size: item.byte_size,
        })))
      }
      throw new Error('object storage unavailable')
    })
    const items = Array.from({ length: MAX_CLIENT_BATCH_FILES + 1 }, (_, index) => ({
      id: `partition-${index}`,
      file: new File(['x'], `partition-${index}.md`),
      path: `金博/partition-${index}.md`,
      size: 1,
    }))

    await expect(uploadBulkImpl({
      createUploadBatch,
      openUploadItemSession: vi.fn(),
      putUploadObject: vi.fn(),
      completeUploadItem: vi.fn(),
      abortUploadItem: vi.fn(),
    } as never, 'kb-jinbo', items, {
      mode: 'DIRECTORY',
      operationId: 'partial-create-operation',
      onProgress: (next) => progress.push(next),
    })).rejects.toThrow('object storage unavailable')

    expect(progress.some((entry) => entry.perFile.get('partition-0')?.batchId === 'batch-first')).toBe(true)
    expect(progress.find((entry) => entry.perFile.get('partition-0')?.batchId === 'batch-first')?.perFile.get('partition-0')).toMatchObject({
      uploadItemId: 'item-partition-0',
    })
  })

  it('splits directories at the server file and byte limits', async () => {
    const createUploadBatch = vi.fn().mockImplementation(async (_kbId, payload) => ({
      id: `batch-${createUploadBatch.mock.calls.length}`,
      batch_id: `batch-${createUploadBatch.mock.calls.length}`,
      status: 'ACCEPTED',
      created: true,
      items: payload.items.map((item: { client_item_id: string; relative_path: string; byte_size: number }) => ({
        client_item_id: item.client_item_id,
        accepted: false,
        normalized_relative_path: null,
        display_path: item.relative_path,
        byte_size: item.byte_size,
        detected_mime: 'text/plain',
        error_code: 'FILE_EMPTY',
        error_detail: null,
        upload_item_id: null,
        upload_session: null,
      })),
    }))
    const makeItem = (index: number, size = 1) => ({
      id: `item-${index}`,
      file: new File([], `item-${index}.txt`, { type: 'text/plain' }),
      path: `金博/item-${index}.txt`,
      size,
    })

    await uploadBulkImpl({
      createUploadBatch,
      openUploadItemSession: vi.fn(),
      putUploadObject: vi.fn(),
      completeUploadItem: vi.fn(),
      abortUploadItem: vi.fn(),
    } as never, 'kb-jinbo', Array.from({ length: MAX_CLIENT_BATCH_FILES + 1 }, (_, index) => makeItem(index)), {
      mode: 'DIRECTORY',
    })

    expect(createUploadBatch).toHaveBeenCalledTimes(2)
    expect(createUploadBatch.mock.calls[0]?.[1].items).toHaveLength(MAX_CLIENT_BATCH_FILES)
    expect(createUploadBatch.mock.calls[1]?.[1].items).toHaveLength(1)

    createUploadBatch.mockClear()
    await uploadBulkImpl({
      createUploadBatch,
      openUploadItemSession: vi.fn(),
      putUploadObject: vi.fn(),
      completeUploadItem: vi.fn(),
      abortUploadItem: vi.fn(),
    } as never, 'kb-jinbo', [
      makeItem(1, MAX_CLIENT_BATCH_BYTES - 1),
      makeItem(2, 2),
    ], { mode: 'DIRECTORY' })
    expect(createUploadBatch).toHaveBeenCalledTimes(2)
  })

  it('marks cancelled queue entries as skipped even when abort fails', async () => {
    const controller = new AbortController()
    controller.abort()
    const abortUploadItem = vi.fn().mockRejectedValue(new Error('abort unavailable'))
    const progress: BulkUploadProgress[] = []

    const result = await uploadBulkImpl({
      createUploadBatch: vi.fn().mockResolvedValue({
        id: 'batch-cancel',
        batch_id: 'batch-cancel',
        status: 'ACCEPTED',
        created: true,
        items: [acceptedItem('cancelled', '金博/取消.md')],
      }),
      openUploadItemSession: vi.fn(),
      putUploadObject: vi.fn(),
      completeUploadItem: vi.fn(),
      abortUploadItem,
    } as never, 'kb-jinbo', [{
      id: 'cancelled',
      file: new File(['data'], '取消.md', { type: 'text/markdown' }),
      path: '金博/取消.md',
      size: 4,
    }], { mode: 'DIRECTORY', signal: controller.signal, onProgress: (next) => progress.push(next) })

    expect(abortUploadItem).toHaveBeenCalledWith('item-cancelled')
    expect(result.summary).toMatchObject({ successCount: 0, failedCount: 0, skippedCount: 1 })
    expect(progress.at(-1)?.perFile.get('cancelled')).toMatchObject({ status: 'skipped' })
  })
})
