import type { ApiClient } from '../../lib/api'
import type { AdapterCapability } from '../types'
import type { SearchHitView, SearchServices } from '../types'
import { stateForData, stateForError, toAdapterError } from './index'

export interface SearchHit {
  readonly documentId: string
  readonly title: string
  readonly score: number
}

export interface SearchAdapter {
  readonly service: SearchServices
}

export function createSearchAdapter(client: ApiClient): SearchServices {
  return {
    search: async (query, kbId) => {
      try {
        const data: SearchHitView[] = (await client.search(query, kbId)).map((result) => ({
          chunkId: result.chunk_id,
          documentId: result.doc_id,
          knowledgeBaseId: result.kb_id,
          title: result.title,
          sectionPath: result.section_path,
          snippet: result.snippet,
          score: result.score,
          updatedAt: result.updated_at,
        }))
        return { state: stateForData(data), data }
      } catch (error) {
        const mapped = toAdapterError(error, '检索失败')
        return { state: stateForError(mapped), error: mapped }
      }
    },
  }
}

export const SEARCH_CAPABILITIES = [
  {
    id: 'search.embedded-entry-point',
    status: 'available',
    reason: '搜索作为知识库、文档中心、AI 助手和全局入口的 adapter 能力。',
  },
] as const satisfies readonly AdapterCapability[]
