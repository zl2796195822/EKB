import type { AdapterError } from './adapters'
import type { PageState } from './view-state'

export interface SearchHitView {
  readonly chunkId: string
  readonly documentId: string
  readonly knowledgeBaseId: string
  readonly title: string
  readonly sectionPath: readonly string[]
  readonly snippet: string
  readonly score: number
  readonly updatedAt: string
}

export interface SearchResultView {
  readonly state: PageState
  readonly data?: readonly SearchHitView[]
  readonly error?: AdapterError
}

export interface SearchServices {
  readonly search: (query: string, kbId: string) => Promise<SearchResultView>
}
