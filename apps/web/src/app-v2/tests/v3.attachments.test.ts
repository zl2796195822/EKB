import { describe, expect, it, vi } from 'vitest'
import { ApiClientError } from '../../lib/api'
import { createAttachmentsAdapter } from '../adapters/attachments'

describe('attachments promotion adapter', () => {
  it('calls the real promotion endpoint with separate attachment and KB ids', async () => {
    const promoteAttachment = vi.fn().mockResolvedValue({
      promotion_id: 'promotion-1',
      attachment_id: 'attachment-1',
      target_knowledge_base_id: 'kb-2',
      normalized_relative_path: '资料/附件.txt',
      client_request_id: '00000000-0000-4000-8000-000000000001',
      document_id: 'doc-1',
      document_version_id: 'version-1',
      ingest_job_id: 'job-1',
      status: 'QUEUED',
      request_id: 'trace-1',
    })
    const randomUUID = vi
      .spyOn(crypto, 'randomUUID')
      .mockReturnValue('00000000-0000-4000-8000-000000000001')

    const result = await createAttachmentsAdapter({ promoteAttachment } as never).promote('attachment-1', {
      targetKnowledgeBaseId: 'kb-2',
      relativePath: '资料/附件.txt',
    })

    expect(randomUUID).toHaveBeenCalledOnce()
    expect(promoteAttachment).toHaveBeenCalledWith('attachment-1', {
      target_knowledge_base_id: 'kb-2',
      relative_path: '资料/附件.txt',
      client_request_id: '00000000-0000-4000-8000-000000000001',
    })
    expect(result).toMatchObject({
      state: 'ready',
      data: {
        promotionId: 'promotion-1',
        attachmentId: 'attachment-1',
        targetKnowledgeBaseId: 'kb-2',
        status: 'QUEUED',
        ingestJobId: 'job-1',
      },
    })
    randomUUID.mockRestore()
  })

  it('keeps real 409/403-style errors as failures', async () => {
    const promoteAttachment = vi.fn().mockRejectedValue(
      new ApiClientError(409, 'PATH_CONFLICT', '目标知识库已存在同路径文档'),
    )

    const result = await createAttachmentsAdapter({ promoteAttachment } as never).promote('attachment-1', {
      targetKnowledgeBaseId: 'kb-1',
      relativePath: '冲突.txt',
    })

    expect(result.state).toBe('error')
    expect(result.data).toBeUndefined()
    expect(result.error).toMatchObject({
      status: 409,
      code: 'PATH_CONFLICT',
      message: '目标知识库已存在同路径文档',
    })
  })
})
