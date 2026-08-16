import { useSyncExternalStore } from 'react'
import { uploadTaskStore } from './uploadTaskStore'

/** 订阅模块级上传任务 store 的 React 钩子（跨路由生命周期）。 */
export function useUploadTaskStore() {
  return useSyncExternalStore(
    uploadTaskStore.subscribe,
    uploadTaskStore.getSnapshot,
    uploadTaskStore.getSnapshot,
  )
}
