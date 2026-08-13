import { createDocumentsAdapter, createKnowledgeAdapter, createSearchAdapter } from '../adapters'
import { it } from 'vitest'

function assert(condition: unknown, message: string): asserts condition {
  if (!condition) throw new Error(message)
}

export async function runM3ContractTests(): Promise<void> {
  const calls: string[] = []
  const fakeClient = {
    listKnowledgeBases: async () => [{
      id: 'kb-1',
      tenant_id: 'tenant-1',
      name: '真实知识库',
      description: '来自 API',
      visibility: 'TEAM' as const,
      role: 'OWNER',
      document_count: 1,
      created_at: '2026-08-09T00:00:00Z',
      updated_at: '2026-08-09T00:00:00Z',
    }],
    getKnowledgeBase: async () => ({
      id: 'kb-1', tenant_id: 'tenant-1', name: '真实知识库', description: '来自 API', visibility: 'TEAM' as const,
      role: 'OWNER', document_count: 1, created_at: '2026-08-09T00:00:00Z', updated_at: '2026-08-09T00:00:00Z',
    }),
    createKnowledgeBase: async (_name: string, _description: string, visibility: 'PRIVATE' | 'TEAM' | 'PUBLIC') => {
      calls.push(`create:${visibility}`)
      return {
        id: 'kb-2', tenant_id: 'tenant-1', name: '新知识库', description: '', visibility,
        role: 'OWNER', document_count: 0, created_at: '2026-08-09T00:00:00Z', updated_at: '2026-08-09T00:00:00Z',
      }
    },
    updateKnowledgeBase: async (_id: string, input: { visibility?: 'PRIVATE' | 'TEAM' | 'PUBLIC' }) => ({
      id: 'kb-1', tenant_id: 'tenant-1', name: '真实知识库', description: '', visibility: input.visibility ?? 'PRIVATE',
      role: 'OWNER', document_count: 1, created_at: '2026-08-09T00:00:00Z', updated_at: '2026-08-09T00:00:00Z',
    }),
    deleteKnowledgeBase: async () => undefined,
    listKbMembers: async () => [{ id: 'membership-1', kb_id: 'kb-1', user_id: 'user-1', role: 'OWNER', granted_by: null, created_at: '2026-08-09T00:00:00Z', updated_at: '2026-08-09T00:00:00Z' }],
    addKbMember: async () => ({ id: 'membership-2', kb_id: 'kb-1', user_id: 'user-2', role: 'VIEWER', granted_by: 'user-1', created_at: '2026-08-09T00:00:00Z', updated_at: '2026-08-09T00:00:00Z' }),
    removeKbMember: async () => undefined,
    listDocuments: async () => [{ id: 'doc-1', kb_id: 'kb-1', title: '真实文档', status: 'PROCESSING' as const, version: 1, mime_type: 'text/plain', checksum: 'sha', chunk_count: 1, file_size: 4, failure_reason: null, created_at: '2026-08-09T00:00:00Z', updated_at: '2026-08-09T00:00:00Z' }],
    getDocument: async () => ({ id: 'doc-1', kb_id: 'kb-1', title: '真实文档', status: 'READY' as const, version: 1, mime_type: 'text/plain', checksum: 'sha', chunk_count: 1, file_size: 4, failure_reason: null, created_at: '2026-08-09T00:00:00Z', updated_at: '2026-08-09T00:00:00Z' }),
    uploadDocument: async () => ({ doc_id: 'doc-2', job_id: 'job-2', status: 'PROCESSING', trace_id: 'trace-2' }),
    deleteDocument: async () => undefined,
    retryDocument: async () => ({ status: 'accepted', trace_id: 'trace-3' }),
    getUploadBatch: async () => ({
      id: 'batch-1', kb_id: 'kb-1', mode: 'FILE', status: 'processing', item_count: 1, total_bytes: 4,
      created_at: '2026-08-09T00:00:00Z', updated_at: '2026-08-09T00:01:00Z', counts: { processing: 1 },
      items: [{
        id: 'item-1', client_item_id: 'client-1', relative_path: 'docs/real.txt', status: 'processing',
        source_status: 'UPLOADED', byte_size: 4, uploaded_bytes: 4, stage: 'PARSING', attempt_id: 'attempt-1',
        attempt_no: 1, attempts: 1, progress: { unit: 'pages', current: 2, total: 4 }, version_id: 'version-1',
        job_id: 'job-1', error: null,
      }],
    }),
    listUploadBatches: async () => ({
      items: [{
        id: 'batch-1', kb_id: 'kb-1', mode: 'FILE', status: 'processing', item_count: 1, total_bytes: 4,
        created_at: '2026-08-09T00:00:00Z', updated_at: '2026-08-09T00:01:00Z', counts: { processing: 1 },
        items: [{
          id: 'item-1', client_item_id: 'client-1', relative_path: 'docs/real.txt', status: 'processing',
          source_status: 'UPLOADED', byte_size: 4, uploaded_bytes: 4, stage: 'PARSING', attempt_id: 'attempt-1',
          attempt_no: 1, attempts: 1, progress: { unit: 'pages', current: 2, total: 4 }, version_id: 'version-1',
          job_id: 'job-1', error: null,
        }],
      }],
      limit: 30,
    }),
    retryIngestJob: async (jobId: string) => ({ success: true, attempt_id: `attempt-retry-${jobId}`, attempt_no: 2 }),
    cancelIngestJob: async () => ({ status: 'CANCELLED' }),
    abortUploadItem: async () => ({ success: true, item_id: 'item-1', status: 'ABORTED' }),
    listDocumentVersions: async () => [{ id: 'version-1', doc_id: 'doc-1', version: 1, checksum: 'sha', chunk_count: 1, content_snapshot: [{ section_path: ['正文'], content_hash: 'hash', content_preview: '摘要' }], created_at: '2026-08-09T00:00:00Z' }],
    getDocumentDiff: async () => ({ doc_id: 'doc-1', from_version: 1, to_version: 2, added: [], removed: [], changed: [], unchanged: [] }),
    search: async (_query: string, kbId: string) => [{ chunk_id: 'chunk-1', doc_id: 'doc-1', kb_id: kbId, title: '真实文档', section_path: [], snippet: '真实命中', score: 0.8, updated_at: '2026-08-09T00:00:00Z' }],
  }

  const client = fakeClient as unknown as Parameters<typeof createKnowledgeAdapter>[0]
  const knowledge = createKnowledgeAdapter(client)
  const documents = createDocumentsAdapter(client)
  const search = createSearchAdapter(client)

  const knowledgeResult = await knowledge.list()
  assert(knowledgeResult.state === 'ready' && knowledgeResult.data?.[0]?.visibility === 'TEAM', 'knowledge adapter should map real visibility')
  await knowledge.create({ name: '新知识库', visibility: 'PUBLIC' })
  assert(calls.includes('create:PUBLIC'), 'knowledge create should forward visibility')

  const documentResult = await documents.list('kb-1')
  assert(documentResult.data?.[0]?.kbId === 'kb-1', 'document adapter should map kb id')
  const uploadResult = await documents.upload('kb-1', new File(['data'], 'real.txt', { type: 'text/plain' }))
  assert(uploadResult.data?.docId === 'doc-2' && uploadResult.data.jobId === 'job-2', 'upload adapter should expose accepted metadata')
  const batchResult = await documents.getUploadBatch('batch-1')
  assert(batchResult.data?.status === 'processing' && batchResult.data.items[0]?.attemptId === 'attempt-1', 'upload center should map server batch/item metadata')
  const batchListResult = await documents.listUploadBatches()
  assert(batchListResult.data?.[0]?.id === 'batch-1' && batchListResult.data[0].items[0]?.jobId === 'job-1', 'upload center list adapter should map server batch projections')
  const retried = await documents.retryUploadJob('job-1')
  assert(retried.data?.attemptNo === 2, 'upload center retry should preserve server attempt number')
  const cancelled = await documents.cancelUploadJob('job-1')
  assert(cancelled.data?.status === 'CANCELLED', 'upload center cancel should preserve server status')
  const aborted = await documents.abortUploadItem('item-1')
  assert(aborted.data?.status === 'ABORTED', 'upload center abort should preserve server status')
  const versionsResult = await documents.listVersions('kb-1', 'doc-1')
  assert(versionsResult.data?.[0]?.contentSnapshot[0]?.sectionPath[0] === '正文', 'version adapter should map snapshot view model')
  const diffResult = await documents.diff('kb-1', 'doc-1', 1, 2)
  assert(diffResult.data?.fromVersion === 1 && diffResult.data.unchanged.length === 0, 'diff adapter should map server diff view model')

  const searchResult = await search.search('真实', 'kb-1')
  assert(searchResult.data?.[0]?.knowledgeBaseId === 'kb-1', 'search adapter should preserve selected KB binding')
}

it('M3 Upload Center contract maps server batch state and actions', async () => {
  await runM3ContractTests()
})
