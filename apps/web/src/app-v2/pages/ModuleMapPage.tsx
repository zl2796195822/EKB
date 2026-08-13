import {
  ChartLineUp,
  ChartPieSlice,
  FileText,
  FolderOpen,
  House,
  Robot,
  SquaresFour,
  Trash,
  UserCircle,
  UsersThree,
} from '@phosphor-icons/react'
import { getModuleMapRoutes, ModuleTile } from '../components/module-map'
import { ModuleMapLink } from '../components/ui'
import type { V2PageProps } from '../types'

const MODULE_METADATA = {
  dashboard: {
    title: '工作台',
    subtitle: 'Dashboard',
    description: '知识资产与使用情况总览',
    icon: <House size={16} />,
  },
  knowledge: {
    title: '知识库',
    subtitle: 'Knowledge Base',
    description: '管理知识空间与内容目录',
    icon: <FolderOpen size={16} />,
  },
  assistant: {
    title: 'AI 助手',
    subtitle: 'AI Assistant',
    description: '基于团队知识进行问答',
    icon: <Robot size={16} />,
  },
  documents: {
    title: '文档中心',
    subtitle: 'Document Center',
    description: '统一管理所有文档',
    icon: <FileText size={16} />,
  },
  team: {
    title: '团队与权限',
    subtitle: 'Team & Permissions',
    description: '成员、角色与空间权限',
    icon: <UsersThree size={16} />,
  },
  analytics: {
    title: '数据看板',
    subtitle: 'Analytics',
    description: '知识库运营与业务洞察',
    icon: <ChartLineUp size={16} />,
  },
  apps: {
    title: '应用中心',
    subtitle: 'App Center',
    description: '发现并连接更多应用',
    icon: <SquaresFour size={16} />,
  },
  recycle: {
    title: '回收站',
    subtitle: 'Recycle Bin',
    description: '管理已删除的文件与空间',
    icon: <Trash size={16} />,
  },
  profile: {
    title: '个人中心 / 设置',
    subtitle: 'Profile & Settings',
    description: '账户、安全与偏好设置',
    icon: <UserCircle size={16} />,
  },
} as const

export function ModuleMapPage(_props: V2PageProps) {
  const moduleRoutes = getModuleMapRoutes()

  return (
    <div className="v2-module-map-page" data-route-id="modules">
      <ModuleMapLink active />
      <section className="v2-module-map-heading" aria-labelledby="v2-module-map-title">
        <div>
          <p className="v2-eyebrow">MODULE MAP / 01 - 09</p>
          <h1 id="v2-module-map-title">知识库模块分布</h1>
          <p>按业务职责拆解后台核心模块，点击任意模块进入对应页面。</p>
        </div>
        <a className="v2-module-map-return" href="#/dashboard">
          <House size={15} aria-hidden="true" />
          返回工作台
        </a>
      </section>
      <section className="v2-module-map-grid" aria-label="知识库业务模块">
        {moduleRoutes.map((route, index) => {
          const metadata = MODULE_METADATA[route.id as keyof typeof MODULE_METADATA]
          return (
            <ModuleTile
              key={route.id}
              index={index + 1}
              routeId={route.id}
              hash={route.hash}
              title={metadata.title}
              subtitle={metadata.subtitle}
              description={metadata.description}
              icon={metadata.icon}
            />
          )
        })}
      </section>
    </div>
  )
}
