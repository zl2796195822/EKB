import { Check, Folder, FolderOpen, PencilSimple, Plus, Trash, X } from '@phosphor-icons/react'
import { useCallback, useEffect, useState } from 'react'
import type { FolderView, KnowledgeServices } from '../../types'
import type { PageState } from '../../types/view-state'

interface FolderTreeProps {
  readonly kbId: string
  readonly services: KnowledgeServices
  readonly canWrite: boolean
}

export function FolderTree({ kbId, services, canWrite }: FolderTreeProps) {
  const [state, setState] = useState<PageState>('loading')
  const [roots, setRoots] = useState<readonly FolderView[]>([])
  const [children, setChildren] = useState<Readonly<Record<string, readonly FolderView[]>>>({})
  const [expanded, setExpanded] = useState<ReadonlySet<string>>(new Set())
  const [newName, setNewName] = useState('')
  const [createParentId, setCreateParentId] = useState<string | null>(null)
  const [editing, setEditing] = useState<{ readonly id: string; readonly name: string } | null>(null)
  const [editName, setEditName] = useState('')
  const [busy, setBusy] = useState(false)
  const loadRoots = useCallback(async () => {
    setState('loading')
    const result = await services.listFolders(kbId)
    setState(result.state)
    setRoots(result.data ?? [])
  }, [kbId, services])
  const reloadTree = useCallback(async (parentId?: string | null) => {
    if (parentId) {
      const result = await services.listFolders(kbId, parentId)
      if (result.data) {
        setChildren((current) => ({ ...current, [parentId]: result.data ?? [] }))
      }
    }
    await loadRoots()
  }, [kbId, loadRoots, services])
  useEffect(() => { void loadRoots() }, [loadRoots])

  const toggle = async (folder: FolderView) => {
    if (expanded.has(folder.id)) {
      setExpanded((current) => new Set([...current].filter((id) => id !== folder.id)))
      return
    }
    if (folder.childCount > 0 && !children[folder.id]) {
      const result = await services.listFolders(kbId, folder.id)
      if (result.data) setChildren((current) => ({ ...current, [folder.id]: result.data ?? [] }))
    }
    setExpanded((current) => new Set([...current, folder.id]))
  }
  const createRoot = async (event: React.FormEvent) => {
    event.preventDefault()
    if (!newName.trim() || busy) return
    setBusy(true)
    const result = await services.createFolder(kbId, {
      name: newName.trim(),
      ...(createParentId ? { parentId: createParentId } : {}),
    })
    setBusy(false)
    if (result.data) {
      setNewName('')
      setCreateParentId(null)
      await reloadTree(createParentId)
    }
  }
  const beginEdit = (folder: FolderView) => {
    setEditing({ id: folder.id, name: folder.name })
    setEditName(folder.name)
  }
  const saveEdit = async (event: React.FormEvent) => {
    event.preventDefault()
    if (!editing || !editName.trim() || busy) return
    setBusy(true)
    const result = await services.updateFolder(editing.id, { name: editName.trim() })
    setBusy(false)
    if (result.data) {
      setEditing(null)
      setEditName('')
      await reloadTree(folderParentId(editing.id))
    }
  }
  const remove = async (folder: FolderView) => {
    if (busy || !window.confirm(`确认删除文件夹「${folder.name}」？`)) return
    setBusy(true)
    const result = await services.removeFolder(folder.id)
    setBusy(false)
    if (result.state === 'ready') {
      await reloadTree(folderParentId(folder.id))
    }
  }
  const folderParentId = (folderId: string): string | null => {
    const all = [...roots, ...Object.values(children).flat()]
    return all.find((folder) => folder.id === folderId)?.parentId ?? null
  }
  const render = (folder: FolderView, depth = 0): React.ReactNode => {
    const isOpen = expanded.has(folder.id)
    return <div key={folder.id} className="v2-m3-folder-row" style={{ paddingLeft: `${depth * 14}px` }}>
      {editing?.id === folder.id ? <form className="v2-m3-folder-edit" onSubmit={(event) => void saveEdit(event)}>
        <input value={editName} onChange={(event) => setEditName(event.target.value)} aria-label={`重命名 ${folder.name}`} autoFocus />
        <button type="submit" disabled={busy || !editName.trim()} aria-label="保存文件夹名称"><Check size={14} /></button>
        <button type="button" onClick={() => setEditing(null)} aria-label="取消重命名"><X size={14} /></button>
      </form> : <div className="v2-m3-folder-line">
        <button type="button" className="v2-m3-folder-toggle" onClick={() => void toggle(folder)} aria-expanded={isOpen}>
          {isOpen ? <FolderOpen size={15} aria-hidden="true" /> : <Folder size={15} aria-hidden="true" />}<span>{folder.name}</span><small>{folder.documentCount}</small>
        </button>
        {canWrite ? <div className="v2-m3-folder-actions">
          <button type="button" onClick={() => { setCreateParentId(folder.id); setNewName('') }} aria-label={`在 ${folder.name} 下新建文件夹`} title="新建子文件夹"><Plus size={13} /></button>
          <button type="button" onClick={() => beginEdit(folder)} aria-label={`重命名 ${folder.name}`} title="重命名"><PencilSimple size={13} /></button>
          <button type="button" onClick={() => void remove(folder)} aria-label={`删除 ${folder.name}`} title="删除"><Trash size={13} /></button>
        </div> : null}
      </div>}
      {isOpen ? (children[folder.id] ?? []).map((child) => render(child, depth + 1)) : null}
    </div>
  }
  return <section className="v2-m3-folder-panel" aria-label="文件夹">
    <div className="v2-m3-context-heading"><strong>文件夹</strong><span>{state === 'loading' ? '加载中…' : `${roots.length} 个根目录`}</span></div>
    {state === 'error' || state === 'permission-denied' ? <p className="v2-m3-inline-error">文件夹加载失败</p> : roots.length === 0 ? <p className="v2-m3-folder-empty">暂无文件夹，可立即创建。</p> : roots.map((folder) => render(folder))}
    {canWrite ? <form className="v2-m3-folder-create" onSubmit={createRoot}>
      <input value={newName} onChange={(event) => setNewName(event.target.value)} placeholder={createParentId ? '新建子文件夹' : '新建根文件夹'} aria-label={createParentId ? '新建子文件夹' : '新建根文件夹'} />
      <button type="submit" disabled={busy || !newName.trim()} aria-label="创建文件夹"><Plus size={15} aria-hidden="true" /></button>
      {createParentId ? <button type="button" onClick={() => { setCreateParentId(null); setNewName('') }} aria-label="取消新建子文件夹"><X size={14} /></button> : null}
    </form> : null}
  </section>
}
