export type Citation = {
  field_name: string
  source_type: string
  source_url?: string | null
  source_signal_external_id?: string | null
  source_quote?: string | null
  confidence?: number | null
}

export type Enrichment = {
  iep_pct?: number | null
  iep_count_estimate?: number | null
  sped_program_notes?: string | null
  superintendent_name?: string | null
  sped_director_name?: string | null
  sped_director_title?: string | null
  sped_director_email?: string | null
  region_context?: string | null
  recent_initiatives?: string | null
  pain_points?: string | null
  nces_district_id?: string | null
  fit_score?: number | null
  fit_reasoning?: string | null
  fit_breakdown?: Record<string, number> | null
  citations: Citation[]
  generated_at?: string | null
}

export type EmailDraft = {
  id?: number
  recipient_name?: string | null
  recipient_title?: string | null
  recipient_email?: string | null
  subject: string
  body: string
  hook_summary?: string | null
  status: string
  citations: Citation[]
  generated_at?: string | null
}

export type District = {
  district_id: string
  name: string
  state?: string | null
  website?: string | null
  enrollment?: number | null
  intake_notes?: string | null
  status: string
  duplicate_of_external_id?: string | null
  non_fit_reason?: string | null
  signal_count: number
  enrichment?: Enrichment | null
  email_draft?: EmailDraft | null
}

export type Signal = {
  signal_id: string
  type: string
  date?: string | null
  match_strategy?: string | null
  match_confidence?: number | null
  match_notes?: string | null
  payload: Record<string, unknown>
}

export type PipelineResult = {
  district_id: string
  status: string
  steps: string[]
  errors: string[]
}
