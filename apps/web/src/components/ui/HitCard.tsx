import React from 'react'
import {
  FileText,
  Clock,
  Database,
  FileDoc,
  Share,
  DotsThree,
  ArrowUpRight,
} from '@phosphor-icons/react'
import { markHitHighlight, relativeTime, type MarkedHtml } from '../../utils/text'
import { CornerTag, type CornerTagVariant } from './CornerTag'
import { GlareCard } from './animated/GlareCard'

export type HitCardType = 'document' | 'qa' | 'chat' | 'faq'

export type HitCardVariantMap = {
  document: 'accent'
  qa: 'kb'
  chat: 'default'
  faq: 'variant'
}

export type HitCardMetaChip = {
  label: string
  value?: string
  icon?: 'source' | 'time' | 'kb' | 'doctype'
}

export type HitCardProps = {
  type: HitCardType
  title: string
  score: number
  snippet: string
  highlights: string[]
  updatedAt?: string | number | Date
  sourceLabel?: string
  kbLabel?: string
  docType?: string
  cornerTag?: CornerTagVariant
  onViewOriginal?: () => void
  onShare?: () => void
  onMore?: () => void
  className?: string
}

type LeftBarVariant = 'accent' | 'kb' | 'default' | 'variant'

const TYPE_LEFTBAR_MAP: Record<HitCardType, LeftBarVariant> = {
  document: 'accent',
  qa: 'kb',
  chat: 'default',
  faq: 'variant',
}

export type HeaderScoreProps = {
  title: string
  score: number
  cornerTag?: CornerTagVariant
}

function HeaderScore({ title, score, cornerTag }: HeaderScoreProps): React.ReactElement {
  const scorePercent = Math.max(0, Math.min(100, Math.round(score * 100)))
  return (
    <div className="hit-card-header">
      <div className="hit-card-header-main">
        <h3 className="hit-card-title">{title}</h3>
        <span className="hit-card-score-pill" aria-label={`得分 ${scorePercent}`}>
          <span className="hit-card-score-value">{scorePercent}</span>
          <span className="hit-card-score-unit">分</span>
        </span>
      </div>
      {cornerTag && <CornerTag variant={cornerTag} />}
    </div>
  )
}

export type MetaRowProps = {
  sourceLabel?: string
  updatedAt?: string | number | Date
  kbLabel?: string
  docType?: string
}

function MetaRow({ sourceLabel, updatedAt, kbLabel, docType }: MetaRowProps): React.ReactElement {
  return (
    <div className="hit-card-meta-row" role="list">
      {sourceLabel && (
        <span className="chip hit-card-meta-chip" role="listitem">
          <FileText size={12} weight="fill" aria-hidden="true" />
          <span>{sourceLabel}</span>
        </span>
      )}
      {updatedAt !== undefined && (
        <span className="chip hit-card-meta-chip" role="listitem">
          <Clock size={12} weight="fill" aria-hidden="true" />
          <span>{relativeTime(updatedAt)}</span>
        </span>
      )}
      {kbLabel && (
        <span className="chip hit-card-meta-chip" role="listitem">
          <Database size={12} weight="fill" aria-hidden="true" />
          <span>{kbLabel}</span>
        </span>
      )}
      {docType && (
        <span className="chip hit-card-meta-chip" role="listitem">
          <FileDoc size={12} weight="fill" aria-hidden="true" />
          <span>{docType}</span>
        </span>
      )}
    </div>
  )
}

export type SnippetTextProps = {
  snippet: string
  highlights: string[]
}

function SnippetText({ snippet, highlights }: SnippetTextProps): React.ReactElement {
  const marked: MarkedHtml = markHitHighlight(snippet, highlights)
  return (
    <p className="hit-card-snippet">
      <span dangerouslySetInnerHTML={marked} />
    </p>
  )
}

export type FooterActionsProps = {
  onViewOriginal?: () => void
  onShare?: () => void
  onMore?: () => void
}

function FooterActions({ onViewOriginal, onShare, onMore }: FooterActionsProps): React.ReactElement {
  return (
    <div className="hit-card-footer-actions">
      <button type="button" className="hit-card-footer-btn hit-card-footer-btn-primary" onClick={onViewOriginal}>
        <ArrowUpRight size={14} weight="fill" aria-hidden="true" />
        <span>原文查看</span>
      </button>
      <button type="button" className="hit-card-footer-btn" onClick={onShare} aria-label="分享">
        <Share size={14} weight="fill" aria-hidden="true" />
        <span>分享</span>
      </button>
      <button type="button" className="hit-card-footer-btn hit-card-footer-btn-more" onClick={onMore} aria-label="更多">
        <DotsThree size={16} weight="bold" aria-hidden="true" />
      </button>
    </div>
  )
}

export function HitCard({
  type,
  title,
  score,
  snippet,
  highlights,
  updatedAt,
  sourceLabel,
  kbLabel,
  docType,
  cornerTag,
  onViewOriginal,
  onShare,
  onMore,
  className,
}: HitCardProps): React.ReactElement {
  const variant = TYPE_LEFTBAR_MAP[type]
  const cls = `hit-card galaxy-card left-bar-${variant} galaxy-card-left-accent${
    className ? ` ${className}` : ''
  }`
  return (
    <GlareCard className={cls} glareOpacity={0.09}>
      <span className="galaxy-card-accent-bar left-bar-stripe" aria-hidden="true" />
      <div className="galaxy-card-inner hit-card-inner">
        <HeaderScore title={title} score={score} cornerTag={cornerTag} />
        <MetaRow
          sourceLabel={sourceLabel}
          updatedAt={updatedAt}
          kbLabel={kbLabel}
          docType={docType}
        />
        <SnippetText snippet={snippet} highlights={highlights} />
        <FooterActions
          onViewOriginal={onViewOriginal}
          onShare={onShare}
          onMore={onMore}
        />
      </div>
    </GlareCard>
  )
}
