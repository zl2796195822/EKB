import type { FC } from 'react'
import {
  ChartLineUp,
  MagnifyingGlass,
  Target,
  Users,
  Smiley,
  RocketLaunch,
} from '@phosphor-icons/react'
import { GalaxyCard, GalaxyHero, MetricCard } from '../components/ui'

export interface OpsPageProps {
  goToPage: (page: 'qa' | 'kb' | 'search' | 'ops') => void
}

const OpsPage: FC<OpsPageProps> = (props) => {
  const { goToPage } = props

  return (
    <section className="content-rail" style={{ paddingTop: 20 }}>
      <GalaxyHero
        eyebrow="Galaxy Motion v4"
        title="运营中心"
        subtitle="指标看板、权限管理、版本审计将在后续版本上线"
        primaryLabel=""
        secondaryLabel="前往检索中心"
        onPrimary={undefined}
        secondaryIcon={MagnifyingGlass}
        onSecondary={() => goToPage('search')}
      />

      <div style={{ height: 24 }} />

      <div className="kb-metrics-grid">
        <div className="ops-metric-wrap">
          <div className="ops-metric-icon ops-metric-icon-blue">
            <ChartLineUp size={18} weight="fill" aria-hidden="true" />
          </div>
          <MetricCard label="总问答数" value={0} delta={0} />
        </div>
        <div className="ops-metric-wrap">
          <div className="ops-metric-icon ops-metric-icon-teal">
            <MagnifyingGlass size={18} weight="fill" aria-hidden="true" />
          </div>
          <MetricCard label="总命中数" value={0} delta={0} />
        </div>
        <div className="ops-metric-wrap">
          <div className="ops-metric-icon ops-metric-icon-green">
            <Target size={18} weight="fill" aria-hidden="true" />
          </div>
          <MetricCard label="平均相似度" value={0} delta={0} />
        </div>
        <div className="ops-metric-wrap">
          <div className="ops-metric-icon ops-metric-icon-purple">
            <Users size={18} weight="fill" aria-hidden="true" />
          </div>
          <MetricCard label="活跃用户" value={0} delta={0} />
        </div>
        <div className="ops-metric-wrap ops-metric-fifth">
          <div className="ops-metric-icon ops-metric-icon-amber">
            <Smiley size={18} weight="fill" aria-hidden="true" />
          </div>
          <MetricCard label="系统健康度" value={0} delta={0} />
        </div>
      </div>

      <div style={{ height: 24 }} />

      <GalaxyCard variant="conversation" leftAccent>
        <div className="ops-notice-inner">
          <RocketLaunch size={20} weight="fill" aria-hidden="true" />
          <p className="ops-notice-text">
            当前版本 M1 仅交付 QA + KB + 检索中心。运营中心详细指标将在 M2 上线。
          </p>
        </div>
      </GalaxyCard>
    </section>
  )
}

export default OpsPage
