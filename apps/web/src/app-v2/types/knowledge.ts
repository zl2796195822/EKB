import type { AdapterError } from './adapters'
import type { PageState } from './view-state'

export interface KnowledgeBaseView {
  readonly id: string
  readonly name: string
  readonly description: string
  readonly visibility: 'PRIVATE' | 'TEAM' | 'PUBLIC'
  readonly role: string
  readonly documentCount: number
  readonly createdAt: string
  readonly updatedAt: string
}

export interface KbMemberView {
  readonly id: string
  readonly userId: string
  readonly role: string
  readonly grantedBy?: string | null
  readonly createdAt: string
  readonly updatedAt: string
}

export interface KnowledgeListResult {
  readonly state: PageState
  readonly data?: readonly KnowledgeBaseView[]
  readonly error?: AdapterError
}

export interface KnowledgeItemResult {
  readonly state: PageState
  readonly data?: KnowledgeBaseView
  readonly error?: AdapterError
}

export interface MemberListResult {
  readonly state: PageState
  readonly data?: readonly KbMemberView[]
  readonly error?: AdapterError
}

export interface KnowledgeWriteResult {
  readonly state: PageState
  readonly data?: KnowledgeBaseView
  readonly error?: AdapterError
}

export interface FolderView {
  readonly id: string
  readonly kbId: string
  readonly parentId: string | null
  readonly name: string
  readonly childCount: number
  readonly documentCount: number
}

export interface FolderListResult {
  readonly state: PageState
  readonly data?: readonly FolderView[]
  readonly error?: AdapterError
}

export interface FolderWriteResult {
  readonly state: PageState
  readonly data?: FolderView
  readonly error?: AdapterError
}

export interface KnowledgeServices {
  readonly list: () => Promise<KnowledgeListResult>
  readonly get: (kbId: string) => Promise<KnowledgeItemResult>
  readonly create: (input: { readonly name: string; readonly description?: string; readonly visibility?: KnowledgeBaseView['visibility'] }) => Promise<KnowledgeWriteResult>
  readonly update: (kbId: string, input: { readonly name?: string; readonly description?: string; readonly visibility?: KnowledgeBaseView['visibility'] }) => Promise<KnowledgeWriteResult>
  readonly remove: (kbId: string) => Promise<{ readonly state: PageState; readonly error?: AdapterError }>
  readonly listMembers: (kbId: string) => Promise<MemberListResult>
  readonly addMember: (kbId: string, input: { readonly email: string; readonly role: string }) => Promise<{ readonly state: PageState; readonly data?: KbMemberView; readonly error?: AdapterError }>
  readonly removeMember: (kbId: string, userId: string) => Promise<{ readonly state: PageState; readonly error?: AdapterError }>
  readonly listFolders: (kbId: string, parentId?: string | null) => Promise<FolderListResult>
  readonly createFolder: (kbId: string, input: { readonly name: string; readonly parentId?: string | null }) => Promise<FolderWriteResult>
  readonly updateFolder: (folderId: string, input: { readonly name?: string; readonly parentId?: string | null }) => Promise<FolderWriteResult>
  readonly removeFolder: (folderId: string) => Promise<{ readonly state: PageState; readonly error?: AdapterError }>
}
