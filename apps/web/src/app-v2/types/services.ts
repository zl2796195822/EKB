import type { DocumentsServices } from './documents'
import type { ConversationsServices } from './conversations'
import type { FeedbackServices } from './feedback'
import type { KnowledgeServices } from './knowledge'
import type { QaStreamServices } from './qa'
import type { SearchServices } from './search'
import type { AdminServices } from './admin'
import type { IdentityServices } from './v3Identity'
import type { ProfileServices } from './profile'
import type { TrashServices } from './trash'
import type { AnalyticsServices } from './analytics'
import type { AppsServices } from './apps'
import type { FavoritesServices } from './favorites'
import type { LLMProfileServices } from './llm'

export interface V2Services {
  readonly conversations: ConversationsServices
  readonly knowledge: KnowledgeServices
  readonly documents: DocumentsServices
  readonly search: SearchServices
  readonly qaStream: QaStreamServices
  readonly feedback: FeedbackServices
  readonly admin: AdminServices
  readonly identity: IdentityServices
  readonly profile: ProfileServices
  readonly trash: TrashServices
  readonly analytics: AnalyticsServices
  readonly apps: AppsServices
  readonly favorites: FavoritesServices
  readonly llm: LLMProfileServices
}
