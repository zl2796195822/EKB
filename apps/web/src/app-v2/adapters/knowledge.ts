import type { ApiClient } from '../../lib/api'
import type { AdapterCapability } from '../types'
import type {
  KbMemberView,
  FolderView,
  KnowledgeBaseView,
  KnowledgeServices,
  KnowledgeWriteResult,
} from '../types'
import { stateForData, stateForError, toAdapterError } from './index'

export interface KnowledgeSpace {
  readonly id: string
  readonly name: string
}

export interface KnowledgeAdapter {
  readonly service: KnowledgeServices
}

function mapKnowledgeBase(input: import('../../types/api').KnowledgeBase): KnowledgeBaseView {
  return {
    id: input.id,
    name: input.name,
    description: input.description,
    visibility: input.visibility,
    role: input.role,
    documentCount: input.document_count,
    createdAt: input.created_at,
    updatedAt: input.updated_at,
  }
}

function mapMember(input: import('../../types/api').KbMemberRecord): KbMemberView {
  return {
    id: input.id,
    userId: input.user_id,
    role: input.role,
    grantedBy: input.granted_by,
    createdAt: input.created_at,
    updatedAt: input.updated_at,
  }
}

function mapFolder(input: import('../../types/api').FolderRecord): FolderView {
  return {
    id: input.id,
    kbId: input.kb_id,
    parentId: input.parent_id,
    name: input.name,
    childCount: input.child_count,
    documentCount: input.document_count,
  }
}

function errorResult<T>(error: unknown, message: string): { state: 'error' | 'permission-denied'; error: ReturnType<typeof toAdapterError> } {
  const mapped = toAdapterError(error, message)
  return { state: stateForError(mapped) as 'error' | 'permission-denied', error: mapped }
}

export function createKnowledgeAdapter(client: ApiClient): KnowledgeServices {
  return {
    list: async () => {
      try {
        const data = (await client.listKnowledgeBases()).map(mapKnowledgeBase)
        return { state: stateForData(data), data }
      } catch (error) {
        return errorResult(error, '知识库列表加载失败')
      }
    },
    get: async (kbId) => {
      try {
        return { state: 'ready', data: mapKnowledgeBase(await client.getKnowledgeBase(kbId)) }
      } catch (error) {
        return errorResult(error, '知识库详情加载失败')
      }
    },
    create: async (input): Promise<KnowledgeWriteResult> => {
      try {
        const data = mapKnowledgeBase(
          await client.createKnowledgeBase(input.name, input.description ?? '', input.visibility ?? 'PRIVATE'),
        )
        return { state: 'ready', data }
      } catch (error) {
        return errorResult(error, '知识库创建失败')
      }
    },
    update: async (kbId, input) => {
      try {
        const data = mapKnowledgeBase(await client.updateKnowledgeBase(kbId, input))
        return { state: 'ready', data }
      } catch (error) {
        return errorResult(error, '知识库更新失败')
      }
    },
    remove: async (kbId) => {
      try {
        await client.deleteKnowledgeBase(kbId)
        return { state: 'ready' }
      } catch (error) {
        return errorResult(error, '知识库删除失败')
      }
    },
    listMembers: async (kbId) => {
      try {
        const data = (await client.listKbMembers(kbId)).map(mapMember)
        return { state: stateForData(data), data }
      } catch (error) {
        return errorResult(error, '成员列表加载失败')
      }
    },
    addMember: async (kbId, input) => {
      try {
        const data = mapMember(await client.addKbMember(kbId, input.email, input.role as 'OWNER' | 'ADMIN' | 'EDITOR' | 'VIEWER'))
        return { state: 'ready', data }
      } catch (error) {
        return errorResult(error, '成员添加失败')
      }
    },
    removeMember: async (kbId, userId) => {
      try {
        await client.removeKbMember(kbId, userId)
        return { state: 'ready' }
      } catch (error) {
        return errorResult(error, '成员移除失败')
      }
    },
    listFolders: async (kbId, parentId = null) => {
      try {
        const data = (await client.listFolders(kbId, parentId)).items.map(mapFolder)
        return { state: stateForData(data), data }
      } catch (error) {
        return errorResult(error, '文件夹列表加载失败')
      }
    },
    createFolder: async (kbId, input) => {
      try {
        const data = mapFolder(await client.createFolder(kbId, input.name, input.parentId))
        return { state: 'ready', data }
      } catch (error) {
        return errorResult(error, '文件夹创建失败')
      }
    },
    updateFolder: async (folderId, input) => {
      try {
        const data = mapFolder(await client.updateFolder(folderId, {
          ...(input.name !== undefined ? { name: input.name } : {}),
          ...(input.parentId !== undefined ? { parent_id: input.parentId } : {}),
        }))
        return { state: 'ready', data }
      } catch (error) {
        return errorResult(error, '文件夹更新失败')
      }
    },
    removeFolder: async (folderId) => {
      try {
        await client.deleteFolder(folderId)
        return { state: 'ready' }
      } catch (error) {
        return errorResult(error, '文件夹删除失败')
      }
    },
  }
}

export const KNOWLEDGE_CAPABILITIES = [
  {
    id: 'knowledge.kb-crud-members',
    status: 'available',
  },
  {
    id: 'knowledge.folder-favorites-shared-recycle',
    status: 'unavailable',
    reason: '共享文档与回收站批量治理仍在后续阶段；文件夹和收藏已接入真实端点。',
  },
] as const satisfies readonly AdapterCapability[]
